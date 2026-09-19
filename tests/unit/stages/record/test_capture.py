"""The URL a page section is opened at, and the retries the capture makes when frames stall."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from decktalk.artifacts import CueTime, CueTimes, RecordingLog, Take, Takes, Word, write_words
from decktalk.errors import ConfigError
from decktalk.media.origin import ORIGIN
from decktalk.model import PageSection, Project
from decktalk.settings import RecordConfig
from decktalk.stages.record import capture as capture_module
from decktalk.stages.record.capture import (
    PageParts,
    capture_section,
    page_parts,
    plan_job,
    prev_words_query,
    scene_params,
    scene_url,
    section_hash,
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


PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>body{background:#fff}</style></head>
<body>
<div data-scene="1" data-name="Open"><template data-slide="1.1"><p data-cue="1.1a">one</p></template></div>
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
    write_words(p.takes_dir / "h1.words.json", [Word("one,", 0.7, 1.0), Word("two", 1.5, 1.8)])
    write_words(p.takes_dir / "h2.words.json", [Word("three", 0.4, 0.8)])
    takes = Takes(script="script.md", model="m", output_format="mp3")
    takes.sections["01"] = Take(1, "A", "h1.mp3", "h1.words.json", "h1", 2, 3.0, 3.0, speech_end_seconds=2.5)
    takes.sections["02"] = Take(2, "B", "h2.mp3", "h2.words.json", "h2", 1, 3.0, 3.0, speech_end_seconds=2.5)
    takes.save(p.takes_path)
    return p


def test_the_words_of_a_section_count_from_where_that_section_starts():
    assert words_param([Word("three", 0.4, 0.8)]) == "three@0.40"
    assert words_param([]) is None


def test_a_section_is_opened_on_the_local_origin_with_its_words_and_the_previous_sections(project):
    """Only a seamless section opens on the picture before it, so only it is given those words."""
    first = PageSection(1, "deck/index.html", "1")
    second = PageSection(2, "deck/index.html", "2", seamless=True)
    assert prev_words_query(project, first) is None
    # Each word counts from the section start, which is 0.5 s of lead before the take's own times.
    assert prev_words_query(project, second) == words_query(project, first) == "one@1.20,two@2.00"
    url = scene_url(project, second, {})
    assert url.startswith(f"{ORIGIN}/deck/index.html?")
    query = parse_qs(urlsplit(url).query)
    assert query["words"] == ["three@0.90"] and query["prevwords"] == ["one@1.20,two@2.00"]
    assert query["scene"] == ["2"] and query["t0"] == ["signal"]
    assert "prevwords" not in parse_qs(urlsplit(scene_url(project, first, {})).query)


def test_a_section_that_opens_on_its_own_picture_carries_none_of_the_previous_sections_words(project):
    """Those words are in the recorded URL, so a section that reads none of them must not be keyed on them."""
    plain = PageSection(2, "deck/index.html", "2")
    assert prev_words_query(project, plain) is None
    assert "prevwords" not in parse_qs(urlsplit(scene_url(project, plain, {})).query)


def test_a_missing_page_names_the_section_and_the_file(project):
    with pytest.raises(ConfigError, match="section 1: page not found"):
        scene_url(project, PageSection(1, "deck/gone.html", "1"), {})


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


def test_a_job_is_keyed_on_the_url_its_length_and_the_files_the_page_loaded(project):
    section = project.page_sections[0]
    picture = project.root / "deck" / "panel.png"
    picture.write_bytes(b"first picture")
    url = scene_url(project, section, {})
    base = section_hash(project, section, url, 4.0, ["deck/panel.png"])

    assert section_hash(project, section, url, 4.0, ["deck/panel.png"]) == base
    assert section_hash(project, section, url, 4.5, ["deck/panel.png"]) != base  # a longer section
    assert section_hash(project, section, "other", 4.0, ["deck/panel.png"]) != base  # other cues or words
    assert section_hash(project, section, url, 4.0, []) != base  # the picture is no longer loaded

    # The page is untouched and only the file beside it changes, which no hash of markup would catch.
    picture.write_bytes(b"second picture")
    assert section_hash(project, section, url, 4.0, ["deck/panel.png"]) != base


def test_a_page_is_cut_into_the_scene_a_section_plays_and_the_part_every_scene_shares():
    """The slice is the [data-scene] element as the file spells it, which is what the runtime mounts."""
    one = page_parts(PAGE, "1")
    assert one.scene.startswith('<div data-scene="1" data-name="Open">') and one.scene.endswith("</div>")
    assert "one" in one.scene and "two" not in one.scene
    assert "one" not in one.shared and "two" not in one.shared
    assert "<style>body{background:#fff}</style>" in one.shared and "decktalk-runtime.js" in one.shared
    two = page_parts(PAGE, "2")
    assert "two" in two.scene and two.shared == one.shared


def test_a_page_whose_scenes_cannot_be_sliced_is_shared_whole():
    """A page in script, a scene that never closes and a scene that is not there all key on the file."""
    in_script = "<!doctype html><body><script>DeckTalk.scene(1, { slides: [] });</script></body>"
    assert page_parts(in_script, "1") == PageParts(scene="", shared=in_script)
    unclosed = '<body><div data-scene="1"><div>never closed</div></body>'
    assert page_parts(unclosed, "1") == PageParts(scene="", shared=unclosed)
    assert page_parts(PAGE, "9") == PageParts(scene="", shared=PAGE)


def test_markup_inside_a_script_is_not_mistaken_for_a_scene():
    """A render string holds the same attribute, and only the element the browser builds counts."""
    page = '<body><script>const s = `<div data-scene="9">x</div>`;</script><div data-scene="1">A</div></body>'
    assert page_parts(page, "9") == PageParts(scene="", shared=page)
    assert page_parts(page, "1").scene == '<div data-scene="1">A</div>'


def test_the_key_moves_for_the_scene_a_section_plays_and_for_what_every_scene_shares(project):
    """One page holds every scene of a film, so a slide edit must reach one section and no more."""
    page = project.root / "deck" / "index.html"
    first, second = project.page_sections

    def keys() -> list[str]:
        return [section_hash(project, s, "u", 4.0, ["deck/index.html"]) for s in (first, second)]

    before = keys()
    page.write_text(PAGE.replace("two", "TWO"), encoding="utf-8")
    scene = keys()
    assert scene[0] == before[0] and scene[1] != before[1]

    page.write_text(PAGE.replace("two", "TWO").replace("#fff", "#eee"), encoding="utf-8")
    shared = keys()
    assert shared[0] != scene[0] and shared[1] != scene[1]


def test_the_frame_geometry_and_the_colour_scheme_are_part_of_the_key(project):
    section = project.page_sections[0]
    url = scene_url(project, section, {})
    base = section_hash(project, section, url, 4.0, [])
    wider = replace(project, settings=replace(project.settings, video=replace(project.settings.video, width=1280)))
    assert section_hash(wider, section, url, 4.0, []) != base
    dark = replace(project, settings=replace(project.settings, record=RecordConfig(color_scheme="dark")))
    assert section_hash(dark, section, url, 4.0, []) != base


def test_a_job_is_unchanged_only_when_the_recording_is_there_finished_and_keyed(project):
    section = project.page_sections[0]
    assert not plan_job(project, section, None, 4.0).unchanged  # nothing recorded yet

    project.recordings_dir.mkdir(parents=True, exist_ok=True)
    project.recording(section).write_bytes(b"a recording")
    url = scene_url(project, section, {})
    recorded = RecordingLog(url=url, requested_seconds=4.0, settle_seconds=0, load_seconds=0, clock_start_seconds=0)
    recorded.assets = ["deck/index.html"]
    recorded.t0_seconds = 0.44
    recorded.input_hash = section_hash(project, section, url, 4.0, recorded.assets)
    recorded.save(project.recording_log(section))
    assert plan_job(project, section, None, 4.0).unchanged

    # A log from a run that never wrote a key is never taken for a match.
    recorded.input_hash = ""
    recorded.save(project.recording_log(section))
    assert not plan_job(project, section, None, 4.0).unchanged

    # Nor is a recording that was never measured, which is a run that stopped half way.
    recorded.input_hash = section_hash(project, section, url, 4.0, recorded.assets)
    recorded.t0_seconds = None
    recorded.save(project.recording_log(section))
    assert not plan_job(project, section, None, 4.0).unchanged


def test_scene_params_adds_cues_unless_the_section_sets_them():
    cue_times = CueTimes({"01": [CueTime("a", "x", 1.5), CueTime("b", "y", 2.0)]})
    own = PageSection(1, "deck/index.html", "1", params={"theme": "dark"})
    assert scene_params(own, cue_times) == {"theme": "dark", "cues": "a@1.5,b@2.0"}
    assert scene_params(own, None) == {"theme": "dark"}
    fixed = PageSection(1, "deck/index.html", "1", params={"cues": "x@1"})
    assert scene_params(fixed, cue_times) == {"cues": "x@1"}
    assert scene_params(PageSection(2, "deck/index.html", "2"), cue_times) == {}
