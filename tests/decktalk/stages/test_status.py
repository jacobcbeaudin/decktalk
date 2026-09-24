"""The status report: what is on disk, what has gone stale, which runs are open, and what to do next."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes
from decktalk.errors import Cancel
from decktalk.events import Level, Log
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.page import Q
from decktalk.pipeline import Artifact, Stage
from decktalk.results import SectionKind, StatusResult
from decktalk.stages import status as stage
from decktalk.stages.status import BUILT, next_command, source_of, status

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
clip = "media/b-roll.mp4"
"""
"""One page section and one clip section, which is the smallest project with both kinds in it."""

SCRIPT = "# Demo\n\n## 1. One\n\nHello there again.\n"


pytestmark = pytest.mark.usefixtures("fake_ffmpeg")
"""Every case here drives a stage that reaches for ffmpeg, so the encoder is faked at its own seam."""


def a_run(root: Path) -> Run:
    """One run, opened straight on a machine, because nothing here needs an events subscriber."""
    machine = Machine(environ={}, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    return Run(machine, id="r1", cancel=Cancel(), root=root)


def a_project(tmp_path: Path, *, script: str | None = SCRIPT, toml: str = TOML) -> Inputs:
    """A project whose page and clip are both on disk, so nothing is missing until a case removes it."""
    (tmp_path / "deck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deck" / "index.html").write_text("<div data-scene='1'></div>", encoding="utf-8")
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)
    (tmp_path / "media" / "b-roll.mp4").write_bytes(b"")
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    if script is not None:
        (tmp_path / "script.md").write_text(script, encoding="utf-8")
    return Inputs.load(tmp_path, environ={})


def a_take(inputs: Inputs, *, spoken: str = "Hello there again.", voiced: bool = True) -> Take:
    """One take for section one, written to the index with its audio file beside it."""
    take = Take(
        section=1,
        key="01",
        chapter="One",
        hash="h",
        voiced=voiced,
        word_count=3,
        characters=18,
        estimated_seconds=2.0,
        duration_seconds=2.0,
        spoken=spoken,
    )
    Takes(script="script.md", model="m", output_format="mp3_44100_128", sections=(take,)).write(
        inputs.workspace.takes_path
    )
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes_dir / take.file).write_bytes(b"")
    return take


def notes(run: Run) -> list[str]:
    """Every sentence this run put on the stream, which is where a reading that is not a code goes."""
    said: list[str] = []
    run.machine.events.subscribe(lambda event: said.append(event.message) if isinstance(event, Log) else None)
    return said


# ---- what to do next ---------------------------------------------------------------------------


def test_every_artifact_the_pipeline_declares_says_when_it_is_built() -> None:
    """A stage added to the pipeline reaches this report, so a new artifact may not be left out."""
    assert set(BUILT) == set(Artifact)


def test_a_project_with_nothing_built_is_told_to_rehearse_the_voice(tmp_path: Path) -> None:
    """The first move a project is told to make never costs credits."""
    assert next_command(a_project(tmp_path)) == "decktalk narrate --no-voice"


def test_a_project_with_takes_is_told_to_resolve_its_cues(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    a_take(inputs)
    assert next_command(inputs) == f"decktalk {Stage.CUE.value}"


def test_a_project_with_cue_times_is_told_to_record(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    a_take(inputs)
    inputs.workspace.cue_times_path.write_text('{"sections": []}', encoding="utf-8")
    assert next_command(inputs) == f"decktalk {Stage.RECORD.value}"


def test_a_project_that_describes_no_soundscape_is_never_told_to_generate_one(tmp_path: Path) -> None:
    """A stage with nothing to do is not the next thing to do."""
    inputs = a_project(tmp_path)
    a_take(inputs)
    inputs.workspace.cue_times_path.write_text('{"sections": []}', encoding="utf-8")
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    assert next_command(inputs) == f"decktalk {Stage.ASSEMBLE.value}"


def test_a_project_with_everything_built_is_told_to_measure_it(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    a_take(inputs)
    inputs.workspace.cue_times_path.write_text('{"sections": []}', encoding="utf-8")
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.write_bytes(b"")
    assert next_command(inputs) == f"decktalk {Stage.VERIFY.value}"


# ---- the sections ------------------------------------------------------------------------------


def test_a_page_section_names_the_scene_it_plays_with_the_runtime_s_own_word(tmp_path: Path) -> None:
    """The query vocabulary has one home, so the report reads the key rather than spelling it."""
    inputs = a_project(tmp_path)
    page, clip = inputs.document.sections
    assert source_of(page) == f"deck/index.html?{Q.SCENE.value}=1"
    assert source_of(clip) == "media/b-roll.mp4"


def test_a_section_is_voiced_when_a_take_of_its_current_text_is_on_disk(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    inputs = a_project(tmp_path)
    a_take(inputs)
    result = status(inputs, a_run(tmp_path))
    assert [row.voiced for row in result.sections] == [True, False]
    assert [row.kind for row in result.sections] == [SectionKind.PAGE, SectionKind.CLIP]
    assert fake_ffmpeg.calls == []


def test_a_take_of_older_words_does_not_make_a_section_voiced(tmp_path: Path) -> None:
    """A take the author has since rewritten is not a take of what this section says now."""
    inputs = a_project(tmp_path)
    a_take(inputs, spoken="Something else entirely.")
    result = status(inputs, a_run(tmp_path))
    assert result.sections[0].voiced is False


def test_a_recorded_section_says_so_and_a_cut_one_says_so(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    inputs.workspace.section_video("02").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.section_video("02").write_bytes(b"")
    result = status(inputs, a_run(tmp_path))
    assert [row.recorded for row in result.sections] == [True, False]
    assert [row.cut for row in result.sections] == [False, True]


def test_whether_a_recording_still_stands_is_asked_of_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One rule decides what a run skips and what this report calls stale, and `record` owns it."""
    inputs = a_project(tmp_path)
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    monkeypatch.setattr(stage, "stale_recording", lambda _inputs, _section: "section 1: its page changed")
    run = a_run(tmp_path)
    said = notes(run)
    result = status(inputs, run)
    assert result.sections[0].stale is True
    assert any("its page changed" in line for line in said)


def test_a_section_with_no_recording_is_never_stale(tmp_path: Path) -> None:
    """Nothing on disk cannot have stopped matching the project, so the row says what it has."""
    result = status(a_project(tmp_path), a_run(tmp_path))
    assert [row.stale for row in result.sections] == [False, False]


# ---- what the project has not got ---------------------------------------------------------------


def test_a_missing_script_is_a_finding(tmp_path: Path) -> None:
    result = status(a_project(tmp_path, script=None), a_run(tmp_path))
    assert [found.code for found in result.findings] == [Code.FILE_MISSING]
    assert result.ok is False


def test_a_missing_page_is_a_finding_that_names_its_section(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    (tmp_path / "deck" / "index.html").unlink()
    result = status(inputs, a_run(tmp_path))
    found = next(row for row in result.findings if row.code is Code.FILE_MISSING)
    assert found.location.section == 1
    assert "deck/index.html" in found.message


def test_an_optional_clip_the_project_has_not_got_is_not_a_finding(tmp_path: Path) -> None:
    """A slate stands in for an optional clip, so its absence is the project working as written."""
    toml = TOML.replace('clip = "media/b-roll.mp4"', 'clip = "media/b-roll.mp4"\noptional = true')
    inputs = a_project(tmp_path, toml=toml)
    (tmp_path / "media" / "b-roll.mp4").unlink()
    assert status(inputs, a_run(tmp_path)).findings == ()


def test_a_file_that_will_not_parse_is_a_line_and_not_a_refusal(tmp_path: Path) -> None:
    """Reading the inputs against each other is this command's work rather than its obstacle."""
    inputs = a_project(tmp_path)
    (tmp_path / "cues.json").write_text("{not json", encoding="utf-8")
    run = a_run(tmp_path)
    said = notes(run)
    result = status(inputs, run)
    assert isinstance(result, StatusResult)
    assert any("cues.json will not parse" in line for line in said)


# ---- the runs that are still open ----------------------------------------------------------------


def an_events_file(inputs: Inputs, name: str, lines: list[dict]) -> Path:
    path = inputs.workspace.events_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return path


def opened(run: str, seq: int = 0) -> dict:
    return {"event": "run.start", "time": "2026-09-24T01:00:00Z", "seq": seq, "run": run, "events_path": None}


def test_a_run_that_has_not_closed_is_listed_with_the_stage_it_opened(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    an_events_file(
        inputs,
        "other",
        [
            opened("other"),
            {"event": "stage.start", "time": "2026-09-24T01:00:01Z", "seq": 1, "run": "other",
             "stage": "record", "index": 1, "count": 2},
        ],
    )  # fmt: skip
    result = status(inputs, a_run(tmp_path))
    assert [row.run for row in result.runs] == ["other"]
    assert result.runs[0].stage is Stage.RECORD
    assert result.runs[0].events == Path("build/events/other.jsonl")


def test_a_run_that_has_closed_is_not_listed(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    an_events_file(
        inputs,
        "done",
        [
            opened("done"),
            {"event": "run.done", "time": "2026-09-24T01:00:02Z", "seq": 1, "run": "done",
             "outcome": "ok", "seconds": 1.0},
        ],
    )  # fmt: skip
    assert status(inputs, a_run(tmp_path)).runs == ()


def test_the_run_making_the_report_is_not_one_of_the_runs_it_reports(tmp_path: Path) -> None:
    """A caller asking what is running wants the runs it is not making."""
    inputs = a_project(tmp_path)
    an_events_file(inputs, "r1", [opened("r1")])
    assert status(inputs, a_run(tmp_path)).runs == ()


def test_an_events_file_this_version_cannot_read_is_skipped_with_a_line(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    an_events_file(inputs, "odd", [{"event": "nothing-like-this"}])
    run = a_run(tmp_path)
    said = notes(run)
    result = status(inputs, run)
    assert result.runs == ()
    assert any("odd.jsonl" in line for line in said)


# ---- the film ------------------------------------------------------------------------------------


def test_the_built_film_is_measured_and_an_unbuilt_one_is_null(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    inputs = a_project(tmp_path)
    assert status(inputs, a_run(tmp_path)).film is None
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.write_bytes(b"")
    fake_ffmpeg.duration_seconds = 12.5
    result = status(inputs, a_run(tmp_path))
    assert result.film == Path("build/final/demo.mp4")
    assert result.film_seconds == 12.5


def test_the_report_writes_nothing(tmp_path: Path) -> None:
    """`status` reads what is on disk, so no result of it may claim to have written anything."""
    assert "written" not in StatusResult.model_fields
    run = a_run(tmp_path)
    status(a_project(tmp_path), run)
    assert run.written == []


def test_the_report_names_the_two_files_the_author_writes(tmp_path: Path) -> None:
    result = status(a_project(tmp_path), a_run(tmp_path))
    assert result.script == Path("script.md")
    assert result.cues == Path("cues.json")
    assert result.name == "demo"


def test_a_stale_recording_is_said_at_warning_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reading a reader may act on is a warning, because it is not what the author asked for."""
    inputs = a_project(tmp_path)
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.recording("01").write_bytes(b"")
    monkeypatch.setattr(stage, "stale_recording", lambda _inputs, _section: "section 1 moved")
    run = a_run(tmp_path)
    levels: list[Level] = []
    run.machine.events.subscribe(lambda event: levels.append(event.level) if isinstance(event, Log) else None)
    status(inputs, run)
    assert Level.WARNING in levels
