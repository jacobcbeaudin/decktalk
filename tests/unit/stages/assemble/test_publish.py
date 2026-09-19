"""Captions, chapters, the transcript and the poster: everything a viewer receives beside the picture."""

from __future__ import annotations

import json

from decktalk.artifacts import RecordingLog, Take, Takes, Word, write_words
from decktalk.model import Project


def test_captions_and_chapters_skip_over_a_clip_between_page_sections(tmp_path, mid_clip_plan):
    from decktalk.stages.assemble.cut import rendered_starts
    from decktalk.stages.assemble.mix import narration_offsets
    from decktalk.stages.assemble.publish import build_captions, build_chapters

    p, takes, rows = mid_clip_plan(tmp_path)
    cues = build_captions(p, takes, narration_offsets(rows, takes, rendered_starts(rows)))
    assert [(c.text, c.start) for c in cues] == [("alpha beta", 0.7), ("gamma delta", 5.1), ("epsilon", 7.6)]
    assert all(c.end <= 2.0 or c.start >= 5.0 for c in cues)  # nothing is captioned over the clip
    assert cues[0].end <= 2.0
    chapters = build_chapters(rows, p.chapters())
    assert [(c.start, c.end, c.title) for c in chapters] == [
        (0.0, 2.0, "Section 1"),
        (2.0, 5.0, "Section 2"),
        (5.0, 7.52, "Section 3"),
        (7.52, 9.0, "Section 4"),
    ]


def test_build_captions_shift_every_section_to_where_its_narration_plays(
    tmp_path, spoken, write_project, pages_toml, take_index
):
    """The narration may start well into the film, and no cue may cross from one section into the next."""
    from decktalk.stages.assemble.publish import build_captions

    p = Project.load(write_project(tmp_path, pages_toml), environ={})
    takes = take_index(
        p,
        {
            "01": ("a", 2.0, None, spoken("alpha beta", 0.1)),
            "02": ("b", 2.0, None, spoken("gamma delta", 0.1)),
        },
    )
    shifted = build_captions(p, takes, 3.0)  # narration starts three seconds into the final file
    assert [c.text for c in shifted] == ["alpha beta", "gamma delta"]
    assert shifted[0].start == 3.1 and shifted[1].start == 5.1


def test_build_captions_uses_the_take_index_spoken_text(tmp_path, write_project, pages_toml):
    from decktalk.stages.assemble.publish import build_captions, caption_texts

    root = write_project(tmp_path, pages_toml)
    p = Project.load(root, environ={})
    p.takes_dir.mkdir(parents=True)
    write_words(p.takes_dir / "01-a.words.json", [Word("hello", 0.5, 0.9), Word("there", 1.0, 1.4)])
    m = Takes(script="script.md", model="m", output_format="mp3")
    m.sections["01"] = Take(1, "A", "01-a.mp3", "01-a.words.json", "h", 2, 1.0, 2.0, spoken="Hello, there.")
    m.save(p.takes_path)
    # No script.md exists, so the text can only come from the take index.
    assert caption_texts(p, m) == {"01": "Hello, there."}
    assert [c.text for c in build_captions(p, m, 0.0, caption_texts(p, m))] == ["Hello, there."]
    # A take index written before the field existed falls back to the script.
    raw = json.loads(p.takes_path.read_text(encoding="utf-8"))
    del raw["sections"]["01"]["spoken"]
    p.takes_path.write_text(json.dumps(raw), encoding="utf-8")
    (root / "script.md").write_text(
        "## 1. A\n\nHello there!\n\n## 2. B\n\nTwo.\n\n## 3. C\n\nThree.\n", encoding="utf-8"
    )
    reloaded = Takes.load(p.takes_path)
    assert reloaded is not None and caption_texts(p, reloaded)["01"] == "Hello there!"


def test_clip_captions_place_the_clip_speech_at_the_clip_start(tmp_path, caplog, titled_clip_rows):
    from decktalk.stages.assemble.publish import clip_captions

    p, rows = titled_clip_rows(tmp_path)
    (tmp_path / "media").mkdir()
    words = [Word("Watch", 0.2, 0.5), Word("it.", 0.6, 0.9), Word("One.", 1.4, 1.7), Word("Late.", 3.1, 3.4)]
    write_words(tmp_path / "media" / "before.words.json", words)
    cues = clip_captions(p, rows)
    assert [(c.text, c.start) for c in cues] == [("Watch it. One.", 2.2)]  # the word after the picture ends is dropped
    assert cues[0].end <= 5.0
    # A slate plays no speech, so it gets no captions.
    _p, slate_rows = titled_clip_rows(tmp_path, clip_audio=False)
    assert clip_captions(p, slate_rows) == []
    # A missing words file warns and captions nothing.
    (tmp_path / "media" / "before.words.json").unlink()
    assert clip_captions(p, rows) == []
    assert "the words file media/before.words.json is missing" in caplog.text


def test_consecutive_sections_with_the_same_title_share_one_chapter(tmp_path, titled_clip_rows):
    from decktalk.stages.assemble.publish import build_chapters

    p, rows = titled_clip_rows(tmp_path)
    assert [(c.start, c.end, c.title) for c in build_chapters(rows, p.chapters())] == [
        (0.0, 2.0, "Open"),
        (2.0, 7.52, "The edit"),
        (7.52, 9.0, "Close"),
    ]


SFX_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"

[[mix.sfx]]
file = "media/tick.mp3"
section = 1
cue = "1.1bounce"
caption = "ball bounces"

[[mix.sfx]]
file = "media/hum.mp3"
section = 1
cue = "1.1hum"

[[mix.sfx]]
file = "media/gone.mp3"
section = 2
cue = "2.1nope"
caption = "[a sound nobody placed]"
"""


def test_a_cued_sound_that_names_a_caption_gets_its_own_cue(tmp_path, write_project):
    """Captions carry the whole soundtrack, not only the dialogue, which is what SC 1.2.2 asks."""
    import json

    from decktalk.stages.assemble.publish import sound_captions

    root = write_project(tmp_path, SFX_TOML)
    p = Project.load(root, environ={})
    p.cue_times_path.parent.mkdir(parents=True, exist_ok=True)
    rows = {"01": [{"cue": "1.1bounce", "on": "bounce", "at": 1.5}, {"cue": "1.1hum", "on": "hum", "at": 2.0}]}
    p.cue_times_path.write_text(json.dumps({"sections": rows}), encoding="utf-8")

    cues = sound_captions(p, {"01": 10.0, "02": 20.0})
    # The sound with no caption says nothing, and the one whose cue never resolved has nowhere to sit.
    assert [(c.start, c.end, c.text) for c in cues] == [(11.5, 12.5, "[ball bounces]")]
    assert sound_captions(p, {}) == []


def test_a_cued_sound_with_no_caption_is_reported_rather_than_dropped_in_silence(tmp_path, write_project):
    """A caption track that leaves a sound out is a hole a deaf viewer cannot know about."""
    from decktalk.stages.assemble.publish import uncaptioned_sounds
    from decktalk.verdicts import Verdict

    p = Project.load(write_project(tmp_path, SFX_TOML), environ={})
    [row] = uncaptioned_sounds(p)
    assert row.verdict is Verdict.NO_CAPTION and not row.verdict.certain
    assert (row.section, row.cue) == (1, "1.1hum")
    assert row.detail is not None and "names no caption" in row.detail
    assert row.where == "media/hum.mp3"


def test_the_transcript_says_what_plays_where_nothing_is_spoken(tmp_path, write_project):
    from decktalk.artifacts import Cut, Cuts
    from decktalk.pipeline import SectionKind, Substitute
    from decktalk.stages.assemble.publish import transcript_sections

    p = Project.load(write_project(tmp_path), environ={})
    cuts = Cuts(
        fps=25,
        total_seconds=9.0,
        sections=[
            Cut(1, SectionKind.PAGE, 0.0, 4.0, "build/recordings/01.webm", "Open"),
            Cut(2, SectionKind.CLIP, 4.0, 6.0, "media/broll.mp4", "B-roll"),
            Cut(3, SectionKind.PAGE, 6.0, 9.0, "build/recordings/03.webm", "Close", substitute=Substitute.BLACK),
        ],
    )
    sections = transcript_sections(p, cuts, {"01": "A bowl. A ball."})
    assert [s.chapter for s in sections] == ["Open", "B-roll", "Close"]
    assert [(x.text, x.note) for x in sections[0].said] == [("A bowl. A ball.", False)]
    assert [(x.text, x.note) for x in sections[1].said] == [("A clip plays here: media/broll.mp4.", True)]
    assert [(x.text, x.note) for x in sections[2].said] == [("A placeholder black frame plays here.", True)]


def test_consecutive_sections_of_one_chapter_share_one_transcript_heading(tmp_path, write_project):
    """The chapter markers group the film, so the transcript a viewer reads instead is grouped the same."""
    from decktalk.artifacts import Cut, Cuts
    from decktalk.pipeline import SectionKind
    from decktalk.stages.assemble.publish import transcript_sections

    p = Project.load(write_project(tmp_path), environ={})
    cuts = Cuts(
        fps=25,
        total_seconds=9.0,
        sections=[
            Cut(1, SectionKind.PAGE, 0.0, 3.0, "build/recordings/01.webm", "The edit"),
            Cut(2, SectionKind.CLIP, 3.0, 6.0, "media/broll.mp4", "The edit"),
            Cut(3, SectionKind.PAGE, 6.0, 9.0, "build/recordings/03.webm", "Close"),
        ],
    )
    sections = transcript_sections(p, cuts, {"01": "One.", "03": "Three."})
    assert [s.chapter for s in sections] == ["The edit", "Close"]
    # Section 2 spoke nothing, so its note sits between the two sections it plays between.
    assert [(x.text, x.note) for x in sections[0].said] == [
        ("One.", False),
        ("A clip plays here: media/broll.mp4.", True),
    ]
    assert (sections[0].start, sections[0].end) == (0.0, 6.0)
    assert [x.text for x in sections[1].said] == ["Three."]


def test_the_poster_freezes_the_opening_slide_with_every_reveal_fired(tmp_path, write_project):
    """A cue-driven slide before its first cue is an empty stage, which sells nothing."""
    from decktalk.stages.assemble.publish import poster_query

    toml = "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = 1\n\n[section.params]\ntheme = 'dark'\n"
    p = Project.load(write_project(tmp_path, toml), environ={})
    section = p.page_sections[0]
    catalog = [{"scene": "1", "slides": ["1.1", "1.2"], "cues": {"1.1": ["1.1bowl", "1.1ball"], "1.2": []}}]
    # The section's own params ride with the slide, because the poster is the picture the film opens on.
    assert poster_query(catalog, section) == {"theme": "dark", "slide": "1.1"}
    assert poster_query([{"scene": "9", "slides": ["9.1"], "cues": {"9.1": []}}], section) is None
    assert poster_query(None, section) is None


def test_a_sound_that_lands_under_speech_joins_that_cue_and_one_in_a_silence_stands_alone():
    """Two overlapping cues are drawn twice or dropped, and an effect is cued to a spoken word."""
    from decktalk.captions import CaptionCue
    from decktalk.stages.assemble.publish import with_sound_captions

    speech = [CaptionCue(2.879, 8.328, ("A bowl.",)), CaptionCue(12.0, 13.5, ("A ball.",))]
    sounds = [CaptionCue(7.24, 8.24, ("[a ball lands]",)), CaptionCue(10.0, 11.0, ("[a tick lands]",))]
    cues = with_sound_captions(speech, sounds)
    assert [c.lines for c in cues] == [("A bowl.", "[a ball lands]"), ("[a tick lands]",), ("A ball.",)]
    assert [(c.start, c.end) for c in cues] == [(2.879, 8.328), (10.0, 11.0), (12.0, 13.5)]


def test_no_caption_runs_into_the_one_after_it():
    """A widened host cue and a free-standing sound are both cut back, so two are never on screen."""
    from decktalk.captions import CaptionCue
    from decktalk.stages.assemble.publish import with_sound_captions

    speech = [CaptionCue(2.0, 4.0, ("One.",)), CaptionCue(4.0, 6.0, ("Two.",))]
    widened = with_sound_captions(speech, [CaptionCue(3.5, 4.5, ("[a tick lands]",))])
    assert [(c.start, c.end) for c in widened] == [(2.0, 4.0), (4.0, 6.0)]
    assert widened[0].lines == ("One.", "[a tick lands]")
    apart = with_sound_captions([CaptionCue(9.0, 10.0, ("Two.",))], [CaptionCue(4.5, 5.5, ("[a tick lands]",))])
    assert [(c.start, c.end) for c in apart] == [(4.5, 5.5), (9.0, 10.0)]


def test_a_caption_with_no_room_left_to_it_is_not_written_at_all():
    """A cue whose end is its start is dropped by every player, so it is never written as one."""
    from decktalk.captions import CaptionCue
    from decktalk.stages.assemble.publish import one_at_a_time

    # The second cue starts where the first does, so cutting the first back leaves it no length.
    assert one_at_a_time([CaptionCue(1.0, 3.0, ("a",)), CaptionCue(1.0, 2.0, ("b",))]) == [CaptionCue(1.0, 2.0, ("b",))]
    # A cue the next one starts inside is cut back to it rather than dropped.
    assert one_at_a_time([CaptionCue(1.0, 3.0, ("a",)), CaptionCue(2.0, 4.0, ("b",))]) == [
        CaptionCue(1.0, 2.0, ("a",)),
        CaptionCue(2.0, 4.0, ("b",)),
    ]


def test_a_sound_with_no_room_of_its_own_joins_the_cue_after_it_rather_than_flashing():
    """A caption is read or it is not written, so a sliver and a cue of no length are both refused."""
    from decktalk.captions import CaptionCue
    from decktalk.stages.assemble.publish import SOUND_CAPTION_SECONDS, with_sound_captions

    speech = [CaptionCue(5.0, 6.0, ("Two.",))]
    crowded = with_sound_captions(speech, [CaptionCue(4.5, 5.5, ("[a tick lands]",))])
    assert [(c.start, c.end, c.lines) for c in crowded] == [(5.0, 6.0, ("[a tick lands]", "Two."))]
    together = with_sound_captions([], [CaptionCue(3.0, 4.0, ("[one]",)), CaptionCue(3.0, 4.0, ("[two]",))])
    assert [(c.start, c.end, c.lines) for c in together] == [(3.0, 4.0, ("[one]", "[two]"))]
    assert all(c.end - c.start >= SOUND_CAPTION_SECONDS for c in crowded + together)


def test_a_reveal_the_page_described_reaches_the_transcript(tmp_path, write_project):
    """The runtime writes its own description beside each cue it ran, and the transcript is where it lands."""
    from decktalk.stages.assemble.publish import described_cues

    p = Project.load(write_project(tmp_path), environ={})
    log = RecordingLog(
        url="http://project.localhost/deck/index.html",
        requested_seconds=6.0,
        settle_seconds=0.2,
        load_seconds=0.4,
        clock_start_seconds=0.5,
        cue_log=[
            {"id": "1.1bowl", "due": 2.0, "ran": 2.04, "frame": 51, "describe": "A bowl appears.", "after": None},
            {"id": "1.1ball", "due": 3.0, "ran": 3.08, "frame": 77, "describe": "  ", "next": None},
            {"id": "1.2sum", "due": 4.0, "ran": None, "frame": None, "describe": "The sum lands."},
        ],
    )
    p.recordings_dir.mkdir(parents=True, exist_ok=True)
    log.save(p.workspace.recording_log("01"))
    assert described_cues(p, "01", 10.0) == ((12.04, "A bowl appears."), (14.0, "The sum lands."))
    assert described_cues(p, "02", 0.0) == ()


def test_the_timestamped_copy_is_written_only_when_the_project_asks_for_it(tmp_path, write_project, monkeypatch):
    """A build wrote a dated second mp4 unasked, so the key is off by default and is read here."""
    import dataclasses
    from importlib import import_module

    from decktalk.stages.assemble.publish import publish

    # The package re-exports `publish` under its module's own name, so the module is fetched by name.
    module = import_module("decktalk.stages.assemble.publish")

    p = Project.load(write_project(tmp_path, SFX_TOML), environ={})
    p.out_dir.mkdir(parents=True, exist_ok=True)
    work = p.out_dir / "work.mp4"

    def place(source, chapters_file, out, language):
        out.write_bytes(b"a film")

    monkeypatch.setattr(module, "mux_chapters", place)
    paths = {"chapters": p.out_dir / "c.txt"}
    paths["chapters"].write_text(";FFMETADATA1\n", encoding="utf-8")

    work.write_bytes(b"a film")
    assert publish(p, work, [], paths) is None, "the copy is off unless the project turns it on"
    assert [f.name for f in p.out_dir.glob("*.mp4")] == [p.final.name]

    on = dataclasses.replace(p.settings, output=dataclasses.replace(p.settings.output, timestamped_copy=True))
    object.__setattr__(p, "settings", on)
    work.write_bytes(b"a film")
    stamped = publish(p, work, [], paths)
    assert stamped is not None and stamped.exists() and stamped != p.final
    assert stamped.name.startswith(f"{p.name}-") and stamped.suffix == ".mp4"
