"""How many sections record at once, and the pool that records them and answers in section order."""

from __future__ import annotations

import contextvars
import threading
import time
from contextlib import ExitStack

import pytest

from decktalk.errors import Cancel, Cancelled, ToolError
from decktalk.stages.record import pool
from decktalk.stages.record.pool import Halt, Pool, at_once

# ---- how many at once ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cpus", "want"),
    [(1.0, 1), (2.0, 1), (3.5, 1), (4.0, 2), (6.0, 3), (8.0, 4), (64.0, pool.MOST_AT_ONCE)],
)
def test_an_automatic_choice_is_one_recording_per_two_cpus_and_never_more_than_four(cpus, want):
    assert at_once(0, 16, cpus=cpus, windows=False) == want


def test_windows_records_one_section_at_a_time_unless_the_machine_says_otherwise():
    assert at_once(0, 16, cpus=64.0, windows=True) == 1
    assert at_once(3, 16, cpus=64.0, windows=True) == 3


def test_a_number_the_machine_names_wins_and_no_more_workers_start_than_there_are_sections():
    assert at_once(8, 16, cpus=2.0, windows=False) == 8
    assert at_once(8, 3, cpus=64.0, windows=False) == 3
    assert at_once(0, 0, cpus=64.0, windows=False) == 1


@pytest.mark.parametrize(
    ("text", "want"),
    [("200000 100000", 2.0), ("150000 100000", 1.5), ("max 100000", None), ("-1 100000", None), ("", None)],
)
def test_a_quota_is_read_as_the_cpus_it_allows(text, want):
    assert pool._quota(text) == want


def test_a_container_quota_is_what_limits_the_choice_rather_than_the_hosts_cores(tmp_path, monkeypatch):
    """A container on a 64-core host with a 2-CPU quota ran as many recordings as the host had cores."""
    monkeypatch.setattr(pool.os, "sched_getaffinity", lambda _pid: set(range(64)), raising=False)
    cpu_max = tmp_path / "cpu.max"
    cpu_max.write_text("200000 100000\n", encoding="ascii")
    monkeypatch.setattr(pool, "CGROUP_V2_MAX", cpu_max)
    assert pool.available_cpus() == 2.0
    cpu_max.write_text("max 100000\n", encoding="ascii")
    assert pool.available_cpus() == 64.0


def test_a_cgroup_v1_quota_is_read_when_there_is_no_v2_file(tmp_path, monkeypatch):
    quota, period = tmp_path / "quota", tmp_path / "period"
    quota.write_text("300000\n", encoding="ascii")
    period.write_text("100000\n", encoding="ascii")
    monkeypatch.setattr(pool, "CGROUP_V2_MAX", tmp_path / "absent")
    monkeypatch.setattr(pool, "CGROUP_V1_QUOTA", quota)
    monkeypatch.setattr(pool, "CGROUP_V1_PERIOD", period)
    monkeypatch.setattr(pool.os, "sched_getaffinity", lambda _pid: set(range(64)), raising=False)
    assert pool.available_cpus() == 3.0


# ---- the pool ------------------------------------------------------------------------------------

BOUND: contextvars.ContextVar[str] = contextvars.ContextVar("test_bound", default="unbound")


def launcher(launched: list[str]):
    def launch(_stack: ExitStack) -> object:
        launched.append(threading.current_thread().name)
        return object()

    return launch


def test_rows_come_back_in_section_order_whatever_order_the_workers_finish_in():
    launched: list[str] = []
    finished: list[int] = []

    def one(_browser: object, job: int, _halt: Halt) -> str:
        time.sleep(0.05 * (4 - job))
        finished.append(job)
        return f"{job}:{BOUND.get()}"

    BOUND.set("the run's")
    with Pool([1, 2, 3], 3, launcher(launched), one, Cancel()) as recording:
        rows = [recording.result(job) for job in (1, 2, 3)]
    assert rows == ["1:the run's", "2:the run's", "3:the run's"], "each worker sees the caller's context"
    assert finished == [3, 2, 1]
    assert len(launched) == 3


def test_one_worker_is_one_browser_recording_one_section_after_another():
    launched: list[str] = []
    with Pool([1, 2, 3], 1, launcher(launched), lambda _b, job, _h: job, Cancel()) as recording:
        assert [recording.result(job) for job in (1, 2, 3)] == [1, 2, 3]
    assert len(launched) == 1


def test_the_first_failure_is_the_one_raised_and_it_stops_the_others():
    """Section 1 was stopped because section 2 failed, and the reason the caller needs is section 2's."""
    stopped: list[int] = []

    def one(_browser: object, job: int, halt: Halt) -> int:
        if job == 2:
            raise ToolError("section 2 would not load")
        for _ in range(100):
            try:
                halt.check()
            except Cancelled:
                stopped.append(job)
                raise
            time.sleep(0.01)
        return job

    started = time.monotonic()
    with Pool([1, 2, 3], 3, launcher([]), one, Cancel()) as recording, pytest.raises(ToolError, match="section 2"):
        recording.result(1)
    assert time.monotonic() - started < 0.9
    assert 1 in stopped


def test_a_cancelled_run_stops_every_worker():
    cancel = Cancel()

    def one(_browser: object, job: int, halt: Halt) -> int:
        cancel.cancel()
        halt.check()
        return job

    with Pool([1, 2], 2, launcher([]), one, cancel) as recording, pytest.raises(Cancelled):
        recording.result(1)


def test_a_browser_that_will_not_launch_fails_the_pool_with_its_own_reason():
    def refuse(_stack: ExitStack) -> object:
        raise ToolError("could not launch Chromium with its sandbox on")

    with Pool([1, 2], 2, refuse, lambda _b, job, _h: job, Cancel()) as recording:
        with pytest.raises(ToolError, match="sandbox"):
            recording.result(1)
