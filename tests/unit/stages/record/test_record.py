"""The record stage: one pass over the sections that records, measures, checks and logs each one."""

from __future__ import annotations

import importlib
import json

import pytest

from decktalk.artifacts import Luma, RecordingChecks, RecordingLog, Timeline, TimelineSection, Word
from decktalk.errors import ConfigError, MissingInputError
from decktalk.model import Project
from decktalk.stages.record import RecordResult, SectionRecording, jobs, record
from decktalk.verdicts import Findings, Verdict

# `decktalk.stages` exports a function named `record`, so the stage package is imported by its path.
record_stage = importlib.import_module("decktalk.stages.record")

TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"
record_margin_seconds = 0.5

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
record_margin_seconds = 0.5
"""


@pytest.fixture
def project(tmp_path) -> Project:
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    p = Project.load(tmp_path, environ={})
    Timeline(
        narration="n.mp3",
        total_seconds=6.0,
        sections={
            "01": TimelineSection("A", 0.0, 3.0, 3.0, 2.5, [Word("one", 0.7, 1.0)]),
            "02": TimelineSection("B", 3.0, 6.0, 3.0, 5.5, [Word("two", 3.4, 3.8)]),
        },
    ).save(p.timeline_path)
    return p


def a_log(**fields) -> RecordingLog:
    base = dict(url="u", requested_seconds=3.5, settle_seconds=0.6, load_seconds=0.1, clock_start_seconds=1.5)
    return RecordingLog(**{**base, **fields})


def a_row(project, key: str = "01", **fields) -> SectionRecording:
    section = next(s for s in project.page_sections if s.key == key)
    return SectionRecording(section=section, path=project.recording(section), log=a_log(**fields))


def drive(monkeypatch, project, logs: dict[str, RecordingLog]) -> None:
    """Replace the browser and the three steps a section goes through, so no Chromium is launched."""
    from decktalk.stages.record import start as start_module

    monkeypatch.setattr(record_stage, "chromium", lambda path: _NoBrowser())
    monkeypatch.setattr(record_stage, "capture_section", lambda p, b, job: logs[job.section.key])
    monkeypatch.setattr(
        record_stage, "find_start", lambda webm, settle, cfg: start_module.Start(0.44, "cover (11 magenta frames)")
    )
    monkeypatch.setattr(
        record_stage,
        "check_recording",
        lambda webm, log, cfg: RecordingChecks(3.5, 3.5, Luma(90.0, 90.0, 90.0, 200.0), tuple(log.warnings and ())),
    )


class _NoBrowser:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def test_a_section_with_no_span_yet_is_skipped_and_an_empty_run_says_so(project, caplog):
    Timeline(narration="n.mp3", total_seconds=0.0, sections={}).save(project.timeline_path)
    with caplog.at_level("WARNING", logger="decktalk"), pytest.raises(ConfigError, match="nothing to record"):
        jobs(project, None, None, use_cues=True)
    assert [r.getMessage() for r in caplog.records] == [
        "section 01: no narration span yet; skipped",
        "section 02: no narration span yet; skipped",
    ]


def test_without_a_timeline_and_without_seconds_the_stage_names_the_command_to_run(project):
    project.timeline_path.unlink()
    with pytest.raises(MissingInputError, match="run `decktalk narrate` first"):
        jobs(project, None, None, use_cues=True)


def test_each_job_asks_for_the_section_span_plus_its_margin(project):
    planned = jobs(project, None, None, use_cues=True)
    assert [(j.section.key, j.seconds) for j in planned] == [("01", 3.5), ("02", 3.5)]
    assert [j.section.key for j in jobs(project, [2], None, use_cues=True)] == ["02"]
    assert [j.seconds for j in jobs(project, None, 9.0, use_cues=True)] == [9.0, 9.0]


def test_every_section_is_measured_checked_and_logged_before_the_next_one_starts(project, monkeypatch):
    written: list[str] = []
    logs = {"01": a_log(), "02": a_log()}
    drive(monkeypatch, project, logs)
    real_save = RecordingLog.save

    def save(self, path):
        written.append(path.name)
        real_save(self, path)

    monkeypatch.setattr(RecordingLog, "save", save)
    result = record(project)
    assert written == ["01.json", "02.json"]
    assert [row.key for row in result.sections] == ["01", "02"]
    for row in result.sections:
        assert row.log.t0_seconds == 0.44 and row.log.t0_method == "cover (11 magenta frames)"
        assert row.log.checks is not None and row.ok and row.label == "ok"
    stored = json.loads((project.recordings_dir / "01.json").read_text(encoding="utf-8"))
    assert stored["t0_seconds"] == 0.44 and stored["checks"]["verdicts"] == []


def test_a_row_carries_everything_a_reader_needs_about_one_recording(project):
    checks = RecordingChecks(3.481, 3.5, Luma(10.0, 20.0, 30.0, 200.0), (Verdict.NO_COVER,))
    row = a_row(project, checks=checks, t0_seconds=0.44, t0_method="NO COVER: fallback", assets=["deck/index.html"])
    row.log.frame_gaps = [(1.0, 140)]
    d = json.loads(json.dumps(row.to_dict(project.root)))
    assert d["key"] == "01" and d["file"] == "build/recordings/01.webm"
    assert d["duration"] == 3.481 and d["wanted"] == 3.5 and d["t0_seconds"] == 0.44
    assert d["luma"] == {"y10": 10.0, "y50": 20.0, "y90": 30.0, "max50": 200.0}
    assert d["verdicts"] == ["NO_COVER"] and d["stall_ms"] == 140 and d["assets"] == ["deck/index.html"]
    assert row.label == "NO COVER" and not row.ok


def test_the_result_tallies_every_verdict_and_names_the_pages_that_threw(project):
    good = a_row(project, "01", checks=RecordingChecks(1.0, 1.0, Luma(1, 1, 1, 1)))
    bad = a_row(
        project,
        "02",
        page_errors=["ReferenceError: nope"],
        checks=RecordingChecks(1.0, 1.0, Luma(1, 1, 1, 1), (Verdict.PAGE_ERROR, Verdict.BLACK_UNSURE)),
    )
    result = RecordResult(sections=[good, bad])
    assert result.findings == Findings(certain=1, uncertain=1)
    assert [row.key for row in result.page_errors] == ["02"]
    assert [r["key"] for r in result.to_dict(project.root)["recordings"]] == ["01", "02"]
    assert RecordResult().findings == Findings()
