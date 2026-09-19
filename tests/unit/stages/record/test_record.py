"""The record stage: one pass over the sections that records, measures, checks and logs each one."""

from __future__ import annotations

import importlib
import json

import pytest

from decktalk.artifacts import Luma, RecordingChecks, RecordingLog, Take, Takes, Word, write_words
from decktalk.cli.schema import RecordedSection
from decktalk.errors import ConfigError, MissingInputError
from decktalk.jsonio import read_as
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


# One page, two scenes, which is the shape `decktalk init` writes and the shape a per-scene key is
# for: an edit inside one <div data-scene> must reach the section that plays it and no other.
PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>body{background:#fff}</style></head>
<body>
<div data-scene="1"><template data-slide="1.1"><p data-cue="1.1a">one</p></template></div>
<div data-scene="2"><template data-slide="2.1"><p data-cue="2.1a">two</p></template></div>
<script src="decktalk-runtime.js"></script>
</body></html>"""


@pytest.fixture
def project(tmp_path) -> Project:
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text(PAGE, encoding="utf-8")
    p = Project.load(tmp_path, environ={})
    p.takes_dir.mkdir(parents=True)
    write_words(p.takes_dir / "h1.words.json", [Word("one", 0.7, 1.0)])
    write_words(p.takes_dir / "h2.words.json", [Word("two", 0.4, 0.8)])
    takes = Takes(script="script.md", model="m", output_format="mp3")
    takes.sections["01"] = Take(1, "A", "h1.mp3", "h1.words.json", "h1", 1, 3.0, 3.0, speech_end_seconds=2.5)
    takes.sections["02"] = Take(2, "B", "h2.mp3", "h2.words.json", "h2", 1, 3.0, 3.0, speech_end_seconds=2.5)
    takes.save(p.takes_path)
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

    def capture_section(p, b, job):
        recorded = logs[job.section.key]
        recorded.assets = [job.section.page]
        recorded.input_hash = job.input_hash
        job.out.parent.mkdir(parents=True, exist_ok=True)
        job.out.write_bytes(b"a recording")
        return recorded

    monkeypatch.setattr(record_stage, "capture_section", capture_section)
    monkeypatch.setattr(
        record_stage, "find_start", lambda webm, settle, cfg: start_module.Start(0.44, "cover (11 magenta frames)")
    )
    monkeypatch.setattr(
        record_stage,
        "check_recording",
        # No verdict is raised here, because what these tests measure is which sections ran.
        lambda webm, log, cfg: RecordingChecks(3.5, 3.5, Luma(90.0, 90.0, 90.0, 200.0), ()),
    )


class _NoBrowser:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


def test_a_section_with_no_span_yet_is_skipped_and_an_empty_run_says_so(project, caplog):
    Takes(script="script.md", model="m", output_format="mp3").save(project.takes_path)
    with caplog.at_level("WARNING", logger="decktalk"), pytest.raises(ConfigError, match="nothing to record"):
        jobs(project, None, None, use_cues=True)
    assert [r.getMessage() for r in caplog.records] == [
        "section 01: there is no narration span yet, so it is skipped",
        "section 02: there is no narration span yet, so it is skipped",
    ]


def test_without_a_take_index_and_without_seconds_the_stage_names_the_command_to_run(project):
    project.takes_path.unlink()
    with pytest.raises(MissingInputError, match="no section has a length to record") as raised:
        jobs(project, None, None, use_cues=True)
    # The next action is the hint and the file is the path, so the message stays one sentence.
    assert raised.value.hint is not None and "decktalk narrate" in raised.value.hint
    assert raised.value.path == project.takes_path


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
        assert row.log.checks is not None and row.ok and row.label == Verdict.OK.label
    stored = json.loads((project.recordings_dir / "01.json").read_text(encoding="utf-8"))
    assert stored["t0_seconds"] == 0.44 and stored["checks"]["verdicts"] == []


def test_a_row_carries_everything_a_reader_needs_about_one_recording(project):
    checks = RecordingChecks(3.481, 3.5, Luma(10.0, 20.0, 30.0, 200.0), (Verdict.NO_COVER,))
    row = a_row(project, checks=checks, t0_seconds=0.44, t0_method="no cover", assets=["deck/index.html"])
    row.log.frame_gaps = [(1.0, 140)]
    d = read_as(RecordedSection, json.loads(json.dumps(row.to_dict(project.root))))
    assert d.key == "01" and d.file == "build/recordings/01.webm"
    assert d.duration == 3.481 and d.wanted == 3.5 and d.t0_seconds == 0.44
    assert d.luma == Luma(10.0, 20.0, 30.0, 200.0)
    # A verdict in a payload is the {code, label, certain} object, so it reads back as the member.
    assert d.verdicts == [Verdict.NO_COVER]
    assert d.stall_ms == 140 and d.assets == ["deck/index.html"]
    assert d.detail is not None and Verdict.NO_COVER.label in d.detail
    assert row.label == Verdict.NO_COVER.label and not row.ok


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


def test_a_section_whose_inputs_are_unchanged_is_kept(project, monkeypatch, caplog):
    drive(monkeypatch, project, {"01": a_log(), "02": a_log()})
    first = record(project)
    assert [row.kept for row in first.sections] == [False, False]

    def refuse(p, browser, job):
        raise AssertionError(f"section {job.section.key} was recorded again")

    monkeypatch.setattr(record_stage, "capture_section", refuse)
    with caplog.at_level("INFO", logger="decktalk"):
        again = record(project)
    assert [(row.key, row.kept) for row in again.sections] == [("01", True), ("02", True)]
    assert again.kept_sections == again.sections and again.findings == Findings()
    kept_line = "kept: its scene, the page around it, its assets, words and cues are unchanged"
    assert any(kept_line in r.getMessage() for r in caplog.records)


def test_a_section_only_passed_over_is_reported_when_its_recording_no_longer_stands(project, monkeypatch):
    """A build assembles every section, so a stale one the run did not name may not go unreported."""
    drive(monkeypatch, project, {"01": a_log(), "02": a_log()})
    record(project)
    # Section 1's page moves while the run names only section 2, so section 1 is out of date.
    (project.root / "deck" / "index.html").write_text("<!doctype html><p>new</p>", encoding="utf-8")
    result = record(project, only=[2])
    assert [row.key for row in result.sections] == ["02"]
    [stale] = result.rows
    assert stale.section == 1 and stale.verdict.certain
    assert "--only did not name it" in (stale.detail or "")
    assert result.findings == Findings(certain=1)
    assert result.to_dict(project.root)["stale"][0]["section"] == 1
    # A run that names nothing judges every section itself, so it reports no such row.
    assert record(project).rows == []


def test_a_section_named_by_only_is_recorded_however_unchanged_it_is(project, monkeypatch):
    drive(monkeypatch, project, {"01": a_log(), "02": a_log()})
    record(project)
    recorded: list[str] = []
    real = record_stage.capture_section
    monkeypatch.setattr(
        record_stage, "capture_section", lambda p, b, job: (recorded.append(job.section.key), real(p, b, job))[1]
    )
    result = record(project, only=[2])
    assert recorded == ["02"]
    assert [(row.key, row.kept) for row in result.sections] == [("02", False)]


def test_an_edit_to_one_scene_records_that_section_and_keeps_its_neighbour(project, monkeypatch):
    """Both sections play the same file, so only a key cut per scene can leave section 01 alone."""
    drive(monkeypatch, project, {"01": a_log(), "02": a_log()})
    record(project)
    page = project.root / "deck" / "index.html"
    page.write_text(PAGE.replace("two", "TWO"), encoding="utf-8")
    recorded: list[str] = []
    real = record_stage.capture_section
    monkeypatch.setattr(
        record_stage, "capture_section", lambda p, b, job: (recorded.append(job.section.key), real(p, b, job))[1]
    )
    result = record(project)
    assert recorded == ["02"]
    assert [(row.key, row.kept) for row in result.sections] == [("01", True), ("02", False)]

    # The head is outside every scene and reaches all of them, so an edit there records both.
    page.write_text(PAGE.replace("two", "TWO").replace("#fff", "#eee"), encoding="utf-8")
    recorded.clear()
    again = record(project)
    assert recorded == ["01", "02"]
    assert [(row.key, row.kept) for row in again.sections] == [("01", False), ("02", False)]


def test_the_stale_reason_names_what_moved(project, monkeypatch):
    section = project.page_sections[0]
    assert record_stage.stale_recording(project, section) == "section 01 has no recording"
    drive(monkeypatch, project, {"01": a_log(), "02": a_log()})
    record(project)
    assert record_stage.stale_recording(project, section) is None
    (project.root / "deck" / "index.html").write_text(PAGE.replace("one", "ONE"), encoding="utf-8")
    assert record_stage.stale_recording(project, section) == (
        "section 01: its scene, the page around it, its assets, its words or its cues changed since it was recorded"
    )
    Takes(script="script.md", model="m", output_format="mp3").save(project.takes_path)
    assert record_stage.stale_recording(project, section) == "section 01 has no narration span yet"


# The scenes are markup rather than script, because a real page holds every scene of a film and the
# browser tests below are what prove one scene's edit records one section.
BROWSER_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body style="background:#fff">
<img src="panel.png" style="position:absolute;left:0;top:0;width:640px;height:360px">
<script src="decktalk-runtime.js"></script>
<div data-scene="1"><template data-slide="1.1"><p data-cue="1.1a">a</p></template></div>
<div data-scene="2"><template data-slide="2.1"><p data-cue="2.1a">b</p></template></div>
</body></html>"""

BROWSER_TOML = """
[project]
name = "skip"

[video]
width = 640
height = 360

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""


def panel(root, colour: bytes) -> None:
    """A one-pixel PNG of one colour, which the page loads beside itself."""
    from decktalk.media import ffmpeg

    ffmpeg.run("-y", "-f", "lavfi", "-i", f"color=c=0x{colour.decode()}:s=64x64", "-frames:v", "1", str(root))


@pytest.fixture
def recorded_project(tmp_path):
    """A two-section project on one page that loads a picture beside it, narrated without voice."""
    import shutil

    from decktalk.stages.narrate import narrate
    from decktalk.toolchain.assets import RUNTIME_FILE, runtime_path

    (tmp_path / "decktalk.toml").write_text(BROWSER_TOML, encoding="utf-8")
    (tmp_path / "script.md").write_text("## 1. A\n\nOne two three.\n\n## 2. B\n\nFour five six.\n", encoding="utf-8")
    (tmp_path / "cues.json").write_text("{}", encoding="utf-8")
    deck = tmp_path / "deck"
    deck.mkdir()
    (deck / "index.html").write_text(BROWSER_PAGE, encoding="utf-8")
    shutil.copyfile(runtime_path(), deck / RUNTIME_FILE)
    panel(deck / "panel.png", b"00ff00")
    project = Project.load(tmp_path, environ={})
    narrate(project, silent=True)
    return Project.load(tmp_path, environ={})


@pytest.mark.browser
@pytest.mark.media
def test_a_run_keeps_a_section_whose_picture_did_not_change_and_records_one_whose_did(recorded_project):
    """The page file never changes here, so only the asset list can tell the two runs apart."""
    project = recorded_project
    first = record(project)
    assert [row.kept for row in first.sections] == [False, False]
    logs = {row.key: row.log for row in first.sections}
    assert "deck/panel.png" in logs["01"].assets and logs["01"].input_hash
    keys = {row.key: project.recording(row.section).stat().st_mtime_ns for row in first.sections}

    kept = record(project)
    assert [(row.key, row.kept) for row in kept.sections] == [("01", True), ("02", True)]
    assert {row.key: project.recording(row.section).stat().st_mtime_ns for row in kept.sections} == keys

    panel(project.root / "deck" / "panel.png", b"0000ff")
    again = record(project)
    assert [(row.key, row.kept) for row in again.sections] == [("01", False), ("02", False)]
    assert again.sections[0].log.input_hash != logs["01"].input_hash


@pytest.mark.browser
@pytest.mark.media
def test_an_edit_to_one_scene_records_that_section_and_keeps_the_other(recorded_project):
    """Both sections play one page, so a slide edit must cost one recording and not the whole film."""
    project = recorded_project
    first = record(project)
    assert [row.kept for row in first.sections] == [False, False]
    before = {row.key: row.log.input_hash for row in first.sections}
    page = project.root / "deck" / "index.html"

    page.write_text(BROWSER_PAGE.replace(">b<", ">B<"), encoding="utf-8")
    again = record(project)
    assert [(row.key, row.kept) for row in again.sections] == [("01", True), ("02", False)]
    assert again.sections[0].log.input_hash == before["01"]
    assert again.sections[1].log.input_hash != before["02"]

    # The body's own style sits outside both scenes and paints behind both, so it records both.
    page.write_text(BROWSER_PAGE.replace(">b<", ">B<").replace("#fff", "#eee"), encoding="utf-8")
    shared = record(project)
    assert [(row.key, row.kept) for row in shared.sections] == [("01", False), ("02", False)]


@pytest.mark.browser
@pytest.mark.media
def test_each_section_log_is_on_disk_before_the_next_section_is_opened(recorded_project, monkeypatch):
    project = recorded_project
    seen: list[tuple[str, list[str]]] = []
    real = record_stage.capture_section

    def capture_section(p, browser, job):
        seen.append((job.section.key, sorted(f.name for f in project.recordings_dir.glob("*.json"))))
        return real(p, browser, job)

    monkeypatch.setattr(record_stage, "capture_section", capture_section)
    record(project)
    # Section 01's whole log, measurement and checks included, is readable while 02 is still recording.
    assert seen == [("01", []), ("02", ["01.json"])]
    finished = json.loads((project.recordings_dir / "01.json").read_text(encoding="utf-8"))
    assert finished["t0_seconds"] is not None and finished["checks"]["verdicts"] == []
