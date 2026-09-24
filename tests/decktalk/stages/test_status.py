"""`status` reads the four input files against each other, because they are only right together."""

from __future__ import annotations

import os
from pathlib import Path

from decktalk.artifacts import ProgressRow, append_row, start_log
from decktalk.cli import main
from decktalk.cli.output import status_table
from decktalk.cli.schema import read_envelope
from decktalk.model import Project
from decktalk.pipeline import ProgressEvent, Stage
from decktalk.stages.status import status
from decktalk.verdicts import Findings, Verdict

TOML = """
[project]
name = "deck"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
clip = "media/broll.mp4"
"""


def project(tmp_path: Path, *, script: str = "## 1. Open\n\nHello.\n", cues: str = '{"sections": {}}') -> Project:
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "script.md").write_text(script, encoding="utf-8")
    (tmp_path / "cues.json").write_text(cues, encoding="utf-8")
    return Project.load(tmp_path, environ={})


def test_status_reports_every_file_assemble_writes_beside_the_film(tmp_path):
    """A reader asks `status` what is built, so the cut list, the transcript and the poster count too."""
    labels = {(row.label, row.key) for row in status(project(tmp_path)).outputs}
    assert labels == {
        ("captions", "srt"),
        ("captions", "vtt"),
        ("chapters", "chapters"),
        ("cuts", "cuts"),
        ("transcript", "transcript"),
        ("poster", "poster"),
    }


def test_a_whole_project_reports_nothing(tmp_path):
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "broll.mp4").write_bytes(b"clip")
    result = status(project(tmp_path))
    assert result.problems == [] and result.findings == Findings()


def test_a_page_and_a_clip_a_section_names_have_to_be_there(tmp_path):
    result = status(project(tmp_path))
    assert [(r.verdict, r.section, r.where) for r in result.problems] == [
        (Verdict.MISSING, 1, "deck/index.html"),
        (Verdict.MISSING, 2, "media/broll.mp4"),
    ]
    assert result.findings == Findings(certain=2)
    assert all(r.detail.startswith("section 0") for r in result.problems)


def test_a_missing_script_is_a_finding_and_not_a_traceback(tmp_path):
    proj = project(tmp_path)
    proj.script.unlink()
    [row] = [r for r in status(proj).problems if r.where == "script.md"]
    assert row.verdict is Verdict.MISSING and row.detail == "script.md is not there."


def test_a_script_that_names_a_section_the_project_does_not_is_a_disagreement(tmp_path):
    """The file parses and contradicts another, so it is reconciled rather than repaired."""
    proj = project(tmp_path, script="## 9. Elsewhere\n\nHello.\n")
    [row] = [r for r in status(proj).problems if r.where == "script.md"]
    assert row.verdict is Verdict.INCONSISTENT and "9" in row.detail


def test_a_cue_for_a_section_that_is_not_declared_is_a_disagreement_and_not_a_parse_failure(tmp_path):
    """`cues.json` is valid JSON here, so telling a reader to repair its syntax would be wrong."""
    proj = project(tmp_path, cues='{"sections": {"42": {"cues": [{"cue": "42.1a", "on": "hello"}]}}}')
    [row] = [r for r in status(proj).problems if r.where == "cues.json"]
    assert row.verdict is Verdict.INCONSISTENT and "42" in row.detail


def test_cues_that_do_not_parse_carry_their_line_and_their_next_action(tmp_path):
    """A file that is there is never MISSING, and its row keeps everything the error knew."""
    proj = project(tmp_path, cues="{not json")
    [row] = [r for r in status(proj).problems if r.where == "cues.json"]
    assert row.verdict is Verdict.UNREADABLE
    assert "cues.json is not valid JSON" in row.detail and "Line 1." in row.detail
    assert "brackets and the commas" in row.detail


def test_the_cli_exits_1_and_leads_the_envelope_with_the_failing_rows(tmp_path, capsys):
    project(tmp_path)
    assert main(["status", "-p", str(tmp_path), "--json"]) == 1
    doc = read_envelope(capsys.readouterr().out)
    assert doc.ok is False and doc.findings.certain == 2
    assert [r.where for r in doc.findings.items] == ["deck/index.html", "media/broll.mp4"]
    assert all(r.verdict is Verdict.MISSING for r in doc.findings.items)
    # The rows are in the payload under a key of their own, which is where findings.items[] is
    # lifted from on every command, so the tally and the rows can never come apart.
    assert doc.payload.problems == doc.findings.items
    assert doc.payload.run is None
    # The table leads with the same rows, and --exit-zero leaves them found and exits 0.
    assert main(["status", "-p", str(tmp_path)]) == 1
    assert capsys.readouterr().out.splitlines()[1].startswith(f"{Verdict.MISSING.label}  deck/index.html:")
    assert main(["status", "-p", str(tmp_path), "--exit-zero"]) == 0


def logged(proj: Project, *rows: ProgressRow) -> None:
    """A progress log holding these rows, written by the writer a build uses."""
    start_log(proj.progress_path)
    for row in rows:
        append_row(proj.progress_path, row)


def a_row(stage: Stage, event: ProgressEvent, *, section: int | None = None, pid: int | None = None) -> ProgressRow:
    """One row of a five-stage run, stamped at a fixed time so a test can name it."""
    index = list(Stage).index(stage) + 1
    return ProgressRow(
        ts=f"2026-09-18T20:3{index}:00.000Z", pid=os.getpid() if pid is None else pid, stage=stage,
        stage_index=index, stage_count=len(Stage), section=section, event=event, detail=None,
    )  # fmt: skip


def test_a_running_build_is_read_from_the_progress_log(tmp_path):
    """An agent polls `status` instead of tailing a file, so the run it reports is the one going."""
    proj = project(tmp_path)
    first = a_row(Stage.NARRATE, ProgressEvent.START)
    logged(proj, first, a_row(Stage.RECORD, ProgressEvent.DONE, section=2))
    run = status(proj).run
    assert run is not None
    assert (run.stage, run.started, run.pid) == (Stage.RECORD, first.ts, os.getpid())
    assert run.alive is True and run.sections_done == 1 and run.sections_total is None


def test_a_finished_build_is_not_alive_whatever_the_machine_says(tmp_path):
    """The last stage closed, so no pid is asked about and a pid a new process took is never read."""
    proj = project(tmp_path)
    logged(proj, a_row(Stage.VERIFY, ProgressEvent.DONE))
    run = status(proj).run
    assert run is not None and run.alive is False and run.stage is Stage.VERIFY


def test_a_build_whose_process_is_gone_is_not_alive(tmp_path):
    """The pid is the one key `status` needs, so the case it exists to answer has a test."""
    proj = project(tmp_path)
    logged(proj, a_row(Stage.RECORD, ProgressEvent.START, pid=2**22 - 1))
    result = status(proj)
    assert result.run is not None and result.run.alive is False and result.run.stage is Stage.RECORD
    assert status_table(result).splitlines()[-1].startswith(f"build     last run at {Stage.RECORD.value}")
    assert result.to_dict(tmp_path)["run"]["stage"] == Stage.RECORD.value


def test_a_torn_last_line_and_an_empty_log_are_both_survived(tmp_path):
    """The writer appends while a reader reads, so the last line can arrive half written."""
    proj = project(tmp_path)
    logged(proj, a_row(Stage.ALIGN, ProgressEvent.START))
    with proj.progress_path.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-09-18T20:33')
    run = status(proj).run
    assert run is not None and run.stage is Stage.ALIGN and run.alive is True
    start_log(proj.progress_path)
    assert status(proj).run is None


def test_a_build_artifact_a_hand_edit_broke_is_a_row_and_not_a_traceback(tmp_path):
    """Reading what is on disk is this command's work, so a broken file there is its subject too."""
    proj = project(tmp_path)
    proj.cue_times_path.parent.mkdir(parents=True, exist_ok=True)
    proj.cue_times_path.write_text("{not json", encoding="utf-8")
    [row] = [r for r in status(proj).problems if r.where == "build/cue-times.json"]
    assert row.verdict is Verdict.UNREADABLE and "is not valid JSON" in row.detail
