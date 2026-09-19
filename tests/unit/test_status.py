"""`status` reads the four input files against each other, because they are only right together."""

from __future__ import annotations

import json
import os
from pathlib import Path

from decktalk.cli import main
from decktalk.cli.output import status_table
from decktalk.model import Project
from decktalk.status import status
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


def test_a_cue_for_a_section_that_is_not_declared_is_reported(tmp_path):
    proj = project(tmp_path, cues='{"sections": {"7": {"cues": [{"cue": "7.1a", "on": "hello"}]}}}')
    assert [r.where for r in status(proj).problems if r.where == "cues.json"] == ["cues.json"]


def test_the_cli_exits_1_and_leads_the_envelope_with_the_failing_rows(tmp_path, capsys):
    project(tmp_path)
    assert main(["status", "-p", str(tmp_path), "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and doc["findings"]["certain"] == 2
    assert [r["where"] for r in doc["findings"]["items"]] == ["deck/index.html", "media/broll.mp4"]
    assert all(r["code"] == "MISSING" and r["certain"] for r in doc["findings"]["items"])
    # The rows are in the payload under a key of their own, which is where findings.items[] is
    # lifted from on every command, so the tally and the rows can never come apart.
    assert [r["where"] for r in doc["status"]["problems"]] == ["deck/index.html", "media/broll.mp4"]
    assert doc["status"]["run"] is None
    # The table leads with the same rows, and --exit-zero leaves them found and exits 0.
    assert main(["status", "-p", str(tmp_path)]) == 1
    assert capsys.readouterr().out.splitlines()[1].startswith("MISSING  deck/index.html:")
    assert main(["status", "-p", str(tmp_path), "--exit-zero"]) == 0


def test_a_running_build_is_read_from_the_progress_log(tmp_path):
    """An agent polls `status` instead of tailing a file, so the run it reports is the one going."""
    proj = project(tmp_path)
    log = proj.build / "progress.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ts": "2026-09-18T20:32:53.581Z", "pid": os.getpid(), "stage": "narrate", "stage_index": 1,
         "stage_count": 7, "section": None, "event": "start", "detail": None},
        {"ts": "2026-09-18T20:33:01.470Z", "pid": os.getpid(), "stage": "record", "stage_index": 3,
         "stage_count": 7, "section": 2, "event": "done", "detail": None},
    ]  # fmt: skip
    log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    run = status(proj).run
    assert run is not None
    assert (run.stage, run.started, run.pid) == ("record", rows[0]["ts"], os.getpid())
    assert run.alive is True and run.sections_done == 1 and run.sections_total is None


def test_a_finished_build_is_not_alive_whatever_the_machine_says(tmp_path):
    """The last stage closed, so no pid is asked about and a pid a new process took is never read."""
    proj = project(tmp_path)
    log = proj.build / "progress.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": "2026-09-18T20:33:04.216Z", "pid": os.getpid(), "stage": "verify", "stage_index": 7,
           "stage_count": 7, "section": None, "event": "done", "detail": None}  # fmt: skip
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")
    run = status(proj).run
    assert run is not None and run.alive is False and run.stage == "verify"


def test_a_build_whose_process_is_gone_is_not_alive(tmp_path):
    """The pid is the one key `status` needs, so the case it exists to answer has a test."""
    proj = project(tmp_path)
    log = proj.build / "progress.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": "2026-09-18T20:32:53.581Z", "pid": 2**22 - 1, "stage": "record", "stage_index": 3,
           "stage_count": 7, "section": None, "event": "start", "detail": None}  # fmt: skip
    log.write_text(json.dumps(row) + "\n", encoding="utf-8")
    result = status(proj)
    assert result.run is not None and result.run.alive is False and result.run.stage == "record"
    assert status_table(result).splitlines()[-1].startswith("build     last run at record")


def test_a_torn_last_line_and_an_empty_log_are_both_survived(tmp_path):
    """The writer appends while a reader reads, so the last line can arrive half written."""
    proj = project(tmp_path)
    log = proj.build / "progress.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": "2026-09-18T20:32:53.581Z", "pid": os.getpid(), "stage": "align", "stage_index": 2,
           "stage_count": 7, "section": None, "event": "start", "detail": None}  # fmt: skip
    log.write_text(json.dumps(row) + '\n{"ts": "2026-09-18T20:33', encoding="utf-8")
    run = status(proj).run
    assert run is not None and run.stage == "align" and run.alive is True
    log.write_text("", encoding="utf-8")
    assert status(proj).run is None


def test_a_build_artifact_a_hand_edit_broke_is_a_row_and_not_a_traceback(tmp_path):
    """Reading what is on disk is this command's work, so a broken file there is its subject too."""
    proj = project(tmp_path)
    proj.cue_times_path.parent.mkdir(parents=True, exist_ok=True)
    proj.cue_times_path.write_text("{not json", encoding="utf-8")
    [row] = [r for r in status(proj).problems if r.where == "build/cue-times.json"]
    assert row.verdict is Verdict.UNREADABLE and "is not valid JSON" in row.detail
