"""Everything a viewer receives beside the picture: captions, chapters, the transcript and the poster."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import ClipWords, Placement, Placements, SpokenWord, Words, words_beside
from decktalk.captions import CAPTION_MAX_CHARS, CaptionCue, Said, srt_text, vtt_text
from decktalk.errors import ErrorCode, InputError, ToolError
from decktalk.inputs import Inputs
from decktalk.media import browser, ffmpeg, pages
from decktalk.media.pagereport import Box, CueRow, Landing, MeasuredScene, MotionLane, PageReport, Size
from decktalk.page import Q
from decktalk.results import SectionKind, Substitute, Word
from decktalk.settings import BY_ID
from decktalk.stages.assemble.cut import placements_of, rendered_starts
from decktalk.stages.assemble.mix import embedded_sounds
from decktalk.stages.assemble.publish import (
    SOUND_CAPTION_SECONDS,
    build_captions,
    build_chapters,
    clip_captions,
    clip_words,
    described_cues,
    film_words,
    inner_words,
    narrated_turns,
    one_at_a_time,
    placement_note,
    poster_query,
    publish,
    render_poster,
    sound_captions,
    spoken_cues,
    transcript_sections,
    uncaptioned_sounds,
    with_sound_captions,
)
from support.fakes import Browsed
from support.pages import a_report, write_log
from support.takes import a_take, narrated

from .conftest import (
    MID_CLIP_TOML,
    PAGES_TOML,
    TITLED_TOML,
    cue_times,
    open_run,
    rendered,
    spoken,
    take_index,
    with_panes,
    write_project,
)

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")


# ---- the transcript ---------------------------------------------------------------------------


def test_described_cues_sort_on_the_time_alone(tmp_path):
    """Two lines at one cue keep document order, never their first letter, so a step never prints
    before the arrival it belongs to. The runtime composes one sentence per cue in document order."""
    inputs = write_project(tmp_path)
    arrival = CueRow(id="1.1:a", due=1.0, ran=1.0, describe="the arrival")
    back = CueRow(id="1.1:b", due=1.0, ran=1.0, describe="a step back")
    write_log(inputs, 1, report=PageReport(cues=(arrival, back)))
    assert described_cues(inputs, 1, 0.0) == ((1.0, "the arrival"), (1.0, "a step back"))


def test_described_cues_are_placed_where_their_section_plays(tmp_path):
    inputs = write_project(tmp_path)
    write_log(inputs, 1, report=PageReport(cues=(CueRow(id="1.1:a", due=0.5, ran=0.75, describe="the reveal"),)))
    assert described_cues(inputs, 1, 4.0) == ((4.75, "the reveal"),)


def test_a_cue_that_described_nothing_is_not_in_the_transcript(tmp_path):
    inputs = write_project(tmp_path)
    blank = CueRow(id="1.1:b", due=2.0, ran=2.0, describe="  ")
    write_log(inputs, 1, report=PageReport(cues=(CueRow(id="1.1:a", due=1.0, ran=1.0), blank)))
    assert described_cues(inputs, 1, 0.0) == ()


def test_a_section_that_was_never_recorded_describes_nothing(tmp_path):
    inputs = write_project(tmp_path)
    assert described_cues(inputs, 1, 0.0) == ()


def test_a_note_says_what_plays_where_nothing_was_said():
    clip = Placement(
        section=2, key="02", kind=SectionKind.CLIP, start=0.0, end=1.0, source=Path("media/b.mp4"), chapter="c"
    )
    page = Placement(section=1, key="01", kind=SectionKind.PAGE, start=0.0, end=1.0, source=Path("a.mp4"), chapter="c")
    slated = clip.model_copy(update={"substitute": Substitute.SLATE})
    assert placement_note(clip) == "A clip plays here: media/b.mp4."
    assert placement_note(page) == ""
    assert placement_note(slated) == "A slate plays here."


def test_sections_that_share_a_chapter_share_one_transcript_entry(tmp_path):
    inputs = write_project(tmp_path, TITLED_TOML)
    takes = take_index(inputs, {1: ("Open", 2.0, 1.6, spoken("hello")), 3: ("The edit", 2.5, 2.0, spoken("again"))})
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    entries = transcript_sections(inputs, placements_of(inputs, rows), takes)
    assert [entry.chapter for entry in entries] == ["Open", "The edit", "Close"]
    # The clip and the page that share "The edit" are one heading with two paragraphs under it.
    assert len(entries[1].said) == 2


TWO_VOICES = PAGES_TOML + '\n[voices.narrator]\nid = "n"\n\n[voices.guest]\nid = "g"\n'
"""The two-section project with a guest beside its narrator."""


def test_each_turn_of_a_section_in_two_voices_is_a_paragraph_naming_its_voice(tmp_path):
    """A reader who cannot hear the film learns who said each line, and a section of one voice is left as it was."""
    inputs = write_project(tmp_path, TWO_VOICES)
    first = a_take(1, seconds=1.0, spoken="Says who?", voice="guest", digest="0000000000000001")
    second = a_take(1, turn=2, seconds=1.0, spoken="Nobody does.", digest="0000000000000002")
    alone = a_take(2, seconds=1.0, spoken="One voice. Two turns.", digest="0000000000000003")
    again = a_take(2, turn=2, seconds=1.0, spoken="Still one.", digest="0000000000000004")
    narrated(inputs, first, second, alone, again)
    takes = inputs.takes(required=True)
    rows = rendered(inputs, {1: 2.0, 2: 2.0})
    entries = transcript_sections(inputs, placements_of(inputs, rows), takes)
    said = [one for entry in entries for one in entry.said]
    assert said == [
        Said("Says who?", voice="guest"),
        Said("Nobody does.", voice="narrator"),
        Said("One voice. Two turns. Still one."),
    ]


@pytest.mark.parametrize("named", [False, True])
def test_captions_name_the_voice_of_a_dialogue_only_when_the_project_asks(tmp_path, named):
    """Words alone by default, and the voice of every turn of a two-voice section once `caption_voices` is on."""
    asks = "\n[output]\ncaption_voices = true\n" if named else ""
    inputs = write_project(tmp_path, TWO_VOICES + asks)
    first = a_take(1, seconds=1.0, spoken="alpha beta", tail_seconds=0.3, voice="guest", digest="0000000000000001")
    second = a_take(1, turn=2, seconds=1.0, spoken="gamma delta", digest="0000000000000002")
    alone = a_take(2, seconds=1.0, spoken="epsilon", digest="0000000000000003")
    words = {(1, 1): spoken("alpha beta"), (1, 2): spoken("gamma delta"), 2: spoken("epsilon")}
    narrated(inputs, first, second, alone, words=words)
    takes = inputs.takes(required=True)
    cues = build_captions(inputs, narrated_turns(inputs, takes, {1: 0.0, 2: 0.0}))
    expected = [True, True, False] if named else [False, False, False]
    assert [cue.named for cue in cues] == expected
    assert ("guest: alpha beta" in srt_text(cues)) is named


def test_every_spoken_caption_carries_its_voice_and_turn_whether_or_not_the_files_name_them(tmp_path):
    """A page draws the captioned copy and places a caption by who says it, so the cue knows that without naming it."""
    inputs = write_project(tmp_path, TWO_VOICES)
    first = a_take(1, seconds=1.0, spoken="alpha beta", tail_seconds=0.3, voice="guest", digest="0000000000000001")
    second = a_take(1, turn=2, seconds=1.0, spoken="gamma delta", digest="0000000000000002")
    alone = a_take(2, seconds=1.0, spoken="epsilon", digest="0000000000000003")
    words = {(1, 1): spoken("alpha beta"), (1, 2): spoken("gamma delta"), 2: spoken("epsilon")}
    narrated(inputs, first, second, alone, words=words)
    takes = inputs.takes(required=True)
    cues = build_captions(inputs, narrated_turns(inputs, takes, {1: 0.0, 2: 0.0}))
    assert [(cue.voice, cue.turn, cue.named) for cue in cues] == [
        ("guest", 1, False),
        ("narrator", 2, False),
        ("narrator", 1, False),
    ]
    assert "guest" not in srt_text(cues) and "<v " not in vtt_text(cues)


def test_a_named_voice_leaves_room_on_the_line_for_its_name(tmp_path):
    """The .srt writes the name before the line, so the line is wrapped short enough to carry it."""
    inputs = write_project(tmp_path, TWO_VOICES + "\n[output]\ncaption_voices = true\n")
    said = "one two three four five six seven eight nine"  # 44 characters: two lines either way
    near = "one two three four five six seven eight"  # 39 characters: one line alone, two beside a name
    first = a_take(1, seconds=1.0, spoken=said, tail_seconds=0.3, digest="0000000000000001")
    second = a_take(1, turn=2, seconds=1.0, spoken=near, voice="guest", digest="0000000000000002")
    narrated(inputs, first, second, words={(1, 1): spoken(said), (1, 2): spoken(near)})
    takes = inputs.takes(required=True)
    cues = build_captions(inputs, narrated_turns(inputs, takes, {1: 0.0}))
    assert all(cue.named and len(f"{cue.voice}: {cue.lines[0]}") <= CAPTION_MAX_CHARS for cue in cues)
    assert len(cues[-1].lines) == 2


def test_a_sound_before_a_voiced_line_stays_outside_the_voice():
    speech = CaptionCue(start=1.2, end=3.0, lines=("Says who?",), voice="guest", turn=1, named=True)
    sound = CaptionCue(start=1.0, end=1.0 + SOUND_CAPTION_SECONDS, lines=("[door opens]",))
    (joined,) = with_sound_captions([speech], [sound])
    assert joined.lines == ("[door opens]", "Says who?") and joined.voice == "guest" and joined.spoken == (1, 2)


def test_a_clip_s_speech_is_read_into_the_transcript_in_the_section_it_plays_in(tmp_path):
    """The transcript reads the speech inside every clip the captions read, one paragraph naming nobody."""
    inputs = write_project(tmp_path, TITLED_TOML)
    (tmp_path / "media").mkdir()
    Words(words=(Word(word="spoken", start=0.0, end=0.4),)).write(tmp_path / "media" / "before.words.json")
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5}, audio={2: tmp_path / "media" / "before.mov"})
    inside = clip_words(inputs, open_run(tmp_path).run, rows)
    takes = take_index(inputs, {1: ("Open", 2.0, 1.6, spoken("hello")), 3: ("The edit", 2.5, 2.0, spoken("again"))})
    entries = transcript_sections(inputs, placements_of(inputs, rows), takes, inside=inside)
    edited = entries[1]
    assert edited.chapter == "The edit"
    assert edited.said[0] == Said("spoken") and edited.said[1].note


def test_a_clip_words_file_that_does_not_read_is_the_authors_input_and_is_never_deleted(tmp_path):
    """The author keeps the file beside the clip, so DeckTalk cannot build it again and never says to delete it."""
    inputs = write_project(tmp_path, TITLED_TOML)
    (tmp_path / "media").mkdir()
    path = tmp_path / "media" / "before.words.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError) as refused:
        rows = rendered(inputs, {1: 2.0, 2: 3.0}, audio={2: tmp_path / "media" / "before.mov"})
        clip_captions(inputs, open_run(tmp_path).run, rows)
    said = f"{refused.value} {refused.value.hint}"
    assert refused.value.code is ErrorCode.INPUT
    assert str(refused.value).startswith("media/before.words.json, the words beside the clip of section 2,")
    assert "the words of one clip: it is not JSON" in str(refused.value)
    assert "elete" not in said
    assert refused.value.location is not None and refused.value.location.file == Path("media/before.words.json")
    assert path.read_text(encoding="utf-8") == "{not json"


# ---- the speech inside a pane clip ------------------------------------------------------------


def said(word: str, start: float, voice: str | None = None, turn: int | None = None) -> SpokenWord:
    return SpokenWord(word=word, start=start, end=round(start + 0.3, 3), voice=voice, turn=turn)


def test_a_clip_s_words_move_to_its_cue_and_stop_where_it_stops_playing() -> None:
    """A word that starts once the clip has stopped is not heard, and one running past its stop ends there."""
    words = ClipWords(words=(said("One.", 0.2, "a", 1), said("Two.", 1.9, "a", 1), said("Three.", 2.5, "a", 1)))
    moved = inner_words(words, 10.0, 2.0)
    assert [(word.word, word.start, word.end, word.voice) for word in moved] == [
        ("One.", 10.2, 10.5, "a"),
        ("Two.", 11.9, 12.0, "a"),
    ]


def test_a_clip_s_captions_are_one_run_per_voice_and_turn_and_name_nobody() -> None:
    words = (said("Hello.", 0.0, "a", 1), said("Hi.", 0.4, "b", 2), said("Bye.", 0.8, "b", 2))
    cues = spoken_cues(words)
    assert [(cue.lines, cue.voice, cue.turn, cue.named) for cue in cues] == [
        (("Hello.",), "a", 1, False),
        (("Hi. Bye.",), "b", 2, False),
    ]
    assert "<v " not in vtt_text(cues)


def test_a_pane_clip_s_words_show_over_the_span_the_mix_plays_its_sound(tmp_path, monkeypatch):
    """The captions and the mix read one span, so a pane clip's words stop where its sound stops."""
    inputs = write_project(tmp_path, PAGES_TOML)
    clip = tmp_path / "deck" / "clips" / "film.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"film")
    ClipWords(words=(said("Heard.", 0.2, "a", 1), said("Cut.", 1.7, "a", 1))).write(words_beside(clip))
    monkeypatch.setattr(ffmpeg, "has_audio", lambda _path: True)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: 9.0)
    lane = MotionLane(
        id="clip-1", kind="clip", box=Box(x=0, y=0, w=960, h=540), size=Size(w=960, h=540), departs=0.8,
        arrives=2.4, landings=(Landing(cue="2.1:play", kind="depart", at=0.8),), clip="deck/clips/film.mp4",
    )  # fmt: skip
    write_log(inputs, 2, report=a_report(motions=[lane]), requested_seconds=2.4, assets=["deck/clips/film.mp4"])
    rows = with_panes(inputs, rendered(inputs, {1: 2.0, 2: 2.5, 3: 1.5}))
    (words,) = clip_words(inputs, open_run(tmp_path).run, rows)
    (sound,) = embedded_sounds(rows, rendered_starts(rows))
    assert [(word.word, word.start) for word in words] == [("Heard.", 3.0)]
    assert sound.at == 2.8 and words[-1].end <= sound.at + sound.seconds


def test_a_film_s_words_are_its_narration_s_and_every_clip_s_in_the_order_they_start() -> None:
    narration = {1: []}
    inside = [(said("later", 3.0),), (said("sooner", 1.0),)]
    assert [word.word for word in film_words(narration, inside).words] == ["sooner", "later"]


# ---- chapters ---------------------------------------------------------------------------------


def test_consecutive_sections_with_one_title_share_one_chapter_marker(tmp_path):
    inputs = write_project(tmp_path, TITLED_TOML)
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5})
    chapters = build_chapters(rows, inputs.chapters())
    assert [chapter.title for chapter in chapters] == ["Open", "The edit", "Close"]
    assert (chapters[1].start, chapters[1].end) == (2.0, 7.5)


# ---- captions ---------------------------------------------------------------------------------


def test_every_spoken_section_is_captioned_where_its_narration_plays(tmp_path):
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta")), 2: ("B", 2.0, 1.6, spoken("gamma delta"))})
    cues = build_captions(inputs, narrated_turns(inputs, takes, {1: 0.0, 2: 0.0}))
    assert cues
    assert cues[0].start == 0.0
    # The second section's take starts where the first one ends in the joined narration.
    assert any(cue.start >= 2.0 for cue in cues)


def test_a_clips_captions_count_from_its_own_start_and_stop_at_its_picture(tmp_path):
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


def test_a_clip_with_no_words_file_beside_it_plays_uncaptioned(tmp_path):
    """A clip whose speech nobody wrote down adds no caption, as a pane clip with no words beside it adds none."""
    inputs = write_project(tmp_path, TITLED_TOML)
    opened = open_run(tmp_path)
    rows = rendered(inputs, {1: 2.0, 2: 3.0}, audio={2: tmp_path / "media" / "before.mov"})
    assert clip_captions(inputs, opened.run, rows) == []


# ---- sound captions ----------------------------------------------------------------------------


def test_a_cued_sound_that_names_no_caption_is_said(tmp_path):
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n"
        '[[mix.effect]]\nfile = "media/ping.mp3"\nsection = 1\ncue = "1.1:ping"\n'
    )
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    uncaptioned_sounds(inputs, opened.run)
    assert opened.notes() == [
        "The sound media/ping.mp3 plays at cue '1.1:ping' in section 1 and names no caption, "
        "so the captions never say it plays."
    ]


def test_a_sounds_caption_is_bracketed_and_placed_at_its_resolved_cue(tmp_path):
    toml = (
        "[project]\nname = 't'\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n"
        '[[mix.effect]]\nfile = "media/ping.mp3"\nsection = 1\ncue = "1.1:ping"\ncaption = "ball bounces"\n'
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


def test_the_poster_freezes_the_opening_slide_with_its_reveals_fired(tmp_path):
    inputs = write_project(tmp_path)
    catalog = (MeasuredScene.model_validate({"scene": "1", "elements": {}, "slides": ["1.1", "1.2"]}),)
    assert poster_query(catalog, inputs.document.page_sections[0]) == {Q.FREEZE: "1.1"}
    assert poster_query((), inputs.document.page_sections[0]) is None


def test_a_refusal_from_the_media_layer_costs_the_film_nothing(tmp_path, monkeypatch):
    """Nothing here may cost a film that is already written, so a refusal is a sentence and no poster."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)

    def refuse(_path: str = "", **_named: str) -> None:
        raise ToolError("could not launch a browser.")

    monkeypatch.setattr(browser, "chromium", refuse)
    assert render_poster(inputs, opened.run, tmp_path / "poster.png") is None
    assert any("could not be drawn" in note for note in opened.notes())


def test_an_unchanged_poster_is_read_back_without_a_browser(tmp_path, monkeypatch):
    """A build with nothing changed should not launch Chromium to draw the same picture again."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    browsed = Browsed().install(monkeypatch)
    catalog = (MeasuredScene.model_validate({"scene": "1", "elements": {}, "slides": ["1.1"]}),)
    browsed.reports["deck/index.html"] = PageReport(catalog=catalog)
    browsed.assets.paths.append("deck/index.html")
    documents: list[object] = []

    def open_page(*_a: object, **named: object) -> tuple[object, object]:
        documents.append(named.get("documents"))
        return browsed.page.page(), browsed.assets

    monkeypatch.setattr(pages, "open_page", open_page)
    out = inputs.workspace.deliverables()["poster"]
    assert render_poster(inputs, opened.run, out) == out
    out.unlink()
    assert render_poster(inputs, opened.run, out) == out
    assert browsed.policies == ["trusted"]
    assert out.read_bytes() == b"png"
    assert browsed.stills == ["http://project.localhost/deck/index.html?freeze=1.1"]
    # The poster page answers the cue-times alias from memory, as every other frozen page does.
    assert documents == [inputs.documents()]
    (tmp_path / "deck" / "index.html").write_text("<html>edited</html>", encoding="utf-8")
    render_poster(inputs, opened.run, out)
    assert browsed.policies == ["trusted", "trusted"]


def test_a_project_with_no_page_section_draws_no_poster(tmp_path):
    toml = "[project]\nname = 't'\n[[section]]\nnumber = 1\nclip = 'media/a.mp4'\n"
    inputs = write_project(tmp_path, toml)
    assert render_poster(inputs, open_run(tmp_path).run, tmp_path / "poster.png") is None


# ---- publishing ---------------------------------------------------------------------------------


def test_the_film_appears_in_one_step_and_a_stamped_copy_is_made_only_when_asked(tmp_path, monkeypatch):
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


def test_a_render_that_produced_nothing_is_never_published(tmp_path, monkeypatch):
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


def test_the_placements_a_transcript_reads_carry_every_section(tmp_path):
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    placements: Placements = placements_of(inputs, rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5}))
    assert len(placements.sections) == 4
    assert [row.section for row in placements.sections if row.start <= 2.5 < row.end] == [2]


def test_an_untrusted_project_draws_its_poster_untrusted(tmp_path, monkeypatch):
    """The poster opens the author's page, so it is sandboxed exactly as the recording of that page is."""
    write_project(tmp_path)
    inputs = Inputs.load(tmp_path, environ={BY_ID["record.page_policy"].environment: "untrusted"})
    opened = open_run(tmp_path)
    launched: list[str] = []

    def refuse(_path: str = "", *, policy: str, **_launch: object) -> None:
        launched.append(policy)
        raise ToolError("could not launch a browser.")

    monkeypatch.setattr(browser, "chromium", refuse)
    render_poster(inputs, opened.run, tmp_path / "poster.png")
    assert launched == ["untrusted"]


def test_a_caption_never_spans_two_turns(tmp_path):
    """Two voices' sentences a short gap apart are two captions, the way two sections' are."""
    inputs = write_project(tmp_path, PAGES_TOML + '\n[voices.narrator]\nid = "n"\n\n[voices.guest]\nid = "g"\n')
    first = a_take(1, seconds=1.0, spoken="alpha beta", tail_seconds=0.3, digest="0000000000000001")
    second = a_take(1, turn=2, voice="guest", seconds=1.0, spoken="gamma delta", digest="0000000000000002")
    narrated(inputs, first, second, words={(1, 1): spoken("alpha beta"), (1, 2): spoken("gamma delta")})
    takes = inputs.takes(required=True)
    cues = build_captions(inputs, narrated_turns(inputs, takes, {1: 0.0}))
    assert [cue.lines for cue in cues] == [("alpha beta",), ("gamma delta",)]
    assert cues[1].start >= 1.3
