"""`script.md`: the sections it declares, the directions between its paragraphs, and what the voice hears."""

from __future__ import annotations

import pytest

from decktalk.errors import InputError
from decktalk.inputs.script import parse_script, read_script, strip_markdown
from decktalk.settings import Settings
from decktalk.speech import BEAT, Piece

SCRIPT = """# Title

## 0. On camera

[clip]

Hi there.

## 1. Open — 0:00 to 0:20

[Deck. Title.]

Welcome to the **deck**. It has `code` and a [link](http://x).

[Deck. Next.]
Second paragraph, with [NUMBER] placeholder.

## Notes

not a section
"""


def test_parse_script_sections_and_directions():
    segs = parse_script(SCRIPT)
    assert [s.number for s in segs] == [0, 1]
    one = segs[1]
    assert one.title == "Open" and one.target_seconds == 20
    # The direction before any prose has nothing to pause after, and the one between paragraphs is a beat.
    assert one.pieces == (
        Piece("Welcome to the deck. It has code and a link.", BEAT),
        Piece("Second paragraph, with [NUMBER] placeholder."),
    )
    assert one.word_count == 15


def test_strip_markdown_keeps_placeholders():
    assert strip_markdown("At [VENUE] tonight. [not spoken]") == (Piece("At [VENUE] tonight."),)


def test_a_section_carries_its_pauses_as_data_and_never_as_markup():
    section = parse_script("## 1. A\n\nThink about it.\n\n[pause 3]\n\nOnly two x is left. [beat] Done.")[0]
    assert section.pieces == (Piece("Think about it.", 3.0), Piece("Only two x is left.", BEAT), Piece("Done."))
    assert not any("<" in piece.text or piece.text.endswith("—") for piece in section.pieces)
    assert section.spoken == "Think about it. Only two x is left. Done."
    # The placeholder honours the declared pauses, so the pause lengthens the section.
    short = parse_script("## 1. A\n\nThink about it.\n\nOnly two x is left. Done.")[0]
    cfg = Settings().narration
    assert abs((section.placeholder_seconds(cfg) - short.placeholder_seconds(cfg)) - 3.7) < 1e-6


def test_pause_direction_accepts_decimals_and_case():
    section = parse_script("## 1. A\n\nOne. [Pause 1.5] Two.")[0]
    assert section.pieces == (Piece("One.", 1.5), Piece("Two."))
    assert section.spoken == "One. Two."


def test_back_to_back_directions_keep_the_longest_pause_and_a_last_pause_is_the_tail():
    section = parse_script("## 1. A\n\nOne.\n\n[beat]\n\n[pause 2]\n\nTwo.\n\n[pause 4]\n")[0]
    assert section.pieces == (Piece("One.", 2.0), Piece("Two."))


@pytest.mark.parametrize(
    "tag", ['<break time="1.5s" />', '<break time="1.5s"/>', "<break time='1500ms'/>", '<BREAK TIME="1.5S" />']
)
def test_a_break_tag_written_by_hand_is_a_timed_pause(tag: str):
    """One way to pause: the tag becomes the same piece and pause that `[pause 1.5]` does."""
    by_tag = parse_script(f"## 1. A\n\nOne. {tag} Two.")[0]
    assert by_tag.pieces == parse_script("## 1. A\n\nOne. [pause 1.5] Two.")[0].pieces


@pytest.mark.parametrize("tag", ['<break strength="strong" />', '<break time="soon" />', "<break>"])
def test_a_break_tag_with_no_length_to_read_is_refused(tag: str):
    with pytest.raises(InputError) as caught:
        parse_script(f"## 1. A\n\nOne. {tag} Two.")
    assert "[pause N]" in (caught.value.hint or "")


def test_a_dash_the_author_wrote_is_spoken_text_and_counts_as_a_beat_in_a_placeholder():
    cfg = Settings().narration
    dashed = parse_script("## 1. A\n\nOne thought — another.")[0]
    plain = parse_script("## 1. A\n\nOne thought another.")[0]
    assert dashed.pieces == (Piece("One thought — another."),)
    assert dashed.spoken == plain.spoken
    beat = cfg.placeholder_beat_seconds
    assert dashed.placeholder_seconds(cfg) - plain.placeholder_seconds(cfg) == pytest.approx(beat)


def test_a_break_tag_refused_while_reading_the_file_names_the_file(tmp_path):
    script = tmp_path / "script.md"
    script.write_text('## 1. A\n\nOne. <break strength="strong" /> Two.\n', encoding="utf-8")
    with pytest.raises(InputError) as caught:
        read_script(script, tmp_path, declared={1})
    assert caught.value.location is not None and "script.md" in str(caught.value.location)
