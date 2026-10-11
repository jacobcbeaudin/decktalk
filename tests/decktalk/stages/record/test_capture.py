"""What a section's recording is made from, part by part.

The parts a skip is decided by move with the scene, the shared page, the assets and the motion, stay
still when another scene of the same page is edited, and name what moved rather than everything that
could have.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes, RecordingLog, stored
from decktalk.errors import NotBuiltError
from decktalk.inputs import Inputs, PageSection, RenderSection
from decktalk.results import CueTime, SectionCues
from decktalk.stages.record.capture import (
    Part,
    moved,
    page_made_from,
    page_parts,
    page_source,
    part_key,
    plan_job,
    rendered_made_from,
    said,
)
from support.pages import TWO_SCENE_PAGE, a_recording
from support.projects import load_project

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

[mix]
music = "media/bed.mp3"
slate = "media/slate.png"

[[mix.effect]]
file = "media/chime.wav"
section = 1
cue = "1.1:open"
"""


def a_project(tmp_path: Path, toml: str = TOML, page: str = TWO_SCENE_PAGE) -> Inputs:
    return load_project(tmp_path, toml, page=page)


def section_of(inputs: Inputs, number: int) -> PageSection:
    found = inputs.document.section(number)
    assert isinstance(found, PageSection)
    return found


def render_of(inputs: Inputs, number: int) -> RenderSection:
    found = inputs.document.section(number)
    assert isinstance(found, RenderSection)
    return found


def cue_times() -> CueTimes:
    row = CueTime(id="1.1:open", phrase="one", seconds=1.5, nudge_seconds=0.0)
    return CueTimes(sections=(SectionCues(section=1, key="01", estimated=False, cues=(row,)),))


def test_a_page_is_cut_into_the_scene_a_section_plays_and_the_part_every_scene_shares() -> None:
    parts = page_parts(TWO_SCENE_PAGE, "1")
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


def made_from(inputs: Inputs, number: int, assets: tuple[str, ...] = ()) -> dict[str, str]:
    """What section `number` would be recorded from, with no cues and a ten-second length."""
    return page_made_from(inputs, section_of(inputs, number), None, 10.0, assets)


def test_editing_one_scene_moves_only_the_sections_that_play_it(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    before = made_from(inputs, 1), made_from(inputs, 2)
    edited = a_project(tmp_path, page=TWO_SCENE_PAGE.replace("two</p>", "two and a half</p>"))
    assert moved(before[0], made_from(edited, 1)) == ()
    assert moved(before[1], made_from(edited, 2)) == (part_key(Part.SCENE),)


def test_editing_the_part_every_scene_shares_moves_every_section_of_the_page(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    before = made_from(inputs, 1), made_from(inputs, 2)
    edited = a_project(tmp_path, page=TWO_SCENE_PAGE.replace("<body>", "<body><style>p{}</style>"))
    shared = (part_key(Part.PAGE, "deck/index.html"),)
    assert (moved(before[0], made_from(edited, 1)), moved(before[1], made_from(edited, 2))) == (shared, shared)
    assert said(shared) == "the part of deck/index.html every scene shares"


def test_the_motion_a_render_asks_for_joins_the_key(tmp_path: Path) -> None:
    plain = a_project(tmp_path)
    reduced = a_project(tmp_path / "other", TOML + "\n[motion]\nreduce = true\n")
    assert moved(made_from(plain, 1), made_from(reduced, 1)) == (part_key(Part.MOTION),)


def test_the_page_policy_a_render_runs_under_joins_the_key(tmp_path: Path) -> None:
    """A recording made trusted is never kept for an untrusted run, whose page may draw without its other origins."""
    trusted = a_project(tmp_path)
    sealed = Inputs.load(tmp_path, environ={}, machine={"record": {"page_policy": "untrusted"}})
    assert moved(made_from(trusted, 1), made_from(sealed, 1)) == (part_key(Part.POLICY),)


def test_a_swapped_asset_moves_the_key_although_no_markup_changed(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    picture = tmp_path / "deck" / "one.png"
    picture.write_bytes(b"first")
    before = made_from(inputs, 1, ("deck/one.png",))
    picture.write_bytes(b"second")
    assert said(moved(before, made_from(inputs, 1, ("deck/one.png",)))) == "the file deck/one.png"


def test_the_order_a_page_asked_for_its_files_in_is_not_part_of_the_key(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    for name in ("a.png", "b.png"):
        (tmp_path / "deck" / name).write_bytes(name.encode())
    assert made_from(inputs, 1, ("deck/a.png", "deck/b.png")) == made_from(inputs, 1, ("deck/b.png", "deck/a.png"))


def test_a_newer_engine_records_again_what_an_older_one_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording carries the recorder, the probe and the contract that made it, so an upgrade moves the key."""
    inputs = a_project(tmp_path)
    before = made_from(inputs, 1)
    monkeypatch.setattr(stored, "ENGINE_VERSION", "999.0.0")
    assert moved(before, made_from(inputs, 1)) == (part_key(Part.ENGINE),)


def test_a_line_names_what_a_change_caused_and_not_what_followed_from_it() -> None:
    """New words are timed again, so their times, the cues and the length move too, and only the words are named."""
    before = {"text:2.1": "a", "words:2.1": "a", "cues": "a", "length": "a", "scene": "a"}
    after = {"text:2.1": "b", "words:2.1": "b", "cues": "b", "length": "b", "scene": "a"}
    assert said(moved(before, after)) == "the words of turn 2.1"
    retimed = {**before, "words:2.1": "b", "cues": "b"}
    assert said(moved(before, retimed)) == "the word times of turn 2.1 and its cue times"


def test_a_turn_added_to_a_section_is_named_as_moved() -> None:
    assert said(moved({"text:2.1": "a"}, {"text:2.1": "a", "text:2.2": "b"})) == "the words of turn 2.2"


def test_a_job_with_no_recording_on_disk_has_not_been_made(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    job = plan_job(inputs, section_of(inputs, 1), cue_times(), 10.0)
    assert not job.unchanged
    assert job.out == inputs.workspace.recording("01")


def test_a_log_with_no_checks_in_it_is_a_recording_that_never_finished(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    job = plan_job(inputs, section_of(inputs, 1), cue_times(), 10.0)
    job.out.parent.mkdir(parents=True, exist_ok=True)
    job.out.write_bytes(b"mp4")
    RecordingLog(section=1, made_from=job.made_from, recording=a_recording(requested_seconds=10.0)).write(job.log_path)
    assert not plan_job(inputs, section_of(inputs, 1), cue_times(), 10.0).unchanged


def test_a_log_an_earlier_engine_wrote_is_recorded_again_and_refused_by_a_stage_that_reads_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The recorder rewrites its own log, so only a stage that needs the log as its input refuses it."""
    inputs = a_project(tmp_path)
    path = inputs.workspace.recording_log("01")
    path.parent.mkdir(parents=True)
    path.write_text('{"section": 1, "digest": "abc", "trim_seconds": 1.5}', encoding="utf-8")
    with caplog.at_level("INFO", logger="decktalk"):
        job = plan_job(inputs, section_of(inputs, 1), cue_times(), 10.0)
    assert job.previous is None and not job.unchanged
    assert any("01.json" in record.getMessage() and "built again" in record.getMessage() for record in caplog.records)
    with pytest.raises(NotBuiltError, match=r"01\.json is there and cannot be read"):
        inputs.recording_log("01")


def test_a_page_that_could_not_be_read_is_hashed_as_empty_and_says_so(tmp_path: Path, caplog) -> None:
    with caplog.at_level("DEBUG", logger="decktalk"):
        assert page_source(tmp_path / "gone.html") == ""
    [record] = [record for record in caplog.records if record.name == "decktalk.stages.record.capture"]
    assert "gone.html" in record.getMessage()


def test_a_render_command_naming_an_absolute_program_is_digested_by_its_words(tmp_path: Path) -> None:
    """Only a relative word naming a project file is digested by its content, so /usr/bin/true is not refused."""
    toml = TOML + '\n[[section]]\nnumber = 4\nrender_command = ["/usr/bin/true", "render/draw", "{out}"]\n'
    inputs = a_project(tmp_path, toml)
    (tmp_path / "render").mkdir()
    (tmp_path / "render" / "draw").write_text("one")
    before = rendered_made_from(inputs, render_of(inputs, 4), {})
    assert [key for key in before if key.startswith(Part.FILE.value)] == [part_key(Part.FILE, "render/draw")]
    (tmp_path / "render" / "draw").write_text("two")
    assert moved(before, rendered_made_from(inputs, render_of(inputs, 4), {})) == (part_key(Part.FILE, "render/draw"),)
