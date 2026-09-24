"""The stage as a whole: what it refuses, what it measures, and what it repeats."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from decktalk.errors import Cancelled, NotBuiltError
from decktalk.events import Event, Progress, Unit
from decktalk.findings import Code, Finding, Location
from decktalk.inputs import Inputs
from decktalk.pipeline import Stage
from decktalk.results import VerifyResult
from decktalk.stages import judge
from decktalk.stages.verify import verify

from .conftest import SECTION_SECONDS, Measurements, opened, write_log

CUES = {1: {"1.1:a": 2.0}}
"""One cue, well inside its section."""


def measure(inputs: Inputs) -> VerifyResult:
    """Run the stage the way the facade would, on a run that read nothing."""
    with opened(inputs.root) as run:
        return verify(inputs, run)


# ---- what it refuses ------------------------------------------------------------------------------


def test_a_film_that_was_never_assembled_is_refused_with_the_stage_that_makes_it(
    assembled: Callable[..., Inputs],
) -> None:
    inputs = assembled(CUES)
    inputs.workspace.film.unlink()
    with pytest.raises(NotBuiltError) as refused:
        measure(inputs)
    assert "decktalk assemble" in (refused.value.hint or "")


def test_a_project_with_no_cut_list_and_no_section_files_is_refused(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    inputs.workspace.cuts_path.unlink()
    for section in inputs.document.sections:
        inputs.workspace.section_video(section.key).unlink()
    with pytest.raises(NotBuiltError):
        measure(inputs)


# ---- what it measures -----------------------------------------------------------------------------


def test_the_result_carries_the_film_it_measured_and_how_long_it_runs(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    result = measure(inputs)
    assert result.film.as_posix() == "build/final/t.mp4"
    assert result.film_seconds == pytest.approx(2 * SECTION_SECONDS)
    assert result.seconds >= 0


def test_every_section_gets_a_start_a_cut_and_a_cue_row(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    result = measure(inputs)
    assert [row.section for row in result.starts] == [1, 2]
    assert [row.section for row in result.cuts] == [1, 2]
    assert [row.cue for row in result.cues] == ["1.1:a"]


def test_a_run_that_names_one_section_measures_that_section_alone(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled({1: {"1.1:a": 2.0}, 2: {"2.1:b": 2.0}})
    with opened(inputs.root) as run:
        result = verify(inputs, run, only=[2])
    assert [row.section for row in result.starts] == [2]
    assert [row.cue for row in result.cues] == ["2.1:b"]


def test_the_stage_writes_nothing_at_all(assembled: Callable[..., Inputs]) -> None:
    """`verify` is the one read-only stage, so its result declares no written files."""
    inputs = assembled(CUES)
    assert "written" not in measure(inputs).model_dump()


# ---- what it repeats ------------------------------------------------------------------------------


def test_a_recording_logs_own_judgement_is_reported_again(assembled: Callable[..., Inputs]) -> None:
    """A page that threw is still a finding after the build that recorded it, and after a rebuild."""
    inputs = assembled(CUES)
    write_log(
        inputs,
        1,
        [
            judge(
                Code.PAGE_RENDER_THREW,
                "a slide's render threw, so the slide is not on screen.",
                Location(where="1.1", section=1),
                stage=Stage.RECORD,
            )
        ],
    )
    result = measure(inputs)
    assert Code.PAGE_RENDER_THREW in [row.code for row in result.findings]


def test_a_section_with_no_recording_log_repeats_nothing(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    assert [row.code for row in measure(inputs).findings if row.stage is Stage.RECORD] == []


# ---- the cues the film never played ----------------------------------------------------------------


def test_a_cue_that_never_resolved_is_a_certain_finding_against_the_film(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES, cues={"1": {"cues": [{"cue": "1.1:a", "on": "hello"}, {"cue": "1.1:z", "on": "nowhere"}]}})
    found = [row for row in measure(inputs).findings if row.code is Code.CUE_UNRESOLVED]
    assert len(found) == 1
    assert found[0].location.cue == "1.1:z"
    assert "nowhere" in found[0].message


def test_a_cue_the_run_resolved_is_never_reported_as_unresolved(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES, cues={"1": {"cues": [{"cue": "1.1:a", "on": "hello"}]}})
    assert [row for row in measure(inputs).findings if row.code is Code.CUE_UNRESOLVED] == []


# ---- what it says while it works ---------------------------------------------------------------------


def test_one_progress_line_is_emitted_for_every_probe(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled({1: {"1.1:a": 2.0}, 2: {"2.1:b": 2.0}})
    lines: list[Event] = []
    with opened(inputs.root) as run:
        with run.machine.events.subscribe(lines.append):
            verify(inputs, run)
    probes = [line for line in lines if isinstance(line, Progress)]
    assert [(line.done, line.total, line.unit) for line in probes] == [(1, 2, Unit.PROBE), (2, 2, Unit.PROBE)]
    assert all(line.stage is Stage.VERIFY for line in probes)


def test_a_cancelled_run_stops_inside_the_cue_loop(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    with pytest.raises(Cancelled), opened(inputs.root) as run:
        run.cancel.cancel()
        verify(inputs, run)


def test_a_film_with_no_cut_list_says_where_its_shape_came_from(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled(CUES)
    inputs.workspace.cuts_path.unlink()
    lines: list[Event] = []
    with opened(inputs.root) as run:
        with run.machine.events.subscribe(lines.append):
            verify(inputs, run)
    said = [getattr(line, "message", "") for line in lines]
    assert any("cut list" in message for message in said)


def test_every_finding_names_the_film_it_judged(assembled: Callable[..., Inputs], measured: Measurements) -> None:
    """A reader dispatching on a finding is told which file to open without knowing a path."""
    inputs = assembled(CUES)
    measured.luma = 1.0
    black = [row for row in measure(inputs).findings if row.code is Code.PAGE_BLACK]
    assert black and all(row.location.file is not None for row in black)
    assert isinstance(black[0], Finding)
