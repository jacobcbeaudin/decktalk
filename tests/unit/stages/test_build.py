"""The whole pipeline in order: which stages a run executes, what it needs, and what it writes down."""

from __future__ import annotations

import importlib
import json
import os

import pytest

from decktalk.artifacts import Timeline, TimelineSection, Word
from decktalk.errors import ConfigError, MissingInputError, ToolError
from decktalk.model import Project
from decktalk.stages.build import STAGES, BuildResult, Progress, build, required_inputs, stage_plan
from decktalk.verdicts import Findings

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
    Timeline(
        narration="narration.mp3",
        total_seconds=2.0,
        sections={"01": TimelineSection("A", 0.0, 2.0, 2.0, 1.8, [Word("Hi", 0.7, 1.0)])},
    ).save(project.timeline_path)


def test_the_five_stages_are_the_only_values_and_both_ends_are_inclusive():
    assert STAGES == ("narrate", "align", "record", "assemble", "verify")
    assert stage_plan(None, None) == STAGES
    assert stage_plan("record", None) == ("record", "assemble", "verify")
    assert stage_plan(None, "align") == ("narrate", "align")
    assert stage_plan("align", "align") == ("align",)


def test_an_unknown_or_backwards_stage_names_the_five():
    with pytest.raises(ConfigError, match="narrate, align, record, assemble, verify"):
        stage_plan("measure", None)
    with pytest.raises(ConfigError, match="comes after"):
        stage_plan("verify", "record")


def test_a_run_that_skips_a_stage_needs_what_that_stage_would_have_written(project):
    assert required_inputs(project, STAGES) == []
    assert required_inputs(project, ("align", "record", "assemble", "verify")) == [project.timeline_path]
    narrated(project)
    assert required_inputs(project, ("align", "record", "assemble", "verify")) == []
    assert required_inputs(project, ("assemble", "verify")) == [
        project.cue_times_path,
        project.recording(project.page_sections[0]),
    ]
    assert required_inputs(project, ("verify",)) == [project.cue_times_path, project.final]


def test_a_run_that_starts_past_a_missing_artifact_stops_and_names_it(project):
    with pytest.raises(MissingInputError) as err:
        build(project, from_stage="assemble")
    assert "this run starts at assemble and needs build/narration/timeline.json" in str(err.value)
    assert "start the build further back with --from" in str(err.value)


def test_the_progress_log_is_one_json_line_per_event_and_starts_empty(tmp_path):
    path = tmp_path / "progress.jsonl"
    path.write_text("a line from the run before\n", encoding="utf-8")
    steps = Progress(path=path, stages=("record", "assemble"))
    steps.start()
    assert path.read_text(encoding="utf-8") == ""
    steps.event("record", "start")
    steps.event("record", "done", section=3, detail="Recorded the section: ok.")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["start", "done"]
    assert rows[1] == {
        "ts": rows[1]["ts"],
        "pid": os.getpid(),
        "stage": "record",
        "stage_index": 1,
        "stage_count": 2,
        "section": 3,
        "event": "done",
        "detail": "Recorded the section: ok.",
    }
    assert rows[1]["ts"].endswith("Z")


def test_a_result_adds_every_stage_and_is_not_ok_until_each_planned_stage_ran():
    empty = BuildResult()
    assert empty.findings == Findings() and not empty.ok, "no stage ran, so nothing was judged ok"
    one = BuildResult(stages=("verify",))
    assert not one.ok


def test_the_missing_artifact_stop_writes_its_own_row_over_the_run_before(project):
    """A reader that opens the log after a refused run must not be handed the run before it."""
    project.progress_path.parent.mkdir(parents=True, exist_ok=True)
    project.progress_path.write_text('{"stage": "verify", "event": "done"}\n', encoding="utf-8")
    with pytest.raises(MissingInputError):
        build(project, from_stage="assemble")
    [row] = [json.loads(line) for line in project.progress_path.read_text(encoding="utf-8").splitlines()]
    assert (row["stage"], row["event"]) == ("assemble", "fail")
    assert "timeline.json" in row["detail"] and row["pid"] == os.getpid()


# `decktalk.stages` exports a function named `build`, so the stage module is imported by its path.
build_module = importlib.import_module("decktalk.stages.build")


def _stages(monkeypatch, **results):
    """Replace each stage `build` calls with one that returns what the test wants."""
    for name, value in results.items():
        monkeypatch.setattr(build_module, name, value)


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
        build(project, from_stage="align", to_stage="align")
    rows = [json.loads(line) for line in project.progress_path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["start", "done", "fail", "fail"]
    assert "2 cue phrase(s) were not found." in rows[2]["detail"]
    assert rows[3]["detail"].startswith("The run stopped on ConfigError: 2 cue(s) could not be matched")


def test_a_page_error_while_recording_stops_the_run_and_says_so_in_the_log(project, monkeypatch):
    from types import SimpleNamespace

    from decktalk.artifacts import RecordingLog

    narrated(project)
    project.cue_times_path.write_text("{}", encoding="utf-8")
    section = project.page_sections[0]
    log = RecordingLog(url="u", requested_seconds=1.0, settle_seconds=0.1, load_seconds=0.0, clock_start_seconds=0.1)
    log.page_errors = ["ReferenceError: nope is not defined (index.html:5)"]
    row = SimpleNamespace(section=section, key=section.key, log=log, kept=False, label="PAGE ERROR")
    result = SimpleNamespace(
        sections=[row],
        kept_sections=[],
        page_errors=[row],
        findings=Findings(certain=1),
        to_dict=lambda root: {},
    )
    _stages(monkeypatch, record=lambda project, only, opening, report: result)
    with pytest.raises(ConfigError, match="hit a page error while recording"):
        build(project, from_stage="record", to_stage="record")
    rows = [json.loads(line) for line in project.progress_path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows] == ["start", "done", "fail", "fail"]
    assert rows[2]["detail"] == "1 section(s) hit a page error."


def test_the_closing_row_names_the_stage_that_was_running(project, monkeypatch):
    """A reader tells a dead run from a working one by that row, so it may not name a stage that never ran."""

    def boom(project, **kw):
        raise ToolError("the voice service is down")

    _stages(monkeypatch, narrate=boom)
    with pytest.raises(ToolError):
        build(project)
    rows = [json.loads(line) for line in project.progress_path.read_text(encoding="utf-8").splitlines()]
    assert [(r["stage"], r["event"]) for r in rows] == [("narrate", "start"), ("narrate", "fail")]
    assert rows[-1]["stage_index"] == 1
    # The row is what a reader relays to a person, so it carries why the run stopped and not only on what.
    assert rows[-1]["detail"] == "The run stopped on ToolError: the voice service is down"


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
    rows = [json.loads(line) for line in project.progress_path.read_text(encoding="utf-8").splitlines()]
    assert (rows[-1]["stage"], rows[-1]["event"]) == ("record", "fail")
    assert rows[-1]["stage_index"] == 3 and rows[-1]["stage_count"] == 5
    assert rows[-1]["detail"] == "The run stopped on ToolError: Chromium closed the connection"


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
    both = BuildResult(stages=("record", "verify"), recordings=recordings, verification=verification)
    assert both.findings == Findings(certain=1), "the same page error is one finding, not two"
    alone = BuildResult(stages=("verify",), verification=verification)
    assert alone.findings == Findings(certain=1), "verify alone still reports what the logs judged"


def test_a_build_counts_a_fault_in_a_section_this_run_did_not_record():
    """`--only` names the sections `record` opens, and `verify` still reads every log there is."""
    from types import SimpleNamespace

    from decktalk.stages.verify import VerifyResult

    verification = VerifyResult(total_seconds=2.0, recordings=[a_logged_recording("01"), a_logged_recording("02")])
    recorded = SimpleNamespace(sections=[SimpleNamespace(key="01")], findings=Findings(certain=1),
                               to_dict=lambda root: {})  # fmt: skip
    out = BuildResult(stages=("record", "verify"), recordings=recorded, verification=verification)
    assert out.findings == Findings(certain=2), "section 2's page error is counted by verify, which read its log"
