"""The whole pipeline in order: which stages a run executes, what it needs, and what it writes down."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from decktalk.artifacts import ProgressRow, Take, Takes, Word, append_row, read_rows, start_log, write_words
from decktalk.errors import ConfigError, MissingInputError, ToolError
from decktalk.model import Project
from decktalk.pipeline import ProgressEvent, Stage
from decktalk.stages.build import BuildResult, Progress, build, required_inputs, stage_plan
from decktalk.verdicts import Findings, Verdict

NARRATE, ALIGN, RECORD, ASSEMBLE, VERIFY = Stage.NARRATE, Stage.ALIGN, Stage.RECORD, Stage.ASSEMBLE, Stage.VERIFY
START, DONE, FAIL = ProgressEvent.START, ProgressEvent.DONE, ProgressEvent.FAIL

TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"
"""


@pytest.fixture
def project(tmp_path) -> Project:
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "script.md").write_text("## 1. A\n\nHi there.\n", encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return Project.load(tmp_path, environ={})


def narrated(project: Project) -> None:
    project.takes_dir.mkdir(parents=True, exist_ok=True)
    write_words(project.takes_dir / "h1.words.json", [Word("Hi", 0.7, 1.0)])
    takes = Takes(script="script.md", model="m", output_format="mp3")
    takes.sections["01"] = Take(1, "A", "h1.mp3", "h1.words.json", "h1", 1, 2.0, 2.0, speech_end_seconds=1.8)
    takes.save(project.takes_path)


def test_the_five_stages_run_in_the_order_they_are_declared_and_both_ends_are_inclusive():
    assert list(Stage) == [NARRATE, ALIGN, RECORD, ASSEMBLE, VERIFY]
    assert stage_plan(None, None) == tuple(Stage)
    assert stage_plan(RECORD, None) == (RECORD, ASSEMBLE, VERIFY)
    assert stage_plan(None, ALIGN) == (NARRATE, ALIGN)
    assert stage_plan(ALIGN, ALIGN) == (ALIGN,)


def test_a_backwards_plan_is_refused():
    with pytest.raises(ConfigError, match="the stage verify comes after the stage record"):
        stage_plan(VERIFY, RECORD)


def test_a_run_that_skips_a_stage_needs_what_that_stage_would_have_written(project):
    assert required_inputs(project, tuple(Stage)) == []
    assert required_inputs(project, (ALIGN, RECORD, ASSEMBLE, VERIFY)) == [project.takes_path]
    narrated(project)
    assert required_inputs(project, (ALIGN, RECORD, ASSEMBLE, VERIFY)) == []
    assert required_inputs(project, (ASSEMBLE, VERIFY)) == [
        project.cue_times_path,
        project.recording(project.page_sections[0]),
    ]
    assert required_inputs(project, (VERIFY,)) == [project.cue_times_path, project.final]


def test_a_run_that_starts_past_a_missing_artifact_stops_and_names_it(project):
    with pytest.raises(MissingInputError) as err:
        build(project, from_stage=ASSEMBLE)
    assert "this run starts at assemble and needs build/narration/takes.json" in str(err.value)
    assert "start the build further back with --from" in str(err.value)


def test_the_progress_log_is_one_json_line_per_event_and_starts_empty(tmp_path):
    path = tmp_path / "progress.jsonl"
    path.write_text("a line from the run before\n", encoding="utf-8")
    steps = Progress(path=path, stages=(RECORD, ASSEMBLE))
    steps.start()
    assert path.read_text(encoding="utf-8") == ""
    steps.event(RECORD, START)
    steps.event(RECORD, DONE, section=3, detail="Recorded the section: ok.")
    rows = read_rows(path)
    assert [r.event for r in rows] == [START, DONE]
    assert rows[1] == ProgressRow(
        ts=rows[1].ts,
        pid=os.getpid(),
        stage=RECORD,
        stage_index=1,
        stage_count=2,
        section=3,
        event=DONE,
        detail="Recorded the section: ok.",
    )
    assert rows[1].ts.endswith("Z")


def test_a_result_adds_every_stage_and_is_not_ok_until_each_planned_stage_ran():
    empty = BuildResult()
    assert empty.findings == Findings() and not empty.ok, "no stage ran, so nothing was judged ok"
    one = BuildResult(stages=(VERIFY,))
    assert not one.ok


def test_a_result_names_each_stage_as_a_build_does():
    """The payload keys and the `stages` list are the words `--from` takes, never a member's repr."""
    doc = BuildResult(stages=(ALIGN, RECORD)).to_dict(Path("/p"))
    assert list(doc) == [*(stage.value for stage in Stage), "stages", "progress"]
    assert doc["stages"] == [ALIGN.value, RECORD.value]


def test_the_missing_artifact_stop_writes_its_own_row_over_the_run_before(project):
    """A reader that opens the log after a refused run must not be handed the run before it."""
    # A whole row another process wrote, so a log left from the run before would still read as a row.
    before = ProgressRow(
        ts="2026-09-18T20:33:04.216Z", pid=os.getpid() + 1, stage=VERIFY, stage_index=5, stage_count=5,
        section=None, event=DONE, detail="Found 0 certain and 0 uncertain finding(s).",
    )  # fmt: skip
    start_log(project.progress_path)
    append_row(project.progress_path, before)
    assert read_rows(project.progress_path) == [before]
    with pytest.raises(MissingInputError):
        build(project, from_stage=ASSEMBLE)
    [row] = read_rows(project.progress_path)
    assert (row.stage, row.event) == (ASSEMBLE, FAIL)
    assert row.detail is not None and "takes.json" in row.detail and row.pid == os.getpid()


# `decktalk.stages` exports a function named `build`, so the stage module is imported by its path.
build_module = importlib.import_module("decktalk.stages.build")


def _stages(monkeypatch, **results):
    """Replace each stage `build` calls with one that returns what the test wants."""
    for name, value in results.items():
        monkeypatch.setattr(build_module, name, value)


def test_every_stage_is_opened_on_the_reporter_before_it_runs(project, monkeypatch):
    """The reporter learns a stage started from this call, and it is what names the stage on stderr."""
    from types import SimpleNamespace

    narrated(project)
    align_result = SimpleNamespace(unresolved=0, problems=[], findings=Findings(), to_dict=lambda root: {})
    _stages(monkeypatch, align=lambda project, allow_unknown_cues: align_result)
    seen: list[tuple[Stage, bool]] = []
    report = lambda stage, result: seen.append((stage, result is None))  # noqa: E731
    build(project, from_stage=ALIGN, to_stage=ALIGN, report=report)
    # The opening call carries no result, and it comes before the one that closes the stage.
    assert seen == [(ALIGN, True), (ALIGN, False)]


def test_a_cue_phrase_that_was_not_found_stops_the_run_and_says_so_in_the_log(project, monkeypatch):
    from types import SimpleNamespace

    narrated(project)
    align_result = SimpleNamespace(
        unresolved=2,
        problems=["section 1: 'a bowl' is not in the words"],
        findings=Findings(certain=2),
        to_dict=lambda root: {},
    )
    _stages(monkeypatch, align=lambda project, allow_unknown_cues: align_result)
    with pytest.raises(ConfigError, match="could not be matched to the narration"):
        build(project, from_stage=ALIGN, to_stage=ALIGN)
    rows = read_rows(project.progress_path)
    assert [r.event for r in rows] == [START, DONE, FAIL, FAIL]
    assert rows[2].detail == "2 cue phrase(s) were not found."
    assert str(rows[3].detail).startswith("The run stopped on ConfigError: 2 cue(s) could not be matched")


def test_a_page_error_while_recording_stops_the_run_and_says_so_in_the_log(project, monkeypatch):
    from types import SimpleNamespace

    from decktalk.artifacts import RecordingLog

    narrated(project)
    project.cue_times_path.write_text("{}", encoding="utf-8")
    section = project.page_sections[0]
    log = RecordingLog(url="u", requested_seconds=1.0, settle_seconds=0.1, load_seconds=0.0, clock_start_seconds=0.1)
    log.page_errors = ["ReferenceError: nope is not defined (index.html:5)"]
    row = SimpleNamespace(section=section, key=section.key, log=log, kept=False, label=Verdict.PAGE_ERROR.label)
    result = SimpleNamespace(
        sections=[row],
        kept_sections=[],
        page_errors=[row],
        findings=Findings(certain=1),
        to_dict=lambda root: {},
    )
    _stages(monkeypatch, record=lambda project, only, opening, report: result)
    with pytest.raises(ConfigError, match="hit a page error while recording"):
        build(project, from_stage=RECORD, to_stage=RECORD)
    rows = read_rows(project.progress_path)
    assert [r.event for r in rows] == [START, DONE, FAIL, FAIL]
    assert rows[2].detail == "1 section(s) hit a page error."


def test_the_closing_row_names_the_stage_that_was_running(project, monkeypatch):
    """A reader tells a dead run from a working one by that row, so it may not name a stage that never ran."""

    def boom(project, **kw):
        raise ToolError("the voice service is down")

    _stages(monkeypatch, narrate=boom)
    with pytest.raises(ToolError):
        build(project)
    rows = read_rows(project.progress_path)
    assert [(r.stage, r.event) for r in rows] == [(NARRATE, START), (NARRATE, FAIL)]
    assert rows[-1].stage_index == 1
    # The row is what a reader relays to a person, so it carries why the run stopped and not only on what.
    assert rows[-1].detail == "The run stopped on ToolError: the voice service is down"


def test_the_closing_row_of_a_five_stage_plan_names_the_stage_that_raised(project, monkeypatch):
    """The first stage of a plan is where the wrong arithmetic and the right one agree, so this one dies third."""
    from types import SimpleNamespace

    narrated(project)
    project.cue_times_path.write_text("{}", encoding="utf-8")

    def boom(project, only, opening, report):
        raise ToolError("Chromium closed the connection")

    empty = SimpleNamespace(findings=Findings(), to_dict=lambda root: {}, synthesized=[], unresolved=0, problems=[])
    _stages(monkeypatch, narrate=lambda project, silent, force: empty, align=lambda project, **kw: empty, record=boom)
    with pytest.raises(ToolError):
        build(project)
    rows = read_rows(project.progress_path)
    assert (rows[-1].stage, rows[-1].event) == (RECORD, FAIL)
    assert rows[-1].stage_index == 3 and rows[-1].stage_count == 5
    assert rows[-1].detail == "The run stopped on ToolError: Chromium closed the connection"


def a_logged_recording(key: str):
    """One row of verify's recordings table, which repeats what that section's recording log judged."""
    from decktalk.stages.verify import LoggedRecording
    from decktalk.verdicts import Verdict

    return LoggedRecording(key=key, verdicts=(Verdict.PAGE_ERROR,), page_errors=(), t0_method=None, where="w")


def test_a_build_counts_a_recording_fault_once_although_verify_reports_it_again():
    """`record` judges each log and `verify` reads it back, and the exit code needs one count."""
    from types import SimpleNamespace

    from decktalk.stages.verify import VerifyResult

    verification = VerifyResult(total_seconds=2.0, recordings=[a_logged_recording("01")])
    row = SimpleNamespace(key="01")
    recordings = SimpleNamespace(sections=[row], findings=Findings(certain=1), to_dict=lambda root: {})
    assert verification.findings == Findings(certain=1) and verification.film_findings == Findings()
    both = BuildResult(stages=(RECORD, VERIFY), recordings=recordings, verification=verification)
    assert both.findings == Findings(certain=1), "the same page error is one finding, not two"
    alone = BuildResult(stages=(VERIFY,), verification=verification)
    assert alone.findings == Findings(certain=1), "verify alone still reports what the logs judged"


def test_a_build_counts_a_fault_in_a_section_this_run_did_not_record():
    """`--only` names the sections `record` opens, and `verify` still reads every log there is."""
    from types import SimpleNamespace

    from decktalk.stages.verify import VerifyResult

    verification = VerifyResult(total_seconds=2.0, recordings=[a_logged_recording("01"), a_logged_recording("02")])
    recorded = SimpleNamespace(sections=[SimpleNamespace(key="01")], findings=Findings(certain=1),
                               to_dict=lambda root: {})  # fmt: skip
    out = BuildResult(stages=(RECORD, VERIFY), recordings=recorded, verification=verification)
    assert out.findings == Findings(certain=2), "section 2's page error is counted by verify, which read its log"
