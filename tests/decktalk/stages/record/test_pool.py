"""How many sections record at once, chosen from the CPU this process may really use."""

from __future__ import annotations

import pytest

from decktalk.stages.record import pool
from decktalk.stages.record.pool import at_once
from support.logs import data_of

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


def test_the_number_of_recorders_chosen_is_recorded_beside_the_cpus_it_was_chosen_from(monkeypatch, caplog):
    """How many recorders ran is the first question about a slow container."""
    monkeypatch.setattr(pool, "available_cpus", lambda: 6.0)
    monkeypatch.setattr(pool.sys, "platform", "linux")
    with caplog.at_level("DEBUG", logger="decktalk"):
        assert pool.automatic(0, 5) == 3
    [record] = [record for record in caplog.records if record.name == "decktalk.stages.record.pool"]
    assert data_of(record) == {"workers": 3, "jobs": 5, "cpus": 6.0, "requested": 0}
