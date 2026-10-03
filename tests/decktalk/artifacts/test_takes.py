"""The take index, and the frozen inputs a take's name is taken over.

The golden digests of the founder's paid takes live once, in `tests/data/take_hash.json`, and
`tests/contract/test_take_hash.py` holds every one of them against the markdown it was voiced from,
which proves the text this file would hold them against and the digest together. What stays here is
the shape of the inputs, which is what a change to `TakeInputs` or its order would move.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from decktalk.artifacts.takes import (
    PLACEHOLDER_PREFIX,
    PLACEHOLDER_SUFFIX,
    TAKE_DIGITS,
    TAKE_HASH,
    PlaceholderInputs,
    Take,
    TakeInputs,
    Takes,
    is_placeholder,
    take_file,
)
from decktalk.errors import ErrorCode, InputError
from support.paths import DATA
from support.projects import MINIMAL_TOML, load_project
from support.takes import a_take

INPUTS = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))["inputs"]
"""The inputs the founder's takes were bought under, which the golden digests are held to."""


def inputs_for(text: str) -> TakeInputs:
    """The inputs of one of the founder's takes, built the way `narrate` builds them."""
    return TakeInputs.of(**INPUTS, text=text)


def test_the_payload_is_every_input_in_declaration_order() -> None:
    payload = inputs_for("hello").payload.split("\n", 5)
    assert payload[:4] == [INPUTS["provider"], INPUTS["voice"], INPUTS["model"], INPUTS["output_format"]]
    assert payload[4].startswith('{"similarity_boost"')
    assert payload[5] == "hello"


@pytest.mark.parametrize("field", ["provider", "voice", "model", "output_format"])
def test_a_newline_in_any_field_but_the_text_is_refused_with_a_sentence(field: str) -> None:
    """The payload joins the fields with newlines, so one inside a field could make two inputs one take."""
    with pytest.raises(InputError, match="holds a newline") as caught:
        TakeInputs.of(**INPUTS | {field: f"{INPUTS[field]}\nx"}, text="hello")
    assert caught.value.hint


def test_a_newline_inside_a_voice_setting_is_written_escaped_and_never_breaks_a_line() -> None:
    made = TakeInputs.of(**INPUTS | {"settings": {"style": "a\nb"}}, text="hello")
    assert len(made.payload.split("\n")) == len(TakeInputs.model_fields)


def test_two_sets_of_inputs_that_would_join_to_one_payload_cannot_both_be_made() -> None:
    """`model="m\nf"` with an empty format joins exactly as `model="m"` with format "f" does, so it is refused."""
    split = INPUTS | {"model": "m", "output_format": "f"}
    joined = INPUTS | {"model": "m\nf", "output_format": ""}
    TakeInputs.of(**split, text="t")
    with pytest.raises(InputError):
        TakeInputs.of(**joined, text="t")


def test_the_text_keeps_every_newline_it_has() -> None:
    assert inputs_for("one\n\ntwo").payload.endswith("\none\n\ntwo")


def test_the_voice_settings_are_rendered_with_their_keys_sorted() -> None:
    shuffled = dict(reversed(list(INPUTS["settings"].items())))
    assert TakeInputs.of(**INPUTS | {"settings": shuffled}, text="x").digest == inputs_for("x").digest


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


INDEX = Takes(
    script="script.md",
    model=INPUTS["model"],
    output_format=INPUTS["output_format"],
    sections=(
        a_take(1, seconds=2.0, lead_seconds=0.5, tail_seconds=0.7),
        a_take(2, seconds=3.0, lead_seconds=0.5, tail_seconds=0.7),
    ),
)


def test_a_take_is_named_by_its_digest_under_the_suffix_of_what_it_holds() -> None:
    assert take_file(f"{1:016x}", ".mp3") == "0000000000000001.mp3"
    assert take_file(f"{1:016x}", ".wav") == "0000000000000001.wav"


def test_a_placeholder_is_the_mp3_decktalk_writes_whatever_the_voice_returns() -> None:
    assert take_file(f"{PLACEHOLDER_PREFIX}0123456789", ".wav") == f"{PLACEHOLDER_PREFIX}0123456789{PLACEHOLDER_SUFFIX}"


DIGEST = st.from_regex(TAKE_HASH, fullmatch=True)
"""Every name a take may have, paid or placeholder, drawn from the pattern the model enforces,
which is what a near miss is built from."""

NEAR_MISS = (
    st.text()
    | DIGEST.map(str.upper)
    | DIGEST.map(lambda digest: digest[:-1])
    | DIGEST.map(lambda digest: digest + "0")
    | DIGEST.map(lambda digest: f"../{digest}")
).filter(lambda name: re.fullmatch(TAKE_HASH, name) is None)
"""Anything that is not a digest, weighted toward the names that are one character away from one."""


@given(hostile=NEAR_MISS)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_a_take_index_that_names_a_file_by_anything_but_a_digest_is_refused(tmp_path: Path, hostile: str) -> None:
    """The digest becomes a file name under the take directory, so a path in it would read anywhere.

    One index file is written over again for every example, which is why the shared directory is safe.
    """
    row = a_take(1, seconds=1.0).model_dump(mode="json") | {"hash": hostile}
    index = INDEX.model_dump(mode="json") | {"sections": [row]}
    path = tmp_path / "takes.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(InputError, match="cannot be read"):
        Takes.read(path)


def test_both_kinds_of_digest_name_a_take() -> None:
    placeholder = PlaceholderInputs(words_per_minute=150.0, beat_seconds=0.35, text="hello").digest
    for digest in (inputs_for("hello").digest, placeholder):
        assert Take.model_validate(a_take(1, seconds=1.0).model_dump() | {"hash": digest}).hash == digest


def test_a_section_runs_for_its_lead_its_sound_and_its_tail() -> None:
    assert a_take(1, seconds=2.0, lead_seconds=0.5, tail_seconds=0.7).span_seconds == 3.2


def test_a_take_plays_to_its_sound_end_rather_than_to_its_last_byte() -> None:
    row = a_take(1, seconds=2.0).model_copy(update={"duration_seconds": 2.6, "sound_end_seconds": 2.0})
    assert row.sound_seconds == 2.0


def test_a_take_with_no_measured_sound_end_plays_whole() -> None:
    row = a_take(1, seconds=2.0).model_copy(update={"sound_end_seconds": None})
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
    assert INDEX.model_copy(update={"sections": (a_take(1, seconds=1.0, voiced=False),)}).estimated


def test_the_paid_sections_are_the_ones_a_placeholder_run_must_not_replace() -> None:
    mixed = INDEX.model_copy(update={"sections": (a_take(1, seconds=1.0), a_take(2, seconds=1.0, voiced=False))})
    assert mixed.voiced == (1,)


def test_the_index_round_trips_through_its_own_file(tmp_path: Path) -> None:
    path = INDEX.write(tmp_path / "takes.json")
    assert Takes.read(path) == INDEX


@pytest.mark.parametrize("own_dir", [False, True], ids=["build-index", "takes-dir-index"])
@pytest.mark.parametrize("written", ["{not json", '{"version": 1, "sections": []}'], ids=["corrupt", "older-shape"])
def test_a_take_index_that_does_not_read_is_refused_with_a_sentence_and_left_on_disk(
    tmp_path: Path, own_dir: bool, written: str
) -> None:
    """The take index is a paid record wherever it lives, so a reader never counts it as absent and never deletes it."""
    toml = MINIMAL_TOML + ('\n[narration]\ntakes_dir = "voice"\n' if own_dir else "")
    inputs = load_project(tmp_path, toml)
    path = inputs.workspace.takes_path
    assert (path.parent == tmp_path / "voice") is own_dir
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(written, encoding="utf-8")
    for reading in (inputs.takes, lambda: Takes.previous(path)):
        with pytest.raises(InputError) as refused:
            reading()
        assert refused.value.code is ErrorCode.INPUT
        assert "takes.json" in str(refused.value) and "paid for" in str(refused.value)
        assert "buys again" in (refused.value.hint or "")
    assert path.read_text(encoding="utf-8") == written
