"""The URL a section is opened at, and what its recording is keyed on.

Two rules are held here. Every query key is a member of the contract's own vocabulary, so a page URL
cannot be built by hand and a param naming a key no page reads is refused. And the key a skip is
decided by moves with the scene, the assets and the motion, and stays still when another scene of
the same page is edited.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes, RecordingLog
from decktalk.errors import InputError
from decktalk.inputs import Inputs, PageSection
from decktalk.media.pagereport import PageReport
from decktalk.page import Q
from decktalk.results import CueTime, SectionCues, Word
from decktalk.stages.record.capture import (
    as_query,
    page_parts,
    plan_job,
    scene_params,
    scene_url,
    section_hash,
    words_param,
)
from support.projects import write_project

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"

[[section]]
number = 3
clip = "media/broll.mp4"
words = "media/broll.words.json"

[mix]
music = "media/bed.mp3"
slate = "media/slate.png"

[[mix.effects]]
file = "media/chime.wav"
section = 1
cue = "1.1:open"
"""

PAGE = """<!doctype html><html><body>
<div data-scene="1"><template data-slide="1.1"><p data-in="open">one</p></template></div>
<div data-scene="2"><template data-slide="2.1"><p data-in="open">two</p></template></div>
</body></html>
"""


def a_project(tmp_path: Path, toml: str = TOML, page: str = PAGE) -> Inputs:
    tmp_path.mkdir(parents=True, exist_ok=True)
    write_project(tmp_path, toml)
    deck = tmp_path / "deck"
    deck.mkdir(exist_ok=True)
    (deck / "index.html").write_text(page, encoding="utf-8")
    return Inputs.load(tmp_path, environ={})


def section_of(inputs: Inputs, number: int) -> PageSection:
    found = inputs.document.section(number)
    assert isinstance(found, PageSection)
    return found


def cue_times() -> CueTimes:
    row = CueTime(cue="1.1:open", phrase="one", seconds=1.5, offset=0.0)
    return CueTimes(sections=(SectionCues(section=1, key="01", estimated=False, cues=(row,)),))


def test_a_param_naming_a_key_no_page_reads_is_refused() -> None:
    with pytest.raises(InputError, match="nope"):
        as_query({"nope": "1"})


def test_a_param_naming_a_contract_key_becomes_that_key() -> None:
    assert as_query({"hud": "1"}) == {Q.HUD: "1"}


def test_the_resolved_cues_join_the_query(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    params = scene_params(section_of(inputs, 1), cue_times())
    assert params[Q.CUES] == "1.1:open@1.5"


def test_a_section_that_sets_its_own_cues_keeps_them(tmp_path: Path) -> None:
    toml = TOML.replace('scene = "1"\n', 'scene = "1"\nparams = { cues = "1.1:open@9" }\n', 1)
    inputs = a_project(tmp_path, toml)
    assert scene_params(section_of(inputs, 1), cue_times())[Q.CUES] == "1.1:open@9"


def test_the_page_url_carries_the_scene_and_the_recorder_signal(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    url = scene_url(inputs, section_of(inputs, 1), scene_params(section_of(inputs, 1), cue_times()))
    assert url.startswith("http://project.localhost/deck/index.html?")
    assert "scene=1" in url
    assert "t0=signal" in url


def test_a_section_whose_page_is_not_there_is_refused(tmp_path: Path) -> None:
    write_project(tmp_path, TOML)
    inputs = Inputs.load(tmp_path, environ={})
    with pytest.raises(InputError, match="deck/index.html"):
        scene_url(inputs, section_of(inputs, 1), {})


def test_a_word_never_carries_the_separators_the_query_uses() -> None:
    value = words_param((Word(word="one,two@three", start=0.0, end=0.4),))
    assert value == "onetwothree@0.00"


def test_no_words_is_no_query_value() -> None:
    assert words_param(()) is None


def test_a_page_is_cut_into_the_scene_a_section_plays_and_the_part_every_scene_shares() -> None:
    parts = page_parts(PAGE, "1")
    assert 'data-slide="1.1"' in parts.scene
    # Every scene is cut out of the shared part, so an edit inside scene two moves scene two's key
    # and no other section's, which is the whole reason the page is keyed in two pieces.
    assert 'data-slide="2.1"' not in parts.scene
    assert "data-scene" not in parts.shared
    assert "<!doctype html>" in parts.shared


def test_a_page_whose_scene_never_closes_is_shared_whole() -> None:
    parts = page_parts('<div data-scene="1"><p>one</p>', "1")
    assert parts.scene == ""
    assert "data-scene" in parts.shared


def test_editing_one_scene_moves_only_the_sections_that_play_it(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    one, two = section_of(inputs, 1), section_of(inputs, 2)
    before = (
        section_hash(inputs, one, "url", 10.0, ()),
        section_hash(inputs, two, "url", 10.0, ()),
    )
    edited = a_project(tmp_path, page=PAGE.replace("two</p>", "two and a half</p>"))
    after = (
        section_hash(edited, one, "url", 10.0, ()),
        section_hash(edited, two, "url", 10.0, ()),
    )
    assert before[0] == after[0]
    assert before[1] != after[1]


def test_the_motion_a_render_asks_for_joins_the_key(tmp_path: Path) -> None:
    plain = a_project(tmp_path)
    reduced = a_project(tmp_path / "other", TOML + "\n[motion]\nreduce = true\n")
    assert section_hash(plain, section_of(plain, 1), "url", 10.0, ()) != section_hash(
        reduced, section_of(reduced, 1), "url", 10.0, ()
    )


def test_a_swapped_asset_moves_the_key_although_no_markup_changed(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    picture = tmp_path / "deck" / "one.png"
    picture.write_bytes(b"first")
    before = section_hash(inputs, section_of(inputs, 1), "url", 10.0, ("deck/one.png",))
    picture.write_bytes(b"second")
    assert section_hash(inputs, section_of(inputs, 1), "url", 10.0, ("deck/one.png",)) != before


def test_a_job_with_no_recording_on_disk_has_not_been_made(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    job = plan_job(inputs, section_of(inputs, 1), cue_times(), 10.0)
    assert not job.unchanged
    assert job.out == inputs.workspace.recording("01")


def test_a_log_with_no_narration_start_in_it_is_a_recording_that_never_finished(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    job = plan_job(inputs, section_of(inputs, 1), cue_times(), 10.0)
    job.out.parent.mkdir(parents=True, exist_ok=True)
    job.out.write_bytes(b"webm")
    RecordingLog(
        section=1,
        url="http://project.localhost/deck/index.html",
        input_hash=job.input_hash,
        requested_seconds=10.0,
        settle_seconds=0.5,
        load_seconds=0.2,
        clock_start_seconds=1.5,
        report=PageReport(),
    ).write(job.log_path)
    assert not plan_job(inputs, section_of(inputs, 1), cue_times(), 10.0).unchanged
