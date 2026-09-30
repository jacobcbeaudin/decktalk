"""How many page sections record at once, and the workers that record them.

A recording waits for its section's whole span in real time, so a film's recording took as long as
the film, and the sections of one film share nothing but the project they read. Several of them are
therefore recorded at once, each by a worker with a Chromium of its own, and the rows come back in
section order whatever order the workers finish in.

A recording is also the one stage that needs its CPU on time. A reveal that lands a frame late is a
finding, so the number at once is chosen from the CPU this process may really use, which inside a
container is its quota and not the host's core count. `[record] concurrency` names the number, and
zero chooses it here.
"""

from __future__ import annotations

import contextvars
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import ExitStack
from pathlib import Path

from playwright.sync_api import Browser

from decktalk.errors import Cancel, Cancelled

log = logging.getLogger(__name__)

CPUS_PER_RECORDING = 2
"""Calibration: the CPUs one recording keeps busy while it presents its frames on time.

The page, the compositor and the encoder of one Chromium kept about two cores of a Linux container
busy, and three recordings at once in four CPUs still landed every reveal inside the offset limit.
"""

MOST_AT_ONCE = 4
"""Calibration: the most sections an automatic choice records at once, because each one is a Chromium in memory.

Four recordings held about 900 MB beside each other, which is what a small render worker can spare,
and a machine with more to give says so through `[record] concurrency`.
"""

WINDOWS_AT_ONCE = 1
"""Calibration: Windows records one section at a time until its capture is measured under load."""

CGROUP_V2_MAX = Path("/sys/fs/cgroup/cpu.max")
"""Truth: where a cgroup v2 container states its CPU quota and period, or `max` for none."""

CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
"""Truth: where a cgroup v1 container states its CPU quota, which is -1 for none, and its period."""


def _quota(text: str) -> float | None:
    """CPUs from one `quota period` pair, or None when the pair states no limit."""
    parts = text.split()
    if len(parts) != 2 or parts[0] in ("max", "-1"):
        return None
    try:
        quota, period = float(parts[0]), float(parts[1])
    except ValueError:
        # silent: a quota that is not two numbers states no limit.
        return None
    return quota / period if quota > 0 and period > 0 else None


def _read(path: Path) -> str | None:
    """The text of one kernel file, or None where this machine has no such file."""
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        # silent: a machine without the kernel file has no quota.
        return None


def available_cpus() -> float:
    """The CPUs this process may use: the fewer of its container's quota and the cores it may run on.

    A container that names no quota, and every machine that is not a Linux container, is limited by
    its cores alone.
    """
    affinity = getattr(os, "sched_getaffinity", None)
    cores = float(len(affinity(0))) if affinity is not None else float(os.cpu_count() or 1)
    v2 = _read(CGROUP_V2_MAX)
    v1 = _read(CGROUP_V1_QUOTA)
    period = _read(CGROUP_V1_PERIOD)
    stated = _quota(v2) if v2 is not None else _quota(f"{v1} {period}") if v1 and period else None
    return min(cores, stated) if stated is not None else cores


def at_once(requested: int, jobs: int, *, cpus: float, windows: bool) -> int:
    """How many sections to record at once: what the machine asked for, or one per two CPUs, never more than the jobs.

    `requested` is `[record] concurrency`, and zero asks this function to choose.
    """
    if requested:
        chosen = requested
    elif windows:
        chosen = WINDOWS_AT_ONCE
    else:
        chosen = min(MOST_AT_ONCE, int(cpus // CPUS_PER_RECORDING))
    return max(1, min(chosen, jobs))


def automatic(requested: int, jobs: int) -> int:
    """`at_once` for this machine, which reads its own CPUs and platform, and records what it chose.

    How many recorders ran is the first thing an operator asks about a slow container, so the choice
    is recorded beside the CPUs it was made from.
    """
    cpus = available_cpus()
    chosen = at_once(requested, jobs, cpus=cpus, windows=sys.platform == "win32")
    log.debug(
        "%d sections are recorded on %d workers.",
        jobs,
        chosen,
        extra={"data": {"workers": chosen, "jobs": jobs, "cpus": cpus, "requested": requested}},
    )
    return chosen


class Halt:
    """Whether a worker should stop: the caller cancelled the run, or another section already failed.

    A section that failed fails the stage, so the recordings still running are stopped rather than
    finished for a film that will not be assembled from them.
    """

    def __init__(self, cancel: Cancel) -> None:
        self.cancel = cancel
        self.halted = threading.Event()

    def check(self) -> None:
        """Raise `Cancelled` when the run was cancelled or another section failed."""
        self.cancel.check()
        if self.halted.is_set():
            raise Cancelled("another section failed, so this one stopped", hint="Fix what the other section said.")


class Pool[R]:
    """`jobs` recorded on `workers` threads, with each job's row waited for in the order the caller asks.

    Each worker takes the next job, opens its own Chromium the first time it needs one, and keeps it
    for every job it takes after, so one worker is exactly the recorder that ran one section after
    another. A worker runs in a copy of the caller's context, because the toolchain and the fetch
    listener are bound there and a new thread does not inherit them.

    The first failure halts every other worker at its next check, and it is the failure the caller
    is given, whichever section the caller was waiting on, because a section stopped by the halt
    has nothing to say about why.
    """

    def __init__(
        self,
        jobs: Sequence[int],
        workers: int,
        opening: Callable[[ExitStack], Browser],
        one: Callable[[Browser, int, Halt], R],
        cancel: Cancel,
    ) -> None:
        self.halt = Halt(cancel)
        self.rows: dict[int, Future[R]] = {job: Future() for job in jobs}
        self.queue = list(jobs)
        self.lock = threading.Lock()
        self.first: BaseException | None = None
        self.opening = opening
        self.one = one
        self.threads = [
            threading.Thread(target=contextvars.copy_context().run, args=(self._work,)) for _ in range(workers)
        ]

    def __enter__(self) -> Pool[R]:
        for thread in self.threads:
            thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop what is still running when the caller leaves early, and wait for every worker either way."""
        if any(not row.done() for row in self.rows.values()):
            self.halt.halted.set()
        for thread in self.threads:
            thread.join()

    def _take(self) -> int | None:
        with self.lock:
            return self.queue.pop(0) if self.queue else None

    def _work(self) -> None:
        with ExitStack() as stack:
            opened: Browser | None = None
            while (job := self._take()) is not None:
                row = self.rows[job]
                try:
                    self.halt.check()
                    opened = opened or self.opening(stack)
                    row.set_result(self.one(opened, job, self.halt))
                except BaseException as exc:  # noqa: BLE001  (handed to the caller, who raises it on its own thread)
                    with self.lock:
                        later = self.first is not None
                        self.first = self.first or exc
                    if later and not isinstance(exc, Cancelled):
                        # Only the first failure is raised, and a section stopped by the halt has nothing to
                        # say, but a second section that failed on its own is recorded rather than lost.
                        log.warning(
                            "Section %d also failed while the first failure was being raised.",
                            job,
                            exc_info=exc,
                            extra={"data": {"section": job}},
                        )
                    self.halt.halted.set()
                    row.set_exception(exc)

    def result(self, job: int) -> R:
        """The row of one job, once it is recorded, or the first failure of the whole pool."""
        try:
            return self.rows[job].result()
        except BaseException as exc:  # noqa: BLE001  (re-raised below, as the pool's first failure)
            self.halt.halted.set()
            for thread in self.threads:
                thread.join()
            raise (self.first or exc) from None


__all__ = ["Halt", "Pool", "at_once", "automatic", "available_cpus"]
