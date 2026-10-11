"""How many recorders work at once, chosen from the CPU this process may really use, and the chunks they record."""

from __future__ import annotations

import itertools
import math
from dataclasses import replace

import pytest

from decktalk.settings import RecordConfig
from decktalk.stages.record import pool
from decktalk.stages.record.pool import Chunk, Span, at_once, chunk_count, chunked
from support.logs import data_of

# ---- how many at once ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cpus", "want"),
    [(1.0, 1), (2.0, 1), (5.5, 1), (6.0, 2), (9.0, 3), (12.0, 4), (64.0, pool.MOST_AT_ONCE)],
)
def test_an_automatic_choice_is_one_recorder_per_three_cpus_and_never_more_than_four(cpus, want):
    """A recording with the capture switches keeps between two and three cores busy, so it is given three."""
    assert at_once(0, cpus=cpus) == want


def test_a_number_the_machine_names_wins_over_its_cpus():
    assert at_once(8, cpus=2.0) == 8


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
    monkeypatch.setattr(pool, "available_cpus", lambda: 9.0)
    with caplog.at_level("DEBUG", logger="decktalk"):
        assert pool.automatic(0) == 3
    [record] = [record for record in caplog.records if record.name == "decktalk.stages.record.pool"]
    assert data_of(record) == {"workers": 3, "cpus": 9.0, "requested": 0}


def test_windows_chooses_its_recorders_as_every_other_machine_does(monkeypatch):
    """The recorder steps the clock, so a loaded machine records the same frames and Windows needs no rule."""
    monkeypatch.setattr(pool, "available_cpus", lambda: 9.0)
    monkeypatch.setattr("sys.platform", "win32")
    assert pool.automatic(0) == 3


# ---- how a section is split into chunks ----------------------------------------------------------

SPLITTING = RecordConfig(chunk_startup_seconds=1.0, step_seconds=0.03, chunk_frames_min=50)
"""A machine whose page loads in a second and whose step costs 30 ms, with chunks of at least 50 frames."""


def moving(section: int, frames: int, *, splits: bool = True) -> Span:
    """A section that steps on every frame, which is what a section never recorded is expected to do."""
    return Span(section, frames, frames, splits=splits)


@pytest.mark.parametrize(
    ("frames", "workers", "want"),
    [
        (2860, 4, 4),  # sqrt(0.03 * 2860 * 4 / 1) is 18.5, and no more chunks than workers
        (2860, 1, 1),  # one worker records the section in one pass
        (60, 4, 1),  # too short for two chunks of 50 frames, so splitting it buys nothing
        (100, 4, 2),  # two chunks of 50 is all 100 frames allow
        (0, 4, 1),
    ],
)
def test_a_section_is_split_into_as_many_chunks_as_its_frames_and_the_workers_allow(frames, workers, want):
    assert chunk_count(moving(1, frames), workers, SPLITTING) == want


def test_a_slow_start_up_splits_a_section_into_fewer_chunks_than_workers():
    """K = sqrt(c W N / s): a chunk costs its start-up again, so a dear start-up is paid fewer times."""
    slow = replace(SPLITTING, chunk_startup_seconds=10.0)
    assert chunk_count(moving(1, 300), 4, slow) == round(math.sqrt(0.03 * 300 * 4 / 10.0)) == 2


def test_a_section_that_mostly_holds_still_is_split_by_its_steps_and_not_its_frames():
    """The lesson steps 260 times for 2,860 frames, so splitting it four ways pays for start-ups it never needed."""
    still = Span(1, 2860, 26, splits=True)
    assert chunk_count(still, 4, SPLITTING) == round(math.sqrt(0.03 * 26 * 4 / 1.0)) == 2
    assert chunk_count(replace(still, steps=8), 4, SPLITTING) == 1


def test_the_chunks_of_a_section_cover_its_frames_once_in_order_and_nearly_evenly():
    chunks = [chunk for chunk in chunked([moving(1, 2861)], 4, SPLITTING) if chunk.section == 1]
    ordered = sorted(chunks, key=lambda chunk: chunk.first)
    assert ordered[0].first == 0 and ordered[-1].stop == 2861
    assert all(one.stop == other.first for one, other in itertools.pairwise(ordered))
    assert {len(chunk.frames) for chunk in chunks} <= {715, 716}


def test_a_section_drawn_by_a_command_is_one_chunk_however_long_it_is():
    assert chunked([moving(3, 5000, splits=False)], 4, SPLITTING) == [Chunk(3, 0, 5000, 5000.0)]


def test_chunks_are_handed_out_dearest_first_across_every_section():
    """Longest processing time first, so no long chunk is left to run alone at the end."""
    spans = [moving(1, 120), moving(2, 2000), moving(3, 40), moving(4, 900, splits=False), Span(5, 3000, 300, True)]
    order = chunked(spans, 4, SPLITTING)
    assert [chunk.steps for chunk in order] == sorted((chunk.steps for chunk in order), reverse=True)
    assert order[0] == Chunk(4, 0, 900, 900.0)
    assert [chunk.first for chunk in order if chunk.section == 2] == [0, 500, 1000, 1500]
    assert order[-1] == Chunk(3, 0, 40, 40.0)
    assert [chunk.steps for chunk in order if chunk.section == 5] == [75.0] * 4, "a still section is cheap a frame"
