"""What the script would sound like, judged with the line each rule is broken on."""

from __future__ import annotations

from pathlib import Path

from decktalk.findings import Certainty, Code
from decktalk.inputs.script import parse_script
from decktalk.stages.check.script import (
    placeholder_findings,
    placeholder_rows,
    script_findings,
    spoken_sections,
    symbol_findings,
)

SCRIPT = """# Demo

## 1. One

The number is [NUMBER] and the rest is written out.

## 2. Two

Nothing here needs filling in.
"""

WHERE = Path("script.md")


def test_a_spoken_line_knows_which_section_it_is_in() -> None:
    """A finding about one line is more use with the section an author would open to fix it."""
    inside = spoken_sections(SCRIPT)
    assert set(inside.values()) == {1, 2}
    assert inside[max(line for line, number in inside.items() if number == 1)] == 1


def test_a_heading_is_not_a_spoken_line() -> None:
    lines = SCRIPT.splitlines()
    headings = [number for number, line in enumerate(lines, start=1) if line.startswith("## ")]
    assert not set(headings) & set(spoken_sections(SCRIPT))


def test_an_open_placeholder_is_found_with_its_line() -> None:
    assert placeholder_rows(SCRIPT) == [(5, "NUMBER")]


def test_a_filled_paragraph_holds_no_placeholder() -> None:
    assert placeholder_rows("## 1. One\n\nNothing to fill in here.\n") == []


def test_an_open_placeholder_is_a_certain_judgement_naming_its_line() -> None:
    """A voiced run reads the placeholder's own name out, and the take has already been paid for."""
    (found,) = placeholder_findings(SCRIPT, script=WHERE)
    assert found.code is Code.TAKE_PLACEHOLDER
    assert found.certainty is Certainty.CERTAIN
    assert found.location.line == 5
    assert found.location.section == 1
    assert "NUMBER" in found.message


def test_a_brace_is_judged_under_the_same_code_as_a_placeholder() -> None:
    """The voice reads a brace out too, which is the one rule `narrate` refuses on."""
    (found,) = placeholder_findings("## 1. One\n\nThe value is {value} today.\n", script=WHERE)
    assert found.code is Code.TAKE_PLACEHOLDER
    assert "brace" in found.message


def test_a_note_inside_a_paragraph_is_judged_because_it_becomes_a_pause() -> None:
    (found,) = placeholder_findings("## 1. One\n\nThe answer [as we saw] is here.\n", script=WHERE)
    assert found.code is Code.TAKE_PLACEHOLDER
    assert "pause" in found.message


def test_a_beat_inside_a_paragraph_is_left_alone() -> None:
    """A beat is a direction the parser turns into the pause the author asked for."""
    assert placeholder_findings("## 1. One\n\nOne thing [beat] and then another.\n", script=WHERE) == []


def test_a_digit_is_an_uncertain_judgement_against_its_section() -> None:
    """The author may want the voice to try "41", so the finding names it and decides nothing."""
    (segment,) = parse_script("## 3. Three\n\nAbout 41 percent of them.\n")
    (found,) = symbol_findings([segment], script=WHERE)
    assert found.code is Code.TAKE_SPOKEN_SYMBOL
    assert found.certainty is Certainty.UNCERTAIN
    assert found.location.section == 3
    assert "41" in found.message


def test_a_section_written_out_in_words_is_left_alone() -> None:
    (segment,) = parse_script("## 3. Three\n\nAbout forty one percent of them.\n")
    assert symbol_findings([segment], script=WHERE) == []


def test_the_two_rules_are_reported_by_one_call() -> None:
    segments = parse_script(SCRIPT)
    codes = {found.code for found in script_findings(SCRIPT, segments, script=WHERE)}
    assert codes == {Code.TAKE_PLACEHOLDER}


def test_every_judgement_names_the_script_it_is_about() -> None:
    segments = parse_script("## 1. One\n\nThe number is [NUMBER] at 41 percent.\n")
    found = script_findings("## 1. One\n\nThe number is [NUMBER] at 41 percent.\n", segments, script=WHERE)
    assert {one.location.file for one in found} == {WHERE}
    assert {one.code for one in found} == {Code.TAKE_PLACEHOLDER, Code.TAKE_SPOKEN_SYMBOL}
