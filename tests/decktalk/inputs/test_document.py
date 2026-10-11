"""What the document derives from its own tables: the fade of every cut and the length of a dip."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.document import (
    ClipSection,
    Document,
    Mix,
    PageSection,
    Transition,
    content_keys,
    frame_dip,
)
from support.projects import MINIMAL_TOML, write_project


def test_fade_flags_follow_dips_and_page_fade_in(tmp_path):
    p = Inputs.load(
        write_project(tmp_path, MINIMAL_TOML + "\n[transition]\ndips = [[0, 1]]\npage_fades_in = true\n"), environ={}
    )
    flags = p.document.fade_flags
    assert flags["00"] == (False, True)  # clip fades out into the dip
    assert flags["01"] == (False, False)  # page fades itself in, with no dip after
    assert flags["02"] == (False, False)
    p2 = Inputs.load(write_project(tmp_path, MINIMAL_TOML), environ={})  # no dips key: every cut dips
    assert p2.document.fade_flags["01"] == (False, True)
    p3 = Inputs.load(write_project(tmp_path, MINIMAL_TOML + "\n[transition]\ndips = []\n"), environ={})
    assert set(p3.document.fade_flags.values()) == {(False, False)}  # an empty list is straight cuts


def test_frame_dip_quantizes_to_whole_frames():
    assert frame_dip(0.15, 25) == 0.16  # 3.75 frames rounds up to 4
    assert frame_dip(0.15, 30) == 0.1333  # 4.5 frames rounds to the even 4
    assert frame_dip(0.001, 25) == 0.04  # never shorter than one frame
    assert frame_dip(0.0, 25) == 0.0


def test_a_project_that_writes_no_value_gets_the_default_its_field_declares() -> None:
    """The parse call and the field spell every default once, so the two cannot disagree."""
    doc = Document.from_toml(
        {
            "section": [{"number": 1, "clip": "a.mp4"}, {"number": 2, "page": "deck/a.html"}],
            "mix": {},
            "transition": {},
        },
        default_name="t",
    )
    clip, page = doc.sections
    assert isinstance(clip, ClipSection) and clip.slate_seconds == ClipSection(number=1, clip="a.mp4").slate_seconds
    assert isinstance(page, PageSection) and page.hold_seconds == PageSection(1, "a", "1").hold_seconds
    assert doc.mix == Mix() and doc.transition == Transition()


def test_an_effect_row_and_a_section_bed_are_read_under_their_singular_names() -> None:
    """Arrays of tables are singular, like `[[section]]`, and a section asks for the bed `with_ambience`."""
    doc = Document.from_toml(
        {
            "section": [{"number": 1, "page": "deck/a.html", "scene": "1", "with_ambience": True}],
            "mix": {"effect": [{"file": "media/tick.wav", "section": 1, "cue": "now"}]},
        },
        default_name="t",
    )
    (page,) = doc.sections
    assert isinstance(page, PageSection) and page.with_ambience
    assert [effect.cue for effect in doc.mix.effects] == ["now"]
    assert doc.notes == ()


def test_the_plural_effect_table_is_named_as_unknown() -> None:
    doc = Document.from_toml(
        {
            "section": [{"number": 1, "page": "deck/a.html", "scene": "1"}],
            "mix": {"effects": [{"file": "media/tick.wav", "section": 1, "cue": "now"}]},
        },
        default_name="t",
    )
    assert doc.mix.effects == ()
    assert any("effects" in note for note in doc.notes), doc.notes


def test_an_unknown_table_is_refused_with_the_nearest_one() -> None:
    """The one-voice table of an older file meets the shape that replaced it, by its nearest name."""
    with pytest.raises(InputError) as refused:
        Document.from_toml({"voice": {"provider": "elevenlabs"}}, default_name="t")
    said = str(refused.value)
    assert said.startswith("decktalk.toml: unknown table(s) ['voice']. The known tables are [")
    assert said.endswith("]. Did you mean 'voices'?")


# ---- a section whose picture an external command renders ----------------------------------------


def test_a_render_section_is_narrated_and_names_the_command_that_draws_its_picture() -> None:
    """The voice reads it like a page section, so the narration runs through it, and a command draws its picture."""
    doc = Document.from_toml(
        {
            "section": [
                {"number": 1, "page": "deck/index.html", "scene": "1"},
                {"number": 2, "render_command": ["{python}", "render/bars.py", "{motion}", "{out}"]},
            ]
        },
        default_name="t",
    )
    rendered = doc.section(2)
    assert isinstance(rendered, PageSection)
    assert rendered.render_command == ("{python}", "render/bars.py", "{motion}", "{out}")
    assert rendered.rendered and not rendered.is_clip
    assert [one.number for one in doc.page_sections] == [1, 2]
    assert [one.number for one in doc.drawn_sections] == [1]


def test_a_render_command_that_is_not_a_list_of_words_is_refused() -> None:
    with pytest.raises(InputError, match="render_command"):
        Document.from_toml({"section": [{"number": 1, "render_command": "make film"}]}, default_name="t")


PAGE = {"number": 1, "page": "deck/index.html", "scene": "1"}
"""One page section, which is all a document needs before it can declare a clip it makes."""


def test_a_clip_the_project_makes_names_its_file_and_the_command_that_writes_it() -> None:
    doc = Document.from_toml(
        {"section": [PAGE], "clip": [{"file": "deck/clips/tone.mp4", "command": ["{python}", "clips/make", "{out}"]}]},
        default_name="t",
    )
    (made,) = doc.clips
    assert (made.file, made.command) == ("deck/clips/tone.mp4", ("{python}", "clips/make", "{out}"))


def test_a_clip_made_outside_the_project_is_refused() -> None:
    with pytest.raises(InputError, match="outside the project"):
        Document.from_toml(
            {"section": [PAGE], "clip": [{"file": "../film.mp4", "command": ["make"]}]}, default_name="t"
        )


def test_a_clip_whose_command_is_not_a_list_of_words_is_refused() -> None:
    with pytest.raises(InputError, match="'command' must be a list of words"):
        Document.from_toml({"section": [PAGE], "clip": [{"file": "a.mp4", "command": "make a.mp4"}]}, default_name="t")


def test_two_clips_that_write_one_file_are_refused() -> None:
    """Two commands writing one clip would leave whichever ran last, which no reader could tell from the file."""
    made = {"file": "deck/a.mp4", "command": ["make"]}
    with pytest.raises(InputError, match="more than one"):
        Document.from_toml({"section": [PAGE], "clip": [made, made]}, default_name="t")


def test_a_key_a_made_clip_does_not_read_is_a_note() -> None:
    doc = Document.from_toml(
        {"section": [PAGE], "clip": [{"file": "a.mp4", "command": ["make"], "fps": 30}]}, default_name="t"
    )
    assert any("fps" in note for note in doc.notes)


def test_a_section_cannot_be_both_a_page_and_a_render() -> None:
    with pytest.raises(InputError, match="render_command"):
        Document.from_toml(
            {"section": [{"number": 1, "page": "deck/index.html", "render_command": ["make"]}]}, default_name="t"
        )


# ---- every number a project writes is held to its range -----------------------------------------

OUT_OF_RANGE = [
    ("[[section]]\nnumber = 3\nclip = 'media/b.mp4'\nslate_seconds = -3\n", "section.slate_seconds", 22),
    ("[transition]\ndip_seconds = 99\n", "transition.dip_seconds", 20),
    ("[transition]\ndip_seconds = 0\n", "transition.dip_seconds", 20),
    ("[mix]\nmusic_db = 40\n", "mix.music_db", 20),
    ("[mix]\nmusic_fade_out_seconds = 0\n", "mix.music_fade_out_seconds", 20),
    ("[[mix.effect]]\nfile = 'media/t.wav'\nsection = 1\ncue = 'now'\ndb = 12\n", "mix.effect.db", 23),
    ("[[section]]\nnumber = 3\npage = 'deck/b.html'\nhold_seconds = -1\n", "section.hold_seconds", 22),
]
"""The audit's probe values and their neighbours: each loaded silently before, and each sits on its own line."""


@pytest.mark.parametrize(("table", "key", "line"), OUT_OF_RANGE, ids=[key for _t, key, _l in OUT_OF_RANGE])
def test_a_number_outside_its_range_is_refused_at_its_file_and_line(tmp_path, table: str, key: str, line: int) -> None:
    """A typo reaches ffmpeg otherwise, as a negative slate or a music bed louder than the voice."""
    with pytest.raises(InputError) as refused:
        Inputs.load(write_project(tmp_path, MINIMAL_TOML + "\n" + table), environ={})
    error = refused.value
    assert f"'{key.rsplit('.', 1)[-1]}' must be" in str(error), str(error)
    assert error.location is not None
    assert error.location.file == Path("decktalk.toml") and error.location.line == line, error.location


def test_the_line_of_a_refused_section_is_the_line_in_that_section() -> None:
    """Two sections can write the same key, and the refusal opens the one that is wrong."""
    text = (
        "[[section]]\nnumber = 1\nclip = 'a.mp4'\nslate_seconds = 2\n\n"
        "[[section]]\nnumber = 2\nclip = 'b.mp4'\nslate_seconds = -2\n"
    )
    with pytest.raises(InputError) as refused:
        Document.from_toml(tomllib.loads(text), default_name="t", text=text)
    assert refused.value.location is not None and refused.value.location.line == 9


def test_every_bounded_content_default_sits_inside_its_own_range() -> None:
    """A default outside its range would be refused the moment a project wrote it back."""
    keys = content_keys()
    assert {"section.slate_seconds", "transition.dip_seconds", "mix.music_db", "mix.effect.db"} <= {
        key.id for key in keys
    }
    for key in keys:
        assert key.bounds is not None, key.id
        assert key.default is None or key.bounds.holds(key.default), key.id
