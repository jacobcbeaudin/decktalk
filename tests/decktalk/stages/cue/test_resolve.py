"""The arithmetic that turns a cue phrase into a second, with no project and no file behind it."""

from __future__ import annotations

from pathlib import Path

from decktalk.findings import Certainty, Code
from decktalk.inputs.cues import Cue, CuedSection
from decktalk.pipeline import Stage
from decktalk.results import Word
from decktalk.stages.cue.resolve import (
    ambiguity,
    anchor_time,
    resolve_cue,
    resolve_sections,
    short_section,
)

WORDS = (
    Word(word="Hello", start=0.5, end=0.9),
    Word(word="one", start=1.0, end=1.2),
    Word(word="in", start=1.3, end=1.4),
    Word(word="ten", start=1.5, end=1.8),
    Word(word="one", start=2.1, end=2.3),
)
"""One section's words, which every case here resolves against."""


def test_a_phrase_resolves_to_the_start_of_the_word_it_names() -> None:
    assert anchor_time(Cue(cue="1.1:a", on="in ten"), WORDS) == 1.3
    assert resolve_cue(Cue(cue="1.1:a", on="in ten", offset=0.1), WORDS) == 1.4


def test_an_occurrence_chooses_between_two_of_the_same_phrase() -> None:
    assert resolve_cue(Cue(cue="1.1:a", on="one", occurrence=2), WORDS) == 2.1


def test_the_two_edges_are_the_section_start_and_the_last_word() -> None:
    assert resolve_cue(Cue(cue="1.1:a", on="$start"), WORDS) == 0.0
    assert resolve_cue(Cue(cue="1.1:a", on="$end"), WORDS) == 2.3


def test_a_phrase_that_is_not_spoken_resolves_to_nothing() -> None:
    assert resolve_cue(Cue(cue="1.1:a", on="missing phrase"), WORDS) is None


def test_a_nudge_never_pulls_a_cue_in_front_of_its_own_section() -> None:
    """A second before the section starts has nowhere to play, so it lands on the section's start."""
    assert resolve_cue(Cue(cue="1.1:a", on="Hello", offset=-9.0), WORDS) == 0.0


def test_a_cue_on_the_section_start_is_an_uncertain_finding_with_its_second_in_it() -> None:
    """The null-offset case: the second is the section's own start and not a word's."""
    block = CuedSection(number=1, cues=(Cue(cue="1.1:a", on="$start"),))
    sections, found = resolve_sections([block], {1: WORDS}, clips=set(), estimated=set(), stage=Stage.CUE)
    assert sections[0].cues[0].seconds == 0.0
    (judged,) = found
    assert judged.code is Code.CUE_NO_ONSET
    assert judged.certainty is Certainty.UNCERTAIN
    assert "0.00s" in judged.message and judged.location.cue == "1.1:a"
    assert judged.stage is Stage.CUE


def test_an_unresolved_phrase_is_certain_and_names_the_phrase_and_the_count() -> None:
    block = CuedSection(number=1, cues=(Cue(cue="1.1:a", on="nowhere"),))
    sections, found = resolve_sections([block], {1: WORDS}, clips=set(), estimated=set())
    assert sections[0].cues[0].seconds is None
    (judged,) = found
    assert judged.code is Code.CUE_UNRESOLVED
    assert judged.certainty is Certainty.CERTAIN
    assert "'nowhere'" in judged.message and f"{len(WORDS)} words" in judged.message


def test_a_page_section_with_no_take_reports_its_cues_rather_than_dropping_them() -> None:
    """A cue nobody resolved must be counted, because the recorder would otherwise play that section blind."""
    block = CuedSection(number=1, cues=(Cue(cue="1.1:a", on="Hello"),))
    sections, found = resolve_sections([block], {}, clips=set(), estimated=set())
    assert sections[0].cues[0].seconds is None
    assert [one.code for one in found] == [Code.CUE_UNRESOLVED]
    assert "has no take" in found[0].message


def test_a_clip_section_with_no_take_is_the_plain_skip_it_should_be() -> None:
    """The voice never reads a clip section, so its cue was never going to resolve against words."""
    block = CuedSection(number=1, cues=(Cue(cue="1.1:a", on="$start"),))
    sections, found = resolve_sections([block], {}, clips={1}, estimated=set())
    assert found == []
    assert sections[0].cues[0].seconds is None


def test_a_row_carries_its_phrase_and_its_nudge_so_the_word_behind_it_is_arithmetic() -> None:
    block = CuedSection(number=1, cues=(Cue(cue="1.1:a", on="ten", offset=0.25),))
    sections, _found = resolve_sections([block], {1: WORDS}, clips=set(), estimated=set())
    (row,) = sections[0].cues
    assert (row.cue, row.phrase, row.seconds, row.offset) == ("1.1:a", "ten", 1.75, 0.25)
    assert row.seconds is not None
    assert round(row.seconds - row.offset, 3) == 1.5


def test_a_section_is_estimated_only_when_its_own_words_were_estimated() -> None:
    blocks = [CuedSection(number=1, cues=()), CuedSection(number=2, cues=())]
    sections, _found = resolve_sections(blocks, {}, clips=set(), estimated={2})
    assert [(one.section, one.key, one.estimated) for one in sections] == [(1, "01", False), (2, "02", True)]


def test_a_finding_carries_the_cue_file_it_is_about_when_the_caller_names_one() -> None:
    block = CuedSection(number=1, cues=(Cue(cue="1.1:a", on="nowhere"),))
    _sections, found = resolve_sections([block], {1: WORDS}, clips=set(), estimated=set(), cues_file=Path("cues.json"))
    assert found[0].location.file == Path("cues.json")


def test_a_repeated_phrase_is_ambiguous_only_when_the_cue_names_no_occurrence() -> None:
    said = ambiguity(Cue(cue="1.1:a", on="one"), WORDS)
    assert said is not None and "occurs 2 times" in said and "1.00s, 2.10s" in said
    assert ambiguity(Cue(cue="1.1:a", on="one", occurrence=1, occurrence_set=True), WORDS) is None
    assert ambiguity(Cue(cue="1.1:a", on="ten"), WORDS) is None
    assert ambiguity(Cue(cue="1.1:a", on="$start"), WORDS) is None


def test_a_section_shorter_than_its_visuals_need_is_said_once_with_both_numbers() -> None:
    block = CuedSection(number=1, cues=(), min_seconds=9.0)
    said = short_section(block, WORDS)
    assert said is not None and "2.3s" in said and "6.7s" in said and "9.0s" in said
    assert short_section(CuedSection(number=1, cues=(), min_seconds=1.0), WORDS) is None
    assert short_section(CuedSection(number=1, cues=()), WORDS) is None


def test_a_cue_whose_phrase_is_not_written_yet_says_so_rather_than_naming_an_empty_phrase() -> None:
    """A scaffolded row carries an empty `on`, and telling its author that no word of the section is
    `''` reads as a defect where the truth is that nobody has written the phrase yet."""
    block = CuedSection(number=1, cues=(Cue(cue="1.1:a", on=""),))
    sections, found = resolve_sections([block], {1: WORDS}, clips=set(), estimated=set())
    assert sections[0].cues[0].seconds is None
    (judged,) = found
    assert judged.code is Code.CUE_UNRESOLVED
    assert "has no phrase yet" in judged.message and "''" not in judged.message
