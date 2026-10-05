"""The one pool every stage fans its sections out to, and how it stops: on a failure, a cancel or a Ctrl-C.

Every test orders its workers with events and barriers and never with the wall clock, so a slow
runner makes a test slower and never makes it pass or fail by luck.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from collections.abc import Callable
from contextlib import ExitStack
from typing import NoReturn

import pytest

from decktalk.errors import Cancel, Cancelled, ToolError
from decktalk.stages.pool import CALLER_FAILED, INTERRUPTED, Halt, Pool, nothing_to_open
from support.interrupts import aimed_signals, interrupts_raise, press_ctrl_c
from support.logs import data_of

WAIT = 10
"""The longest any event here is waited for, which only a broken pool ever reaches."""

BOUND: contextvars.ContextVar[str] = contextvars.ContextVar("test_bound", default="unbound")


def launcher(launched: list[str]):
    def launch(_stack: ExitStack) -> object:
        launched.append(threading.current_thread().name)
        return object()

    return launch


class Counted:
    """A job that counts how many started and finished, and holds every one it starts until `release`.

    `in_flight` is set once `hold` jobs are running at once, which is the moment a test interrupts.
    """

    def __init__(self, hold: int) -> None:
        self.hold = hold
        self.lock = threading.Lock()
        self.started: list[int] = []
        self.finished: list[int] = []
        self.in_flight = threading.Event()
        self.release = threading.Event()

    def __call__(self, _opened: None, job: int, _halt: Halt) -> int:
        with self.lock:
            self.started.append(job)
            if len(self.started) == self.hold:
                self.in_flight.set()
        assert self.release.wait(WAIT), "the test never released the jobs in flight"
        with self.lock:
            self.finished.append(job)
        return job


def released_once_halted(pool: Pool[None, int], counted: Counted) -> threading.Thread:
    """A thread that releases the jobs in flight only once the pool has halted, so none can take another."""

    def release() -> None:
        assert pool.halt.halted.wait(WAIT), "the pool never halted"
        counted.release.set()

    thread = threading.Thread(target=release, daemon=True)
    thread.start()
    return thread


# ---- making the rows -----------------------------------------------------------------------------


def test_rows_come_back_in_section_order_whatever_order_the_workers_finish_in():
    launched: list[str] = []
    finished: list[int] = []
    done = {job: threading.Event() for job in (1, 2, 3)}

    def one(_browser: object, job: int, _halt: Halt) -> str:
        if job < 3:
            assert done[job + 1].wait(WAIT)
        finished.append(job)
        done[job].set()
        return f"{job}:{BOUND.get()}"

    BOUND.set("the run's")
    with Pool([1, 2, 3], 3, launcher(launched), one, Cancel()) as pool:
        rows = [pool.result(job) for job in (1, 2, 3)]
    assert rows == ["1:the run's", "2:the run's", "3:the run's"], "each worker sees the caller's context"
    assert finished == [3, 2, 1]
    assert len(launched) == 3


def test_one_worker_opens_one_tool_and_makes_one_section_after_another():
    launched: list[str] = []
    with Pool([1, 2, 3], 1, launcher(launched), lambda _b, job, _h: job, Cancel()) as pool:
        assert [pool.result(job) for job in (1, 2, 3)] == [1, 2, 3]
    assert len(launched) == 1


def test_no_more_workers_start_than_there_are_sections():
    with Pool([1, 2], 8, nothing_to_open, lambda _n, job, _h: job, Cancel()) as pool:
        assert len(pool.threads) == 2
        assert [pool.result(job) for job in (1, 2)] == [1, 2]


def test_a_stage_whose_sections_share_no_tool_opens_nothing():
    with Pool([1], 1, nothing_to_open, lambda opened, job, _h: (opened, job), Cancel()) as pool:
        assert pool.result(1) == (None, 1)


# ---- a failure -----------------------------------------------------------------------------------


def test_the_first_failure_is_the_one_raised_and_it_stops_the_others():
    """Section 1 was stopped because section 2 failed, and the reason the caller needs is section 2's."""
    stopped: list[int] = []
    running = threading.Event()

    def one(_browser: object, job: int, halt: Halt) -> int:
        if job == 2:
            assert running.wait(WAIT), "section 1 never started"
            raise ToolError("section 2 would not load")
        if job == 1:
            running.set()
        assert halt.halted.wait(WAIT)
        try:
            halt.check()
        except Cancelled:
            stopped.append(job)
            raise
        return job

    with Pool([1, 2, 3], 3, launcher([]), one, Cancel()) as pool, pytest.raises(ToolError, match="section 2"):
        pool.result(1)
    assert 1 in stopped, "section 1 was running when section 2 failed, and stopped at its next check"


def test_a_failure_stops_every_section_not_yet_started_and_the_pool_returns():
    """A section still queued when another failed must neither start nor hold the wait open."""
    started: list[int] = []

    def one(_n: None, job: int, _halt: Halt) -> int:
        started.append(job)
        raise ToolError(f"section {job} failed")

    with Pool(range(1, 21), 1, nothing_to_open, one, Cancel()) as pool, pytest.raises(ToolError, match="section 1"):
        pool.result(20)
    assert started == [1]


def test_a_second_section_that_failed_on_its_own_is_recorded_and_a_halted_one_is_not(caplog):
    both_running = threading.Barrier(2)

    def one(_browser: object, job: int, halt: Halt) -> int:
        if job == 1:
            both_running.wait(timeout=WAIT)
            raise ToolError("section 1 would not load")
        if job == 2:
            both_running.wait(timeout=WAIT)
            # Section 1 has failed and been recorded first by the time the pool halts.
            assert halt.halted.wait(WAIT)
            raise ToolError("section 2 would not load")
        halt.check()
        return job

    with (
        caplog.at_level("DEBUG", logger="decktalk"),
        Pool([1, 2, 3], 2, launcher([]), one, Cancel()) as pool,
        pytest.raises(ToolError, match="section 1"),
    ):
        pool.result(3)
    later = [
        record for record in caplog.records if record.name == "decktalk.stages.pool" and record.levelname == "WARNING"
    ]
    assert [data_of(record)["section"] for record in later] == [2]
    assert later[0].exc_info is not None
    assert "section 2 would not load" in str(later[0].exc_info[1])


def test_a_tool_that_will_not_open_fails_the_pool_with_its_own_reason():
    def refuse(_stack: ExitStack) -> NoReturn:
        raise ToolError("could not launch Chromium with its sandbox on")

    with Pool([1, 2], 2, refuse, lambda _b, job, _h: job, Cancel()) as pool:
        with pytest.raises(ToolError, match="sandbox"):
            pool.result(1)


# ---- a cancel ------------------------------------------------------------------------------------


def test_a_cancelled_run_stops_a_section_that_checks_the_halt():
    cancel = Cancel()

    def one(_browser: object, job: int, halt: Halt) -> int:
        cancel.cancel()
        halt.check()
        return job

    with Pool([1, 2], 2, launcher([]), one, cancel) as pool, pytest.raises(Cancelled):
        pool.result(1)


def test_a_run_cancelled_mid_run_starts_no_new_section_and_finishes_the_ones_in_flight():
    """A section in flight runs to its end, because a paid request is already sent once it starts."""
    cancel = Cancel()
    counted = Counted(hold=2)
    cancelled = threading.Thread(target=lambda: (counted.in_flight.wait(WAIT), cancel.cancel(), counted.release.set()))
    cancelled.start()
    with Pool(range(1, 11), 2, nothing_to_open, counted, cancel) as pool:
        assert [pool.result(job) for job in (1, 2)] == [1, 2]
        with pytest.raises(Cancelled, match="caller stopped"):
            pool.result(3)
    cancelled.join()
    assert sorted(counted.started) == [1, 2]
    assert sorted(counted.finished) == [1, 2]


# ---- a Ctrl-C ------------------------------------------------------------------------------------


def test_an_interrupt_starts_no_queued_section_and_lets_the_ones_in_flight_finish():
    """A paid run bought every section still queued when the pool waited for all of them after a Ctrl-C."""
    counted = Counted(hold=2)
    with pytest.raises(KeyboardInterrupt):
        with Pool(range(1, 11), 2, nothing_to_open, counted, Cancel()) as pool:
            released_once_halted(pool, counted)
            assert counted.in_flight.wait(WAIT)
            raise KeyboardInterrupt
    assert sorted(counted.started) == [1, 2], "no queued section started"
    assert sorted(counted.finished) == [1, 2], "the sections in flight finished"
    assert all(not thread.is_alive() for thread in pool.threads)


@aimed_signals
def test_ctrl_c_while_the_caller_waits_on_a_row_starts_no_queued_section_even_when_the_ones_in_flight_end_at_once():
    """The sections in flight are let go the moment the signal is sent, without waiting for the pool to halt.

    A worker that took the next section itself would start one before the caller's thread raised the
    interrupt. The signal is pending on that thread once it is sent, and the caller's thread is the
    one that hands out sections, so it raises the interrupt before it can hand out another.
    """
    counted = Counted(hold=2)

    def press() -> None:
        assert counted.in_flight.wait(WAIT)
        press_ctrl_c()
        counted.release.set()

    pressed = threading.Thread(target=press, daemon=True)
    with interrupts_raise(), pytest.raises(KeyboardInterrupt):
        with Pool(range(1, 11), 2, nothing_to_open, counted, Cancel()) as pool:
            pressed.start()
            pool.result(1)
    assert sorted(counted.started) == [1, 2]
    assert sorted(counted.finished) == [1, 2]


class Said(logging.Handler):
    """Calls `then` the first time the pool says the run is stopping, which it says between its halt and its wait."""

    def __init__(self, then: Callable[[], None]) -> None:
        super().__init__(logging.WARNING)
        self.then = then
        self.said = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == "decktalk.stages.pool" and "stopping" in record.getMessage() and not self.said.is_set():
            self.said.set()
            self.then()


@aimed_signals
@pytest.mark.parametrize(
    "where",
    [
        "while the halt settles the queued sections",
        "while the pool says it is stopping",
        "once the pool has said it is stopping",
    ],
)
def test_a_second_ctrl_c_anywhere_after_the_first_leaves_at_once(where: str, monkeypatch: pytest.MonkeyPatch):
    """The section in flight is held until the test ends, so only leaving without it can return.

    Each second Ctrl-C is pressed at a point the pool has provably reached: from inside the halt, from
    inside the warning that comes between the halt and the wait, or from another thread once that
    warning was said, so it lands in the warning's last steps or in the wait, whichever the caller's
    thread has reached.
    """
    counted = Counted(hold=1)
    pool: Pool[None, int] = Pool(range(1, 4), 1, nothing_to_open, counted, Cancel())
    said = Said(press_ctrl_c if where == "while the pool says it is stopping" else lambda: None)
    if where == "while the halt settles the queued sections":
        settle = pool.rows[3].set_exception

        def pressed(failure: BaseException | None) -> None:
            press_ctrl_c()
            settle(failure)

        monkeypatch.setattr(pool.rows[3], "set_exception", pressed)
    if where == "once the pool has said it is stopping":
        threading.Thread(target=lambda: (said.said.wait(WAIT), press_ctrl_c()), daemon=True).start()
    logging.getLogger("decktalk").addHandler(said)
    try:
        with interrupts_raise(), pytest.raises(KeyboardInterrupt):
            with pool:
                assert counted.in_flight.wait(WAIT)
                raise KeyboardInterrupt
        assert counted.started == [1]
        assert counted.finished == [], "the pool left while its section was still in flight"
        assert pool.stops == 2
        assert pool.left, "the second interrupt left the wait rather than finishing it"
        assert all(thread.daemon for thread in pool.threads), "a worker left behind cannot hold the process open"
    finally:
        logging.getLogger("decktalk").removeHandler(said)
        counted.release.set()
        for thread in pool.threads:
            thread.join(WAIT)
    assert counted.finished == [1]
    assert not any(thread.is_alive() for thread in pool.threads), "a worker whose reply came waits for no other job"


def test_a_section_that_failed_while_an_interrupt_was_waiting_on_it_is_recorded(caplog):
    """The interrupt is what the caller raises, so a paid request that failed meanwhile is not lost behind it."""
    sent = threading.Event()

    def one(_n: None, job: int, halt: Halt) -> int:
        sent.set()
        assert halt.halted.wait(WAIT)
        raise ToolError(f"section {job} broke after it was sent")

    with (
        caplog.at_level("DEBUG", logger="decktalk"),
        pytest.raises(KeyboardInterrupt),
        Pool([1, 2], 1, nothing_to_open, one, Cancel()),
    ):
        assert sent.wait(WAIT)
        raise KeyboardInterrupt
    stopping, record = [
        record for record in caplog.records if record.name == "decktalk.stages.pool" and record.levelname == "WARNING"
    ]
    assert data_of(stopping) == {"in_flight": 1, "not_started": 1}
    assert data_of(record)["section"] == 1
    assert record.exc_info is not None and "broke after it was sent" in str(record.exc_info[1])


def test_a_halt_cut_short_by_an_interrupt_is_finished_by_the_next_one(monkeypatch):
    """A row the first halt never settled would hold the wait open forever."""
    pool: Pool[None, int] = Pool([1, 2, 3], 1, nothing_to_open, lambda _n, job, _h: job, Cancel())

    def interrupted(_failure: BaseException | None) -> NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(pool.rows[2], "set_exception", interrupted)
    with pytest.raises(KeyboardInterrupt):
        pool._halt(INTERRUPTED)
    assert not pool.rows[3].done(), "the halt was cut short before it settled every row"
    monkeypatch.undo()
    pool._halt(INTERRUPTED)
    assert all(row.done() for row in pool.rows.values())


def test_a_caller_that_failed_on_its_own_still_waits_for_the_sections_in_flight_at_the_first_ctrl_c():
    counted = Counted(hold=1)
    with pytest.raises(ToolError, match="the caller broke"):
        with Pool(range(1, 4), 1, nothing_to_open, counted, Cancel()) as pool:
            released_once_halted(pool, counted)
            assert counted.in_flight.wait(WAIT)
            raise ToolError("the caller broke")
    assert pool.stops == 0, "a failure of the caller's own is not an interrupt"
    assert pool.halt.why is CALLER_FAILED
    assert counted.finished == [1]
