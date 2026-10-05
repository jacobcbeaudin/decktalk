"""The one pool `narrate` and `record` fan their sections out to, and the three ways it stops.

A stage hands the pool the section numbers it must make, the function that makes one, and the tool a
worker opens once and keeps for every section it makes after, and then waits for each section's row
in the order it reads them. Rows come back in that order whatever order the workers finish in.

The pool stops starting sections when a section fails, when the run's cancel token is set, or when
the caller's own thread is interrupted. A section that has not started never starts once the pool
has halted. A section already started runs to its end unless it checks the halt itself, which a
recording does while it waits on its page and a take never does, because a take's request is paid for
once it is sent and its answer is worth keeping.

An interrupt reaches the caller's thread and never a worker's, so the caller's thread is where the
pool answers it, and it is also the only thread that hands a worker its next section. A signal that
has reached the process is raised on the caller's thread at its next step, before it can hand out
another section, so no section starts after a Ctrl-C however its takes in flight finish. The
caller's thread never sleeps longer than `WAKE_SECONDS` in one wait, because a signal does not
always wake a thread that is waiting on a lock, and it is not answered until that thread runs again.

The first interrupt halts the pool and waits for the sections in flight, so a take already paid for
is written. A second interrupt, whether it lands while the pool halts, while it says it is stopping
or while it waits, leaves at once without them, and every worker is a daemon thread so the process
can end while one is still waiting on a reply.
"""

from __future__ import annotations

import contextvars
import logging
import queue
import threading
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import ExitStack
from dataclasses import dataclass

from decktalk.errors import Cancel, Cancelled

log = logging.getLogger(__name__)

WAKE_SECONDS = 0.05
"""Calibration: the longest the caller's thread sleeps in one wait, so a Ctrl-C that did not wake it is seen."""


@dataclass(frozen=True)
class Why:
    """Why a pool halted, as the sentence a section stopped by the halt raises and the advice beside it."""

    said: str
    hint: str


FAILED = Why("another section failed", "Fix what the other section said.")
"""A section failed, so the sections that have not started would be made for a stage that fails anyway."""

INTERRUPTED = Why("the run was interrupted", "Run the command again, and every section already made is kept.")
"""The caller's thread was interrupted, so the sections that have not started are not wanted."""

CALLER_FAILED = Why("the run failed", "Fix what the run said, and every section already made is kept.")
"""The caller's thread failed on its own, so the sections that have not started would be made for nothing."""


class Halt:
    """Whether a worker should stop: the caller cancelled the run, or the pool halted for a reason of its own.

    The first reason the pool halts for is the one every stopped section reports.
    """

    def __init__(self, cancel: Cancel) -> None:
        self.cancel = cancel
        self.halted = threading.Event()
        self.why = FAILED
        self._lock = threading.Lock()

    def stop(self, why: Why) -> None:
        """Halt the pool, keeping the first reason it was halted for."""
        with self._lock:
            if not self.halted.is_set():
                self.why = why
                self.halted.set()

    def refusal(self) -> Cancelled:
        """What a section the halt stopped says, which names why the pool halted."""
        return Cancelled(f"{self.why.said}, so this section stopped.", hint=self.why.hint)

    def check(self) -> None:
        """Raise `Cancelled` when the run was cancelled or the pool has halted."""
        self.cancel.check()
        if self.halted.is_set():
            raise self.refusal()


def nothing_to_open(_stack: ExitStack) -> None:
    """The tool a worker opens when a stage's sections need none of their own, which is nothing."""
    return


class Pool[W, R]:
    """`jobs` made on at most `workers` threads, with each job's row waited for in the order the caller asks.

    The caller's thread hands each worker its next job while it waits for a row, and only while no
    halt is set. A worker opens its tool with `opening` the first time it needs one and keeps it for
    every job it is handed after, so one worker is exactly one recorder that ran one section
    after another. A worker runs in a copy of the caller's context, because the toolchain, the fetch
    listener and the run's place on the stream are bound there and a new thread inherits none of them.

    The first failure halts every other worker at its next check, and it is the failure the caller
    is given, whichever section the caller was waiting on, because a section stopped by the halt has
    nothing to say about why. A failure the caller is not given is logged with its traceback, because
    a paid request that failed may still have been charged.
    """

    def __init__(
        self,
        jobs: Sequence[int],
        workers: int,
        opening: Callable[[ExitStack], W],
        one: Callable[[W, int, Halt], R],
        cancel: Cancel,
    ) -> None:
        self.halt = Halt(cancel)
        self.rows: dict[int, Future[R]] = {job: Future() for job in jobs}
        self.queue = deque(jobs)
        """The jobs not yet handed to a worker, which only the caller's thread hands out."""
        self.handed: queue.SimpleQueue[int | None] = queue.SimpleQueue()
        """The jobs handed to the workers, each taken by the first worker free, then one `None` per worker to end."""
        self.out: set[int] = set()
        """Every job the caller's thread handed out, so it hands out no more than there are workers to make them."""
        self.taken: set[int] = set()
        """The jobs a worker took from `handed`, whose rows only that worker settles."""
        self.lock = threading.Lock()
        self.changed = threading.Condition()
        """Notified each time a worker settles a row, which is when the caller's thread may hand out the next job."""
        self.first: tuple[int, BaseException] | None = None
        self.opening = opening
        self.one = one
        self.interrupts: list[BaseException] = []
        """Every interrupt that reached the caller's thread while it held the pool, each counted once."""
        self.drained = False
        self.left = False
        self.closed = False
        """Whether every worker has been handed its `None`, which happens once."""
        self.threads = [
            threading.Thread(target=contextvars.copy_context().run, args=(self._work,), daemon=True)
            for _ in range(min(workers, len(self.rows)))
        ]

    def __enter__(self) -> Pool[W, R]:
        """Start every worker, and stop them again if the caller is interrupted while they start."""
        try:
            for thread in self.threads:
                thread.start()
            self._hand_out()
        except BaseException as stopped:
            # Starting a thread waits for it, so a Ctrl-C can land here with a worker already making a section.
            self._drain(stopped)
            raise
        return self

    def __exit__(self, _kind: object, failure: BaseException | None, _trace: object) -> None:
        """Wait for every worker, halting first when the caller's thread is leaving on a failure or an interrupt."""
        if self.drained or self.left:
            return
        self._drain(failure)

    def _hand_out(self) -> None:
        """Hand the next queued jobs to the workers that are free, or settle every queued one once the pool has halted.

        Only the caller's thread runs this, so an interrupt that reached the process is raised on that
        thread before it hands out another job.
        """
        if self.halt.halted.is_set():
            self._halt(self.halt.why)
            return
        while self.queue and sum(not self.rows[job].done() for job in self.out) < len(self.threads):
            job = self.queue.popleft()
            self.out.add(job)
            self.handed.put(job)

    def _take(self) -> int | None:
        """The next job handed to this worker, or None once the pool is ending.

        A job the halt already settled is passed over, so it never starts and its row is settled once.
        """
        while (job := self.handed.get()) is not None:
            with self.lock:
                if not self.rows[job].done():
                    self.taken.add(job)
                    return job
        return None

    def _work(self) -> None:
        with ExitStack() as stack:
            opened: list[W] = []
            while (job := self._take()) is not None:
                row = self.rows[job]
                try:
                    self.halt.check()
                    if not opened:
                        opened.append(self.opening(stack))
                    row.set_result(self.one(opened[0], job, self.halt))
                except BaseException as exc:  # noqa: BLE001  (handed to the caller, who raises it on its own thread)
                    with self.lock:
                        later = self.first is not None
                        if not later:
                            self.first = (job, exc)
                    if later and not isinstance(exc, Cancelled):
                        # Only the first failure is raised, and a section stopped by the halt has nothing to
                        # say, but a second section that failed on its own is recorded rather than lost.
                        _unraised(job, exc, "also failed while the first failure was being raised")
                    self.halt.stop(FAILED)
                    row.set_exception(exc)
                with self.changed:
                    self.changed.notify_all()

    def _until(self, done: Callable[[], bool]) -> None:
        """Hand out jobs as workers come free until `done`, waking at least every `WAKE_SECONDS` to see an interrupt."""
        with self.changed:
            while True:
                self._hand_out()
                if done():
                    return
                self.changed.wait(WAKE_SECONDS)

    def result(self, job: int) -> R:
        """The row of one job, once it is made, or the first failure of the whole pool.

        An interrupt that reaches the caller while it waits here halts the pool and waits for the
        sections in flight before it is raised again, and a second one leaves without them.
        """
        row = self.rows[job]
        try:
            self._until(row.done)
        except BaseException as stopped:
            self._drain(stopped)
            raise
        failure = row.exception()
        if failure is None:
            return row.result()
        self._halt(FAILED)
        self._drain()
        raise (self.first[1] if self.first is not None else failure) from None

    def _halt(self, why: Why) -> None:
        """Halt the pool and settle every section no worker took as stopped, so none is needed to start it.

        The rows to settle are worked out afresh on every call, so a halt cut short by an interrupt is
        finished by the next one. They are settled under the lock a worker takes a job under, so a row
        a worker took is never settled here and a row settled here is never taken.
        """
        self.halt.stop(why)
        with self.lock:
            self.queue.clear()
            for job, row in self.rows.items():
                if job not in self.taken and not row.done():
                    row.set_exception(self.halt.refusal())

    @property
    def stops(self) -> int:
        """How many interrupts reached the caller's thread while it held the pool."""
        return len(self.interrupts)

    def _count(self, stop: BaseException) -> None:
        """Count an interrupt once, however often the stopping path meets it, as an exception equals only itself."""
        if stop not in self.interrupts:
            self.interrupts.append(stop)

    def _stop(self, stop: BaseException) -> None:
        """Count `stop` when it is an interrupt, halt for it, and say how many sections in flight finish first."""
        if isinstance(stop, KeyboardInterrupt):
            self._count(stop)
        not_started = len(self.queue)
        self._halt(INTERRUPTED if stop in self.interrupts else CALLER_FAILED)
        in_flight = sum(not row.done() for row in self.rows.values())
        log.warning(
            "The run is stopping: no section that has not started will start, and the %d in flight finish "
            "first. Interrupt to leave without them.",
            in_flight,
            extra={"data": {"in_flight": in_flight, "not_started": not_started}},
        )

    def _first_unraised(self) -> None:
        """Record the pool's first failure when the caller raises its own instead, since it may have been charged."""
        if self.first is not None and not isinstance(self.first[1], Cancelled):
            _unraised(*self.first, "failed while the run was being stopped")

    def _close(self) -> None:
        """Hand each worker the `None` that ends it once its last job is done, so no worker waits for a job forever."""
        if not self.closed:
            self.closed = True
            for _ in self.threads:
                self.handed.put(None)

    def _drain(self, failure: BaseException | None = None) -> None:
        """Wait for every section in flight and every worker, halting at the first interrupt and leaving at the second.

        `failure` is what the caller's thread is leaving on, if anything. Stopping for it or for an
        interrupt (counting it, halting, saying so) happens inside the same guarded region as the
        wait, so an interrupt that lands anywhere in that work counts as one more and is never
        missed, and the second leaves at once with `left` set. Only an interrupt counts toward the
        second, so a run that failed on its own still waits for its sections in flight at the first
        Ctrl-C.

        Every row is settled once the sections in flight finish, because a halt settles the queued
        ones itself, so the wait is on the rows and does not depend on which workers managed to start.
        """
        stopping = failure
        interrupted: BaseException | None = None
        while True:
            try:
                if stopping is not None:
                    self._stop(stopping)
                    stopping = None
                self._until(lambda: all(row.done() for row in self.rows.values()))
                self._close()
                for thread in self.threads:
                    while thread.is_alive():
                        thread.join(WAKE_SECONDS)
                break
            except BaseException as exc:
                if isinstance(stopping, KeyboardInterrupt):
                    self._count(stopping)
                self._count(exc)
                if self.stops > 1:
                    # The workers are daemon threads, so the process may end while one still waits on a reply,
                    # and a worker whose reply does come ends after it rather than waiting for another job.
                    self.left = True
                    self._close()
                    raise
                stopping = interrupted = exc
        self.drained = True
        if failure is not None or interrupted is not None:
            self._first_unraised()
        if interrupted is not None:
            raise interrupted


def _unraised(job: int, failure: BaseException, said: str) -> None:
    """Record a section's failure that the caller is not given, with its traceback."""
    log.warning("Section %d %s.", job, said, exc_info=failure, extra={"data": {"section": job}})


__all__ = ["CALLER_FAILED", "FAILED", "INTERRUPTED", "Halt", "Pool", "Why", "nothing_to_open"]
