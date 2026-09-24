"""Everything a viewer receives beside the picture: captions, chapters, the transcript and the poster."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Cut, Cuts, RecordingLog, Words
from decktalk.captions import CaptionCue
from decktalk.errors import ToolError
from decktalk.media import browser
from decktalk.media.pagereport import CueRow, PageReport, MeasuredScene
from decktalk.page import Q
from decktalk.results import SectionKind, Substitute, Word
from decktalk.stages.assemble.cut import cut_list
from decktalk.stages.assemble.publish import (
    SOUND_CAPTION_SECONDS,
    build_captions,
    build_chapters,
    caption_texts,
    clip_captions,
    clip_speech,
    cut_note,
    described_cues,
    one_at_a_time,
    poster_query,
    publish,
    render_poster,
    scene_slides,
    sound_captions,
    transcript_sections,
    uncaptioned_sounds,
    with_sound_captions,
)

from .conftest import MID_CLIP_TOML, TITLED_TOML

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")


def a_log(inputs, section: int, cues: tuple[CueRow, ...]) -> None:
    """One recording log on disk, carrying what the page said about the cues it ran."""
    RecordingLog(
        section=section,
        url="http://project.localhost/deck/index.html",
        input_hash="h",
        requested_seconds=2.0,
        settle_seconds=0.1,
        load_seconds=0.1,
        clock_start_seconds=0.2,
        report=PageReport(cues=cues),
    ).write(inputs.workspace.recording_log(f"{section:02d}"))


# ---- the transcript ---------------------------------------------------------------------------


def test_described_cues_sort_on_the_time_alone(tmp_path, write_project):
    """Two lines at one cue used to break the tie on their first letter, which printed a step back
    before the arrival it belongs to. The runtime composes one sentence per cue in document order,
    so the second is the whole of the rule."""
    inputs = write_project(tmp_path)
    a_log(
        inputs,
        1,
        (
            CueRow(id="1.1:a", due=1.0, ran=1.0, describe="the arrival"),
            CueRow(id="1.1:b", due=1.0, ran=1.0, describe="a step back"),
        ),
    )
    assert described_cues(inputs, 1, 0.0) == ((1.0, "the arrival"), (1.0, "a step back"))


def test_described_cues_are_placed_where_their_section_plays(tmp_path, write_project):
    inputs = write_project(tmp_path)
    a_log(inputs, 1, (CueRow(id="1.1:a", due=0.5, ran=0.75, describe="the reveal"),))
    assert described_cues(inputs, 1, 4.0) == ((4.75, "the reveal"),)


def test_a_cue_that_described_nothing_is_not_in_the_transcript(tmp_path, write_project):
    inputs = write_project(tmp_path)
    a_log(inputs, 1, (CueRow(id="1.1:a", due=1.0, ran=1.0), CueRow(id="1.1:b", due=2.0, ran=2.0, describe="  ")))
    assert described_cues(inputs, 1, 0.0) == ()


def test_a_section_that_was_never_recorded_describes_nothing(tmp_path, write_project):
    inputs = write_project(tmp_path)
    assert described_cues(inputs, 1, 0.0) == ()


def test_a_note_says_what_plays_where_nothing_was_said():
    clip = Cut(section=2, key="02", kind=SectionKind.CLIP, start=0.0, end=1.0, source=Path("media/b.mp4"), chapter="c")
    page = Cut(section=1, key="01", kind=SectionKind.PAGE, start=0.0, end=1.0, source=Path("a.webm"), chapter="c")
    slated = clip.model_copy(update={"substitute": Substitute.SLATE})
    assert cut_note(clip) == "A clip plays here: media/b.mp4."
    assert cut_note(page) == ""
    assert cut_note(slated) == "A placeholder slate frame plays here."


def test_sections_that_share_a_chapter_share_one_transcript_entry(tmp_path, write_project, rendered):
    inputs = write_project(tmp_path, TITLED_TOML)
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    entries = transcript_sections(inputs, cut_list(inputs, rows), {1: "hello", 3: "again"})
    assert [entry.chapter for entry in entries] == ["Open", "The edit", "Close"]
    # The clip and the page that share "The edit" are one heading with two paragraphs under it.
    assert len(entries[1].said) == 2


def test_a_clip_that_names_a_words_file_is_read_into_the_transcript(tmp_path, write_project):
    inputs = write_project(tmp_path, TITLED_TOML)
    (tmp_path / "media").mkdir()
    Words(words=(Word(word="spoken", start=0.0, end=0.4),)).write(tmp_path / "media" / "before.words.json")
    assert clip_speech(inputs, 2) == "spoken"
    assert clip_speech(inputs, 1) == ""


# ---- chapters ---------------------------------------------------------------------------------


def test_consecutive_sections_with_one_title_share_one_chapter_marker(tmp_path, write_project, rendered):
    inputs = write_project(tmp_path, TITLED_TOML)
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    chapters = build_chapters(rows, inputs.chapters())
    assert [chapter.title for chapter in chapters] == ["Open", "The edit", "Close"]
    assert (chapters[1].start, chapters[1].end) == (2.0, 7.5)


# ---- captions ---------------------------------------------------------------------------------


def test_every_spoken_section_is_captioned_where_its_narration_plays(tmp_path, write_project, take_index, spoken):
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta")), 2: ("B", 2.0, 1.6, spoken("gamma delta"))})
    cues = build_captions(inputs, takes, {1: 0.0, 2: 0.0}, caption_texts(inputs, takes))
    assert cues
    assert cues[0].start == 0.0
    # The second section's take starts where the first one ends on the narration clock.
    assert any(cue.start >= 2.0 for cue in cues)


def test_a_clips_captions_count_from_its_own_start_and_stop_at_its_picture(tmp_path, write_project, open_run, rendered):
    inputs = write_project(tmp_path, TITLED_TOML)
    (tmp_path / "media").mkdir()
    Words(
        words=(
            Word(word="inside", start=0.0, end=0.4),
            Word(word="past", start=9.0, end=9.4),
        )
    ).write(tmp_path / "media" / "before.words.json")
    opened = open_run(tmp_path)
    rows = rendered(inputs, {1: 2.0, 2: 3.0}, audio={2: tmp_path / "media" / "before.mov"})
    cues = clip_captions(inputs, opened.run, rows)
    assert [line for cue in cues for line in cue.lines] == ["inside"]
    assert cues[0].start == 2.0


def test_a_clip_whose_words_file_is_missing_is_said_and_plays_uncaptioned(tmp_path, write_project, open_run, rendered):
    inputs = write_project(tmp_path, TITLED_TOML)
    opened = open_run(tmp_path)
    rows = rendered(inputs, {1: 2.0, 2: 3.0}, audio={2: tmp_path / "media" / "before.mov"})
    assert clip_captions(inputs, opened.run, rows) == []
    assert any("plays with no captions" in note for note in opened.notes())


def test_the_take_index_lends_the_captions_the_scripts_own_spelling(tmp_path, write_project, take_index, spoken):
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    assert caption_texts(inputs, takes) == {1: "alpha beta"}


def test_a_section_the_index_never_spoke_falls_back_to_the_script(tmp_path, write_project, take_index, spoken):
    inputs = write_project(tmp_path)
    (tmp_path / "script.md").write_text("## 1. One\n\nWritten words.\n\n## 2. Two\n\nMore.\n", encoding="utf-8")
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha"))})
    texts = caption_texts(
        inputs, takes.model_copy(update={"sections": (takes.sections[0].model_copy(update={"spoken": ""}),)})
    )
    assert texts[1] == "Written words."


# ---- sound captions ----------------------------------------------------------------------------


def test_a_cued_sound_that_names_no_caption_is_said(tmp_path, write_project, open_run):
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n"
        '[[mix.effects]]\nfile = "media/ping.mp3"\nsection = 1\ncue = "1.1:ping"\n'
    )
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    uncaptioned_sounds(inputs, opened.run)
    assert opened.notes() == [
        "The sound media/ping.mp3 plays at cue '1.1:ping' in section 1 and names no caption, "
        "so the captions never say it plays."
    ]


def test_a_sounds_caption_is_bracketed_and_placed_at_its_resolved_cue(tmp_path, write_project, cue_times):
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n"
        '[[mix.effects]]\nfile = "media/ping.mp3"\nsection = 1\ncue = "1.1:ping"\ncaption = "ball bounces"\n'
    )
    inputs = write_project(tmp_path, toml)
    cue_times(inputs, {1: {"1.1:ping": 0.5}})
    cues = sound_captions(inputs, {1: 4.0})
    assert cues == [CaptionCue(start=4.5, end=4.5 + SOUND_CAPTION_SECONDS, lines=("[ball bounces]",))]


def test_a_sound_joins_the_caption_it_shares_its_moment_with():
    speech = [CaptionCue(start=0.0, end=2.0, lines=("a line",))]
    sound = [CaptionCue(start=1.0, end=2.0, lines=("[ping]",))]
    assert with_sound_captions(speech, sound) == [CaptionCue(start=0.0, end=2.0, lines=("a line", "[ping]"))]


def test_a_sound_with_room_to_itself_keeps_its_own_caption():
    speech = [CaptionCue(start=0.0, end=1.0, lines=("a line",))]
    sound = [CaptionCue(start=4.0, end=5.0, lines=("[ping]",))]
    assert with_sound_captions(speech, sound) == [*speech, *sound]


def test_no_two_captions_are_ever_on_screen_at_once():
    overlapping = [
        CaptionCue(start=0.0, end=3.0, lines=("first",)),
        CaptionCue(start=1.0, end=4.0, lines=("second",)),
        CaptionCue(start=4.0, end=4.0, lines=("no room",)),
    ]
    kept = one_at_a_time(overlapping)
    # The first is cut back to where the second opens, and a cue with no room left is not written.
    assert [cue.lines for cue in kept] == [("first",), ("second",)]
    assert kept[0].end == 1.0


# ---- the poster --------------------------------------------------------------------------------


def test_a_scene_names_its_slides_in_the_order_the_page_declares_them():
    declared = MeasuredScene.model_validate({"scene": "1", "elements": {"1.2": []}, "slides": ["1.1", "1.2"]})
    measured_only = MeasuredScene(scene="2", elements={"2.1": ()})
    assert scene_slides((declared,), "1") == ("1.1", "1.2")
    assert scene_slides((measured_only,), "2") == ("2.1",)
    assert scene_slides((declared,), "9") == ()


def test_the_poster_freezes_the_opening_slide_with_its_reveals_fired(tmp_path, write_project):
    inputs = write_project(tmp_path)
    catalog = (MeasuredScene.model_validate({"scene": "1", "elements": {}, "slides": ["1.1", "1.2"]}),)
    assert poster_query(catalog, inputs.document.page_sections[0]) == {Q.SLIDE: "1.1"}
    assert poster_query((), inputs.document.page_sections[0]) is None


def test_a_refusal_from_the_media_layer_costs_the_film_nothing(tmp_path, write_project, open_run, monkeypatch):
    """Nothing here may cost a film that is already written, so a refusal is a sentence and no poster."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)

    def refuse(_path: str = "") -> None:
        raise ToolError("could not launch a browser.")

    monkeypatch.setattr(browser, "chromium", refuse)
    assert render_poster(inputs, opened.run, tmp_path / "poster.png") is None
    assert any("could not be drawn" in note for note in opened.notes())


def test_a_project_with_no_page_section_draws_no_poster(tmp_path, write_project, open_run):
    toml = "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/a.mp4'\n"
    inputs = write_project(tmp_path, toml)
    assert render_poster(inputs, open_run(tmp_path).run, tmp_path / "poster.png") is None


# ---- publishing ---------------------------------------------------------------------------------


def test_the_film_appears_in_one_step_and_a_stamped_copy_is_made_only_when_asked(tmp_path, write_project, monkeypatch):
    inputs = write_project(tmp_path)
    paths = inputs.workspace.deliverables()
    inputs.workspace.final_dir.mkdir(parents=True)
    paths["chapters"].write_text(";FFMETADATA1\n", encoding="utf-8")
    work = inputs.workspace.final_dir / ".t.tmp.mp4"

    def run(*args: str) -> None:
        Path(args[-1]).write_bytes(b"a film")

    monkeypatch.setattr("decktalk.media.ffmpeg.run", run)
    work.write_bytes(b"a film")
    assert publish(inputs, work, paths) is None
    assert inputs.workspace.film.read_bytes() == b"a film"
    assert not work.exists()


def test_a_render_that_produced_nothing_is_never_published(tmp_path, write_project, monkeypatch):
    inputs = write_project(tmp_path)
    paths = inputs.workspace.deliverables()
    inputs.workspace.final_dir.mkdir(parents=True)
    paths["chapters"].write_text(";FFMETADATA1\n", encoding="utf-8")
    work = inputs.workspace.final_dir / ".t.tmp.mp4"
    monkeypatch.setattr("decktalk.media.ffmpeg.run", lambda *args: Path(args[-1]).write_bytes(b""))
    work.write_bytes(b"")
    with pytest.raises(ToolError):
        publish(inputs, work, paths)
    assert not inputs.workspace.film.exists()


def test_the_cut_list_a_transcript_reads_carries_every_section(tmp_path, write_project, rendered):
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    cuts: Cuts = cut_list(inputs, rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5}))
    assert len(cuts.sections) == 4
    assert cuts.at(2.5).section == 2
