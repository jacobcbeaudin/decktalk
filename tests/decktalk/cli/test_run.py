"""The eight commands that move a project forward, each asserted on what it asked the library for."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.cli import run as commands
from decktalk.errors import ErrorCode
from decktalk.events import RunStart
from decktalk.findings import Code
from decktalk.pipeline import Stage
from decktalk.results import (
    AssembleResult,
    ClipResult,
    CueResult,
    NarrateResult,
    RecordResult,
    SoundscapeResult,
    VerifyResult,
    Voicing,
)

from .conftest import spend

NARRATE = NarrateResult(ok=True, run="r", voice=Voicing.PLACEHOLDER, sections=(), spend=spend(), seconds=1.0)
CUE = CueResult(ok=True, run="r", sections=(), seconds=1.0)
RECORD = RecordResult(ok=True, run="r", sections=(), seconds=1.0)
SOUNDSCAPE = SoundscapeResult(ok=True, run="r", items=(), spend=spend(), seconds=1.0)
ASSEMBLE = AssembleResult(ok=True, run="r", film="build/final/demo.mp4", film_seconds=64.0, sections=(), seconds=1.0)
VERIFY = VerifyResult(ok=True, run="r", film="build/final/demo.mp4", film_seconds=64.0, seconds=1.0)


def test_narrate_with_no_voice_never_buys(run, project) -> None:
    made = project(narrate=NARRATE)
    assert run("narrate", "--no-voice").exit_code == 0
    assert made.called("narrate")["voice"] is Voicing.PLACEHOLDER


def test_narrate_with_spend_buys_without_asking(run, project) -> None:
    made = project(narrate=NARRATE)
    run("narrate", "--spend", "--max-cost", "5")
    asked = made.called("narrate")
    assert asked["voice"] is Voicing.PAID
    assert asked["max_cost"] == 5


def test_narrate_keeps_every_paid_take_unless_the_flag_says_otherwise(run, project) -> None:
    made = project(narrate=NARRATE)
    run("narrate", "--no-voice")
    assert made.called("narrate")["replace_voiced"] is False
    run("narrate", "--no-voice", "--replace-voiced")
    assert made.calls[-1][2]["replace_voiced"] is True


def test_cue_reads_the_allowed_codes_rather_than_a_flag_of_its_own(run, project) -> None:
    made = project(cue=CUE)
    run("cue", "--allow", Code.CUE_UNKNOWN.value)
    assert made.called("cue")["allow_unknown"] is True


def test_record_passes_the_section_selection_through(run, project) -> None:
    made = project(record=RECORD)
    run("record", "--section", "1,3-4")
    assert made.called("record")["only"] == (1, 3, 4)


def test_soundscape_takes_the_spending_flags(run, project) -> None:
    made = project(soundscape=SOUNDSCAPE)
    run("soundscape", "--no-voice")
    assert made.called("soundscape")["voice"] is Voicing.PLACEHOLDER


def test_assemble_reads_skip_as_the_one_stage_it_can_leave_out(run, project) -> None:
    made = project(assemble=ASSEMBLE)
    run("assemble", "--skip", "soundscape")
    assert made.called("assemble")["soundscape"] is False


def test_assemble_refuses_a_skip_that_names_a_stage_it_does_not_run(run, project) -> None:
    project(assemble=ASSEMBLE)
    ran = run("assemble", "--skip", "record")
    assert ran.exit_code == 2
    assert "soundscape alone" in ran.err


def test_verify_measures_the_film(run, project) -> None:
    made = project(verify=VERIFY)
    ran = run("verify")
    assert made.called("verify")
    assert "build/final/demo.mp4" in ran.out


def test_build_runs_the_span_two_flags_name(run, project, answers) -> None:
    made = project(build=answers["build"])
    run("build", "--no-voice", "--from", "record", "--to", "assemble")
    asked = made.called("build")
    assert asked["stages"] == (Stage.RECORD, Stage.SOUNDSCAPE, Stage.ASSEMBLE)


def test_build_with_neither_end_runs_the_whole_pipeline(run, project, answers) -> None:
    made = project(build=answers["build"])
    run("build", "--no-voice")
    assert made.called("build")["stages"] is None


def test_build_names_its_run_and_its_events_file_on_the_first_line_of_stderr(run, project, answers) -> None:
    made = project(build=answers["build"])
    made.emits["build"] = (RunStart, {"events_path": Path("build/events/r.jsonl")})
    ran = run("build", "--no-voice")
    assert ran.err.startswith("run r, events build/events/r.jsonl")


def test_events_writes_one_json_line_per_moment_on_stderr(run, project, answers) -> None:
    made = project(build=answers["build"])
    made.emits["build"] = (RunStart, {"events_path": Path("build/events/r.jsonl")})
    ran = run("build", "--no-voice", "--events")
    assert '"event":"run.start"' in ran.err
    assert ran.out.strip().startswith("Built")


def test_build_without_a_terminal_and_without_a_flag_refuses_the_spend(run, project, answers) -> None:
    project(build=answers["build"], check=answers["check"])
    ran = run("build")
    assert ran.exit_code == 2
    assert "error[APPROVAL]" in ran.err
    assert "--spend" in ran.err


def test_the_approval_refusal_is_one_object_under_json(run, project, answers) -> None:
    project(build=answers["build"], check=answers["check"])
    ran = run("build", "--json")
    assert ran.exit_code == 2
    written = json.loads(ran.out)
    assert written["error"]["code"] == ErrorCode.APPROVAL.value
    assert written["ok"] is False


def test_clip_needs_exactly_one_section(run, project) -> None:
    project(
        clip=ClipResult(
            ok=True,
            run="r",
            section=1,
            film="clip-1.mp4",
            words="clip-1.words.json",
            start=0.0,
            end=2.0,
            seconds=2.0,
            hold_seconds=0.0,
            gain_db=0.0,
            estimated=True,
        )
    )
    assert run("clip", "--section", "1", "--start", "0", "--end", "2").exit_code == 0
    assert run("clip", "--section", "1,2").exit_code == 2


@pytest.mark.parametrize("name", ["narrate", "cue", "record", "soundscape", "assemble", "verify", "build", "clip"])
def test_every_stage_command_answers_with_its_own_result(run, name: str) -> None:
    assert run(name, "--help").exit_code == 0


def test_every_moving_command_is_registered() -> None:
    assert all(
        hasattr(commands, name)
        for name in ("narrate", "cue", "record", "soundscape", "assemble", "verify", "build", "clip")
    )
