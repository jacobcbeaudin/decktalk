"""The status report: what is on disk, what has gone stale, which runs are open, and what to do next."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.artifacts import Take, file_digest
from decktalk.errors import InputError
from decktalk.events import Level, Log
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.page import Q
from decktalk.pipeline import Artifact, Stage
from decktalk.results import SectionKind, StatusResult
from decktalk.stages import status as stage
from decktalk.stages.status import BUILT, next_command, source_of, status, voiced_text
from support.runs import a_run, notes
from support.takes import a_take, write_takes

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


def take_on_disk(inputs: Inputs, *, spoken: str = "Hello there again.", voiced: bool = True) -> Take:
    """One take for section one, written to the index with its audio file beside it."""
    take = a_take(1, seconds=2.0, voiced=voiced, spoken=spoken)
    write_takes(inputs, take)
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes_dir / take.file).write_bytes(b"")
    return take


# ---- what to do next ---------------------------------------------------------------------------


def test_every_artifact_the_pipeline_declares_says_when_it_is_built() -> None:
    """A stage added to the pipeline reaches this report, so a new artifact may not be left out."""
    assert set(BUILT) == set(Artifact)


def built_up_to(inputs: Inputs, steps: int) -> None:
    """The first `steps` artifacts on disk in the order a build makes them, with the take speaking the script."""
    made = [
        lambda: take_on_disk(inputs),
        lambda: inputs.workspace.cue_times_path.write_text('{"sections": []}', encoding="utf-8"),
        lambda: inputs.workspace.recording("01").write_bytes(b""),
        lambda: inputs.workspace.film.write_bytes(b"film"),
    ]
    inputs.workspace.recording("01").parent.mkdir(parents=True, exist_ok=True)
    inputs.workspace.film.parent.mkdir(parents=True, exist_ok=True)
    for make in made[:steps]:
        make()


@pytest.mark.parametrize(
    ("steps", "command"),
    [
        # The first move a project is told to make never costs credits.
        pytest.param(0, "decktalk narrate --no-voice", id="nothing built rehearses the voice"),
        pytest.param(1, f"decktalk {Stage.CUE.value}", id="takes resolve their cues"),
        pytest.param(2, f"decktalk {Stage.RECORD.value}", id="cue times record"),
        # A project that describes no soundscape is never told to generate one: a stage with nothing
        # to do is not the next thing to do.
        pytest.param(3, f"decktalk {Stage.ASSEMBLE.value}", id="recordings assemble, past the soundscape"),
        pytest.param(4, f"decktalk {Stage.VERIFY.value}", id="a film is measured"),
    ],
)
def test_a_project_is_told_the_next_stage_its_artifacts_leave(tmp_path: Path, steps: int, command: str) -> None:
    inputs = a_project(tmp_path)
    built_up_to(inputs, steps)
    assert next_command(inputs) == command


def measured(inputs: Inputs) -> None:
    """The record a build leaves after it assembled this film and verified it."""
    options: dict[str, object] = {"only": None}
    made = stage.assemble_key(inputs, options)  # type: ignore[arg-type]
    film = inputs.relative(inputs.workspace.film).as_posix()
    stage.Kept(
        assemble=stage.KeptStage(key=made, options={"only": None}, outputs={film: file_digest(inputs.workspace.film)}),
        verify=stage.KeptStage(key=stage.verify_key(inputs, made, {"only": None}), options={"only": None}),
    ).write(stage.kept_path(inputs))


def test_a_film_the_last_build_measured_leaves_nothing_next(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After a build that verified, there is nothing to do, and saying verify again would be false."""
    # The recording here has no log beside it, so the recorder's own rule is told it still stands.
    monkeypatch.setattr(stage, "stale_recording", lambda _inputs, _section: None)
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    measured(inputs)
    assert next_command(inputs) is None
    assert status(inputs, a_run(tmp_path)).next is None


def test_a_script_edit_after_the_build_names_build(tmp_path: Path) -> None:
    """A take of older words is stale, and verify would measure a film its inputs no longer describe."""
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    measured(inputs)
    (tmp_path / "script.md").write_text(SCRIPT.replace("again", "once more"), encoding="utf-8")
    assert next_command(inputs) == "decktalk build"


def test_a_stale_recording_names_build(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    assert next_command(inputs, stale=True) == "decktalk build"


def test_a_film_changed_since_the_build_names_build(tmp_path: Path) -> None:
    """A film rewritten outside the build is not the one the record vouches for."""
    inputs = a_project(tmp_path)
    built_up_to(inputs, 4)
    measured(inputs)
    inputs.workspace.film.write_bytes(b"another film")
    assert next_command(inputs) == "decktalk build"


# ---- the sections ------------------------------------------------------------------------------


def test_a_page_section_names_the_scene_it_plays_with_the_runtime_s_own_word(tmp_path: Path) -> None:
    """The query vocabulary has one home, so the report reads the key rather than spelling it."""
    inputs = a_project(tmp_path)
    page, clip = inputs.document.sections
    assert source_of(page) == f"deck/index.html?{Q.SCENE.value}=1"
    assert source_of(clip) == "media/b-roll.mp4"


def test_a_section_is_voiced_when_a_take_of_its_current_text_is_on_disk(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    inputs = a_project(tmp_path)
    take_on_disk(inputs)
    result = status(inputs, a_run(tmp_path))
    assert [row.voiced for row in result.sections] == [True, False]
    assert [row.kind for row in result.sections] == [SectionKind.PAGE, SectionKind.CLIP]
    assert fake_ffmpeg.calls == []


def test_a_placeholder_take_does_not_make_a_section_voiced(tmp_path: Path) -> None:
    """`voiced` is the take's own word, so the column that says what this project has paid for
    counted a run without a voice as though it had bought every section."""
    inputs = a_project(tmp_path)
    take_on_disk(inputs, voiced=False)
    result = status(inputs, a_run(tmp_path))
    assert result.sections[0].voiced is False


def test_a_take_of_older_words_does_not_make_a_section_voiced(tmp_path: Path) -> None:
    """A take the author has since rewritten is not a take of what this section says now."""
    inputs = a_project(tmp_path)
    take_on_disk(inputs, spoken="Something else entirely.")
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
    said: list[Log] = []
    run.machine.events.subscribe(lambda event: said.append(event) if isinstance(event, Log) else None)
    result = status(inputs, run)
    assert result.sections[0].stale is True
    # A reading a reader may act on is a warning, because it is not what the author asked for.
    assert any("its page changed" in line.message and line.level is Level.WARNING for line in said)


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


def test_a_script_that_will_not_parse_is_recorded_rather_than_read_as_no_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Unparsed:
        def script(self) -> None:
            raise InputError("script.md has no sections.")

    with caplog.at_level("DEBUG", logger="decktalk"):
        assert voiced_text(Unparsed()) == {}  # type: ignore[arg-type]
    [record] = [record for record in caplog.records if record.name == "decktalk.stages.status"]
    assert record.exc_info is not None and "no sections" in str(record.exc_info[1])
