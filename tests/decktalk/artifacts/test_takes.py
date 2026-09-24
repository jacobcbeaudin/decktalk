"""The take index, and the frozen inputs a take's name is taken over.

The golden rows are the six takes of the founder's own film and its hero cut, which were really
voiced and really paid for. A change to `TakeInputs`, to its order, or to the way the script is
cleaned for speech moves one of these digests, and the next run would buy that take again. Nothing
here needs audio, a network or a credential, because a digest is arithmetic over text.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts.takes import (
    PLACEHOLDER_PREFIX,
    TAKE_DIGITS,
    PlaceholderInputs,
    Take,
    TakeInputs,
    Takes,
    is_placeholder,
    take_file,
)

BEAT = "—"
"""The dash the founder's script writes a beat with, which is data rather than prose."""

VOICE = "03SG2XsqDqqfP8RMUXX1"
"""The voice the founder's films were read in, which is a published name and not a secret."""

MODEL = "eleven_multilingual_v2"
FORMAT = "mp3_44100_128"
SETTINGS = {"similarity_boost": 0.75, "speed": 1.0, "stability": 0.55, "style": 0.0, "use_speaker_boost": True}

GOLDEN: tuple[tuple[str, int, str, str], ...] = (
    (
        "halfway",
        1,
        "edcbaaa1db7507bd",
        f"Two friends, {BEAT}\n\none city, {BEAT}\n\nforty minutes apart. {BEAT}\n\n"
        f"So they text, {BEAT}\n\nand they never meet.",
    ),
    (
        "halfway",
        2,
        "cabcd6253368401b",
        f"Halfway finds the fair spot. {BEAT}\n\nType two addresses. {BEAT}\n\n"
        f"We drop a pin where it takes you both the same time, {BEAT}\n\nat a place worth the trip. {BEAT}\n\n"
        f"Twenty minutes for her. {BEAT}\n\nTwenty minutes for you.",
    ),
    (
        "halfway",
        3,
        "2f8dff906962193f",
        f"Last month, fourteen thousand people met in the middle. {BEAT}\n\nCoffee. {BEAT}\n\nLunch. {BEAT}\n\n"
        f"One first date {BEAT}\n\nthat became a second.",
    ),
    (
        "halfway",
        4,
        "3c1ca0a4ea4893d3",
        f"This year, five more cities, {BEAT}\n\nand one more seat at the table. {BEAT}\n\nHalfway. {BEAT}\n\n"
        f"Meet in the middle.",
    ),
    (
        "halfway/hero",
        3,
        "ea4cd720227d64d5",
        f"Last month, eleven thousand people met in the middle. {BEAT}\n\nCoffee. {BEAT}\n\nLunch. {BEAT}\n\n"
        f"One first date {BEAT}\n\nthat became a second.",
    ),
    ("halfway/hero", 4, "ddd317cc1c08a23c", f"Halfway. {BEAT}\n\nMeet in the middle."),
)
"""Every take the founder has paid for, as the film, the section, the digest and the text sent."""

IDS = [f"{film}-{section}" for film, section, _digest, _text in GOLDEN]


def inputs_for(text: str) -> TakeInputs:
    """The inputs of one of the founder's takes, built the way `narrate` builds them."""
    return TakeInputs.of(
        provider="elevenlabs", voice=VOICE, model=MODEL, output_format=FORMAT, settings=SETTINGS, text=text
    )


@pytest.mark.parametrize(("film", "section", "digest", "text"), GOLDEN, ids=IDS)
def test_the_digest_of_a_paid_take_is_the_one_in_its_index(film: str, section: int, digest: str, text: str) -> None:
    """A digest here that moved means the next run buys that take again, so this may never be updated."""
    assert inputs_for(text).digest == digest, f"{film} section {section} would be voiced again"


def test_every_golden_digest_is_distinct() -> None:
    assert len({digest for _film, _section, digest, _text in GOLDEN}) == len(GOLDEN)


def test_the_payload_is_every_input_in_declaration_order() -> None:
    payload = inputs_for("hello").payload.split("\n", 5)
    assert payload[:4] == ["elevenlabs", VOICE, MODEL, FORMAT]
    assert payload[4].startswith('{"similarity_boost"')
    assert payload[5] == "hello"


def test_the_voice_is_one_of_the_inputs() -> None:
    """Two voices reading one sentence are two takes, so the id is part of what names the file."""
    other = inputs_for("hello").model_copy(update={"voice": "someone-else"})
    assert other.digest != inputs_for("hello").digest


def test_the_voice_settings_are_rendered_with_their_keys_sorted() -> None:
    shuffled = dict(reversed(list(SETTINGS.items())))
    assert (
        TakeInputs.of(
            provider="elevenlabs", voice=VOICE, model=MODEL, output_format=FORMAT, settings=shuffled, text="x"
        ).digest
        == inputs_for("x").digest
    )


def test_a_digest_is_as_long_as_the_module_declares() -> None:
    assert len(inputs_for("hello").digest) == TAKE_DIGITS


def test_a_placeholder_digest_can_never_be_read_as_a_paid_one() -> None:
    placeholder = PlaceholderInputs(words_per_minute=150.0, beat_seconds=0.35, text="hello").digest
    assert placeholder.startswith(PLACEHOLDER_PREFIX)
    assert is_placeholder(placeholder)
    assert not is_placeholder(inputs_for("hello").digest)


def test_a_placeholder_moves_with_its_pace_and_its_beat() -> None:
    first = PlaceholderInputs(words_per_minute=150.0, beat_seconds=0.35, text="hello").digest
    faster = PlaceholderInputs(words_per_minute=170.0, beat_seconds=0.35, text="hello").digest
    assert first != faster


def take(section: int, *, seconds: float, voiced: bool = True, lead: float = 0.0, tail: float = 0.0) -> Take:
    return Take(
        section=section,
        key=f"{section:02d}",
        chapter="",
        hash=f"h{section}",
        voiced=voiced,
        word_count=2,
        characters=10,
        estimated_seconds=seconds,
        duration_seconds=seconds,
        speech_end_seconds=seconds,
        sound_end_seconds=seconds,
        lead_seconds=lead,
        tail_seconds=tail,
        spoken="hello there",
    )


INDEX = Takes(
    script="script.md",
    model=MODEL,
    output_format=FORMAT,
    sections=(take(1, seconds=2.0, lead=0.5, tail=0.7), take(2, seconds=3.0, lead=0.5, tail=0.7)),
)


def test_a_take_is_named_by_its_digest() -> None:
    assert take(1, seconds=1.0).file == take_file("h1")


def test_a_section_runs_for_its_lead_its_sound_and_its_tail() -> None:
    assert take(1, seconds=2.0, lead=0.5, tail=0.7).span_seconds == 3.2


def test_a_take_plays_to_its_sound_end_rather_than_to_its_last_byte() -> None:
    row = take(1, seconds=2.0).model_copy(update={"duration_seconds": 2.6, "sound_end_seconds": 2.0})
    assert row.sound_seconds == 2.0


def test_a_take_with_no_measured_sound_end_plays_whole() -> None:
    row = take(1, seconds=2.0).model_copy(update={"sound_end_seconds": None})
    assert row.sound_seconds == 2.0


def test_the_clock_is_the_rows_added_up_in_section_order() -> None:
    assert INDEX.starts == {1: 0.0, 2: 3.2}
    assert INDEX.end(1) == 3.2
    assert INDEX.total_seconds == 7.4


def test_the_last_word_of_a_section_lands_after_that_section_lead() -> None:
    assert INDEX.speech_end(1) == 2.5


def test_a_section_with_no_take_has_no_place_on_the_clock() -> None:
    assert INDEX.of(9) is None
    assert INDEX.start(9) is None
    assert INDEX.end(9) is None
    assert INDEX.speech_end(9) is None


def test_an_index_is_estimated_when_any_row_is_a_placeholder() -> None:
    assert not INDEX.estimated
    assert INDEX.model_copy(update={"sections": (take(1, seconds=1.0, voiced=False),)}).estimated


def test_the_paid_sections_are_the_ones_a_placeholder_run_must_not_replace() -> None:
    mixed = INDEX.model_copy(update={"sections": (take(1, seconds=1.0), take(2, seconds=1.0, voiced=False))})
    assert mixed.voiced == (1,)


def test_the_index_round_trips_through_its_own_file(tmp_path: Path) -> None:
    path = INDEX.write(tmp_path / "takes.json")
    assert Takes.read(path) == INDEX
