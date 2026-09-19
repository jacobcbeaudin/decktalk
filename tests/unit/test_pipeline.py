"""The vocabulary of a run: the five stages in the order they run, what a progress row records, and the
closed values a run's files carry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.pipeline import ProgressEvent, SectionKind, SoundscapeStatus, Stage, Substitute, TakeStatus

WIRE = json.loads((Path(__file__).resolve().parents[1] / "data" / "vocabulary.json").read_text(encoding="utf-8"))


def test_the_stages_are_declared_in_the_order_a_build_runs_them_under_the_names_it_gives_them():
    """The contract fixes the five words and their order, and the build flags and the progress log use them."""
    assert [stage.value for stage in Stage] == WIRE["stages"]
    assert [Stage(word) for word in WIRE["stages"]] == list(Stage)


def test_a_span_is_both_ends_inclusive_and_empty_when_it_runs_backwards():
    assert Stage.span(None, None) == tuple(Stage)
    assert Stage.span(Stage.RECORD, None) == (Stage.RECORD, Stage.ASSEMBLE, Stage.VERIFY)
    assert Stage.span(None, Stage.ALIGN) == (Stage.NARRATE, Stage.ALIGN)
    assert Stage.span(Stage.ALIGN, Stage.ALIGN) == (Stage.ALIGN,)
    assert Stage.span(Stage.VERIFY, Stage.RECORD) == ()


def test_the_progress_events_are_the_four_the_contract_names_and_three_of_them_close():
    assert [event.value for event in ProgressEvent] == WIRE["progress_events"]
    assert [event for event in ProgressEvent if event.closes] == [
        ProgressEvent.DONE,
        ProgressEvent.SKIP,
        ProgressEvent.FAIL,
    ]


@pytest.mark.parametrize(
    ("enum", "key"),
    [
        (TakeStatus, "take_statuses"),
        (SoundscapeStatus, "soundscape_statuses"),
        (SectionKind, "section_kinds"),
        (Substitute, "substitutes"),
    ],
    ids=lambda value: value if isinstance(value, str) else value.__name__,
)
def test_each_closed_value_a_run_writes_is_the_word_its_json_carries(enum, key):
    """A take plan, a soundscape item and a `cuts.json` row carry these words, and the contract fixes them."""
    assert [member.value for member in enum] == WIRE[key]
    assert [enum(word) for word in WIRE[key]] == list(enum)


@pytest.mark.parametrize("member", [*Stage, *ProgressEvent, *TakeStatus, *SoundscapeStatus, *SectionKind, *Substitute])
def test_a_raw_string_never_compares_equal_to_a_member(member):
    """A plain enum, so a word read from a file has to be parsed into a member before it is compared."""
    assert member != member.value
