"""The URL a page section is opened at, and the retries the capture makes when frames stall."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from decktalk.artifacts import CueTime, CueTimes, RecordingLog, Timeline, TimelineSection, Word
from decktalk.errors import ConfigError
from decktalk.media.origin import ORIGIN
from decktalk.model import PageSection, Project
from decktalk.settings import RecordConfig
from decktalk.stages.record import capture as capture_module
from decktalk.stages.record.capture import (
    capture_section,
    plan_job,
    prev_words_query,
    scene_params,
    scene_url,
    words_param,
    words_query,
)

TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
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
            "01": TimelineSection("A", 0.0, 3.0, 3.0, 2.5, [Word("one,", 0.7, 1.0), Word("two", 1.5, 1.8)]),
            "02": TimelineSection("B", 3.0, 6.0, 3.0, 5.5, [Word("three", 3.4, 3.8)]),
        },
    ).save(p.timeline_path)
    return p


def test_the_words_of_a_section_count_from_where_that_section_starts():
    sec = TimelineSection("B", 3.0, 6.0, 3.0, 5.5, [Word("three", 3.4, 3.8)])
    assert words_param(sec) == "three@0.40"
    assert words_param(TimelineSection("B", 3.0, 6.0, 3.0, 5.5, [])) is None


def test_a_section_is_opened_on_the_local_origin_with_its_words_and_the_previous_sections(project):
    first, second = PageSection(1, "deck/index.html", "1"), PageSection(2, "deck/index.html", "2")
    assert prev_words_query(project, first) is None
    assert prev_words_query(project, second) == words_query(project, first) == "one@0.70,two@1.50"
    url = scene_url(project, second, {})
    assert url.startswith(f"{ORIGIN}/deck/index.html?")
    query = parse_qs(urlsplit(url).query)
    assert query["words"] == ["three@0.40"] and query["prevwords"] == ["one@0.70,two@1.50"]
    assert query["scene"] == ["2"] and query["t0"] == ["signal"]
    assert "prevwords" not in parse_qs(urlsplit(scene_url(project, first, {})).query)


def test_a_missing_page_names_the_section_and_the_file(project):
    with pytest.raises(ConfigError, match="section 1: page not found"):
        scene_url(project, PageSection(1, "deck/gone.html", "1"), {})


def test_the_resolved_cues_ride_along_unless_the_section_sets_them_itself():
    section = PageSection(1, "deck/index.html", "1")
    cue_times = CueTimes(sections={"01": [CueTime(cue="1.1a", on="a", at=1.5)]})
    assert scene_params(section, cue_times) == {"cues": "1.1a@1.5"}
    assert scene_params(section, None) == {}
    own = PageSection(1, "deck/index.html", "1", params={"cues": "mine@0"})
    assert scene_params(own, cue_times) == {"cues": "mine@0"}


def test_a_job_names_the_files_the_section_will_be_written_to(project):
    section = project.page_sections[0]
    job = plan_job(project, section, None, 4.25)
    assert job.seconds == 4.25 and job.section is section
    assert job.out == project.recordings_dir / "01.webm"
    assert job.log_path == project.recordings_dir / "01.json"


def a_log(stall_ms: int) -> RecordingLog:
    recording_log = RecordingLog(url="u", requested_seconds=1, settle_seconds=0, load_seconds=0, clock_start_seconds=0)
    recording_log.frame_gaps = [(1.0, stall_ms)]
    return recording_log


def test_a_section_whose_frames_stalled_is_recorded_again(project, monkeypatch, caplog):
    stalls = [900, 900, 10]
    calls: list[float] = []

    def record_page(browser, url, seconds, out, **kwargs):
        calls.append(seconds)
        return a_log(stalls[len(calls) - 1])

    monkeypatch.setattr(capture_module, "record_page", record_page)
    project = replace(project, settings=replace(project.settings, record=RecordConfig(retries=2)))
    job = plan_job(project, project.page_sections[0], None, 4.0)
    with caplog.at_level("WARNING", logger="decktalk"):
        recording_log = capture_section(project, object(), job)
    assert calls == [4.0, 4.0, 4.0] and recording_log.worst_stall_ms == 10
    assert len(caplog.records) == 2


def test_the_capture_gives_up_after_the_last_retry(project, monkeypatch):
    monkeypatch.setattr(capture_module, "record_page", lambda *a, **k: a_log(900))
    project = replace(project, settings=replace(project.settings, record=RecordConfig(retries=0)))
    job = plan_job(project, project.page_sections[0], None, 4.0)
    assert capture_section(project, object(), job).worst_stall_ms == 900


def test_the_capture_opens_the_project_root_so_the_page_can_load_its_own_files(project, monkeypatch):
    seen: dict[str, Path] = {}

    def record_page(browser, url, seconds, out, *, root, **kwargs):
        seen["root"] = root
        return a_log(0)

    monkeypatch.setattr(capture_module, "record_page", record_page)
    capture_section(project, object(), plan_job(project, project.page_sections[0], None, 1.0))
    assert seen["root"] == project.root


def test_scene_params_adds_cues_unless_the_section_sets_them():
    cue_times = CueTimes({"01": [CueTime("a", "x", 1.5), CueTime("b", "y", 2.0)]})
    own = PageSection(1, "deck/index.html", "1", params={"theme": "dark"})
    assert scene_params(own, cue_times) == {"theme": "dark", "cues": "a@1.5,b@2.0"}
    assert scene_params(own, None) == {"theme": "dark"}
    fixed = PageSection(1, "deck/index.html", "1", params={"cues": "x@1"})
    assert scene_params(fixed, cue_times) == {"cues": "x@1"}
    assert scene_params(PageSection(2, "deck/index.html", "2"), cue_times) == {}
