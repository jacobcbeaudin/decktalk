"""`script.md`: the sections it declares, the directions between its paragraphs, and what the voice hears."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from decktalk.errors import InputError
from decktalk.inputs.script import estimated_words, parse_script, read_script, spoken_words
from decktalk.settings import Settings
from decktalk.speech import BEAT, Piece, canonical_text
from support.paths import DATA

SCRIPT = """# Title

## 0. On camera

[clip]

Hi there.

## 1. Open

[Deck. Title.]

Welcome to the **deck**. It has `code` and a [link](http://x).

[Deck. Next.]
Second paragraph, with [NUMBER] placeholder.

## Notes

not a section
"""


def test_a_heading_is_its_number_and_its_title_and_nothing_after_the_title_is_read_apart():
    """A time range after a title was once read as a budget nothing used, so it is now part of the title."""
    (section,) = parse_script("## 1. Open - 0:00 to 0:20\n\nHello.\n")
    assert (section.number, section.title) == (1, "Open - 0:00 to 0:20")
    assert not hasattr(section, "target_seconds")


def test_parse_script_sections_and_directions():
    segs = parse_script(SCRIPT)
    assert [s.number for s in segs] == [0, 1]
    one = segs[1]
    assert one.title == "Open"
    # The direction before any prose has nothing to pause after, and the one between paragraphs is a beat.
    assert one.pieces == (
        Piece("Welcome to the deck. It has code and a link.", BEAT),
        Piece("Second paragraph, with [NUMBER] placeholder."),
    )
    assert one.word_count == 15


def test_a_placeholder_is_spoken_and_a_direction_is_not():
    assert parse_script("## 1. A\n\nAt [VENUE] tonight. [not spoken]")[0].pieces == (Piece("At [VENUE] tonight."),)


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


def test_the_spoken_words_drop_a_token_that_is_only_punctuation() -> None:
    """A dash the author wrote is a beat, never a word an aligner could be asked to find."""
    assert spoken_words("A bowl - a ball.") == ("A", "bowl", "a", "ball")
    assert spoken_words("“Quoted,” he said… — then")[:3] == ("Quoted", "he", "said")
    assert spoken_words("") == ()


def test_estimated_words_space_the_section_evenly_and_drop_its_punctuation() -> None:
    (section,) = parse_script("## 1. Open\n\nA bowl, a ball.\n")
    words = section.estimated_words(4.0)
    assert [word.word for word in words] == ["A", "bowl", "a", "ball"]
    assert words[0].start == 0.0
    assert words[-1].end == pytest.approx(3.98)


def test_a_section_that_says_nothing_has_no_estimated_words() -> None:
    (section,) = parse_script("## 1. Open\n\n[A direction alone.]\n")
    assert section.estimated_words(4.0) == []


SPOKEN_TOKEN = st.sampled_from(["A", "bowl", "ball.", "-", "—", "...", "R&D", "“said”", "it's", "/"])


@given(tokens=st.lists(SPOKEN_TOKEN, max_size=12), seconds=st.floats(min_value=0.0, max_value=60.0))
def test_every_estimated_word_is_one_of_the_spoken_words(tokens: list[str], seconds: float) -> None:
    """The estimate and the aligner's request read one tokenizer, so they never disagree on a word."""
    sections = parse_script("## 1. Open\n\n" + " ".join(["Open", *tokens]) + "\n")
    (section,) = sections
    assert tuple(word.word for word in section.estimated_words(seconds)) == spoken_words(section.spoken)
    assert all(word.word for word in section.estimated_words(seconds))
    assert section.estimated_words(seconds) == estimated_words(section.spoken, seconds)


# ---- voices and turns --------------------------------------------------------------------------

HASHED = [
    take["markdown"]
    for name in ("take_hash.json", "take_hash_pauses.json")
    for take in json.loads((DATA / name).read_text(encoding="utf-8"))["takes"]
]
"""Every section whose digest a take was bought under, which a section with no voice direction must read alike."""


@pytest.mark.parametrize("markdown", HASHED, ids=[f"take-{place}" for place in range(len(HASHED))])
def test_a_section_with_no_voice_direction_is_one_turn_of_todays_pieces(markdown: str) -> None:
    (section,) = parse_script(markdown)
    (turn,) = section.turns
    assert (turn.number, turn.voice, turn.gap) == (1, None, None)
    assert turn.pieces == section.pieces


def test_a_leading_direction_changes_no_piece() -> None:
    plain = parse_script("## 1. Open\n\nHello there. [beat] General Kenobi.\n")[0]
    marked = parse_script("## 1. Open\n\n[voice narrator]\n\nHello there. [beat] General Kenobi.\n")[0]
    assert marked.pieces == plain.pieces
    (turn,) = marked.turns
    assert turn.voice == "narrator"


def test_a_direction_naming_the_voice_in_force_is_no_boundary() -> None:
    """It is removed, so it adds no pause and no piece, and the text either side stays one turn."""
    (section,) = parse_script("## 1. Open\n\n[voice host]\n\nHello there.\n[voice host]\nGeneral Kenobi.\n")
    (turn,) = section.turns
    assert turn.pieces == (Piece("Hello there. General Kenobi."),)
    assert "—" not in canonical_text(turn.pieces)


DIALOGUE = """## 2. The interview

[Scene 2. The question, then the answer.]

[voice narrator]

Sam, what made you start?

[pause 1.5]

[voice guest]

I wanted a film I could change one sentence at a time.

[voice narrator]

And now?

## 3. Close

That is all.
"""


def test_each_change_of_voice_starts_a_turn_and_each_section_starts_in_the_default() -> None:
    interview, close = parse_script(DIALOGUE)
    assert [(turn.number, turn.voice) for turn in interview.turns] == [(1, "narrator"), (2, "guest"), (3, "narrator")]
    assert [turn.pieces for turn in interview.turns] == [
        (Piece("Sam, what made you start?"),),
        (Piece("I wanted a film I could change one sentence at a time."),),
        (Piece("And now?"),),
    ]
    assert interview.pieces == tuple(piece for turn in interview.turns for piece in turn.pieces)
    assert [(turn.voice, turn.gap) for turn in close.turns] == [(None, None)]


@pytest.mark.parametrize(
    ("between", "gap"),
    [
        ("[pause 1.5]\n\n[voice guest]", 1.5),
        ("[voice guest]\n\n[pause 2]", 2.0),
        ("[pause 1]\n\n[voice guest]\n\n[pause 2.5]", 2.5),
        ("[beat]\n\n[voice guest]", None),
        ("[voice guest]", None),
        ('<break time="800ms" />\n\n[voice guest]', 0.8),
    ],
    ids=["before", "after", "both-the-longest", "a-beat", "none", "a-break-tag"],
)
def test_the_pause_written_where_the_voice_changes_is_the_gap_and_in_neither_turns_text(
    between: str, gap: float | None
) -> None:
    """The pause is placement, never take content, so retiming a dialogue buys nothing."""
    markdown = f"## 1. Talk\n\n[voice host]\n\nHello there.\n\n{between}\n\nGeneral Kenobi.\n"
    first, second = parse_script(markdown)[0].turns
    assert first.gap == gap
    assert second.gap is None
    assert first.pieces == (Piece("Hello there."),)
    assert second.pieces == (Piece("General Kenobi."),)


@pytest.mark.parametrize(
    "claimed", ["[Voice over the chart.]", "[voice.guest]", "[voice Guest]", "[voice]", "[voice guest please]"]
)
def test_every_whole_line_that_opens_with_the_word_voice_is_claimed_and_never_a_beat(claimed: str) -> None:
    (section,) = parse_script(f"## 1. Open\n\nHello there.\n\n{claimed}\n\nGeneral Kenobi.\n")
    (turn,) = section.turns
    assert turn.pieces == (Piece("Hello there."), Piece("General Kenobi."))
    assert "—" not in canonical_text(section.pieces)


def test_a_note_that_merely_starts_with_voice_is_still_a_beat() -> None:
    (section,) = parse_script("## 1. Open\n\nHello there.\n\n[voiceover]\n\nGeneral Kenobi.\n")
    assert section.pieces == (Piece("Hello there.", BEAT), Piece("General Kenobi."))


def test_a_turn_knows_the_line_its_first_prose_sits_on() -> None:
    interview, close = parse_script(DIALOGUE)
    lines = DIALOGUE.splitlines()
    assert [lines[turn.line - 1] for turn in interview.turns] == [
        "Sam, what made you start?",
        "I wanted a film I could change one sentence at a time.",
        "And now?",
    ]
    assert lines[close.turns[0].line - 1] == "That is all."


def test_a_turn_is_measured_like_a_section() -> None:
    interview = parse_script(DIALOGUE)[0]
    cfg = Settings().narration
    assert sum(turn.word_count for turn in interview.turns) == interview.word_count
    second = interview.turns[1]
    assert second.spoken == "I wanted a film I could change one sentence at a time."
    assert second.placeholder_seconds(cfg) == round(second.word_count / cfg.placeholder_words_per_minute * 60, 3)
    assert second.estimated_seconds(cfg) == round(second.word_count / cfg.words_per_minute * 60, 1)
    assert [word.word for word in second.estimated_words(3.0)][:2] == ["I", "wanted"]
