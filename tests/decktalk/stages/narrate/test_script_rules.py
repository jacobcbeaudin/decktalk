"""What the voice must never receive, and the two scans `check` judges a script by."""

from __future__ import annotations

import pytest

from decktalk.errors import InputError
from decktalk.findings import Code
from decktalk.inputs.script import parse_script
from decktalk.stages.narrate.script_rules import (
    ascending,
    check_script,
    script_refusals,
    shown,
    spoken_lines,
    symbol_tokens,
    voice_refusals,
)

NAMES = ("narrator", "guest")
"""The voices of the project every script here is judged against, the default first."""

CLEAN = """## 1. Open

[A whole line of direction, which nobody speaks.]

A bowl. [beat] A ball. [pause 2] Then it falls.

See [the docs](https://example.test) and fill [NUMBER] in.
"""


def test_a_clean_script_is_refused_nothing() -> None:
    assert script_refusals(CLEAN) == []


@pytest.mark.parametrize(
    ("line", "what"),
    [
        ("A ball <!-- and a note --> falls.", "an HTML comment"),
        ("A ball {x} falls.", "a brace"),
        ("A ball [which is red] falls.", "inside a paragraph"),
    ],
)
def test_the_voice_never_receives_what_it_would_read_out(line: str, what: str) -> None:
    (found,) = script_refusals(f"## 1. Open\n\n{line}\n")
    assert found[0] == 3
    assert what in found[1]


def test_only_the_body_of_a_numbered_section_is_spoken() -> None:
    spoken = [line for _number, _section, line in spoken_lines("# Notes\n\nnever spoken\n\n## 1. Open\n\nspoken\n")]
    assert "spoken" in spoken
    assert "never spoken" not in spoken


def test_a_spoken_line_knows_which_section_it_is_in() -> None:
    """A finding about one line is more use with the section an author would open to fix it."""
    rows = list(spoken_lines("# Demo\n\n## 1. One\n\nfirst\n\n## 2. Two\n\nsecond\n"))
    assert [(number, section) for number, section, line in rows if line] == [(5, 1), (9, 2)]


def test_a_refused_script_names_every_line_and_the_rule() -> None:
    with pytest.raises(InputError) as refused:
        check_script("script.md", "## 1. Open\n\nA ball {x} [which is red] falls.\n", NAMES)
    assert "script.md has 2 things" in str(refused.value)
    assert "line 3" in str(refused.value)
    assert refused.value.hint is not None
    assert "[beat]" in refused.value.hint


def test_a_clean_script_raises_nothing() -> None:
    check_script("script.md", CLEAN, NAMES)


def test_a_digit_or_a_symbol_is_named_word_by_word() -> None:
    (section,) = parse_script("## 1. Open\n\nIt costs 40% of $2 today.\n")
    assert symbol_tokens(section) == ("$2", "40%")


def test_a_section_the_voice_can_read_names_nothing() -> None:
    (section,) = parse_script("## 1. Open\n\nForty per cent of two dollars.\n")
    assert symbol_tokens(section) == ()


def test_a_finding_names_the_first_few_words_rather_than_all_of_them() -> None:
    assert shown(["a", "b", "c", "d", "e", "f"]) == "a, b, c, d, e"


def test_headings_that_ascend_are_in_the_one_order_the_index_holds() -> None:
    assert ascending(parse_script("## 1. One\n\nx\n\n## 2. Two\n\ny\n")) is None


def test_headings_that_count_backwards_name_the_pair_that_does_not_ascend() -> None:
    out_of_order = ascending(parse_script("## 2. Two\n\nx\n\n## 1. One\n\ny\n"))
    assert out_of_order is not None
    first, second = out_of_order
    assert (first.number, second.number) == (2, 1)


# ---- voice directions ---------------------------------------------------------------------------

UNKNOWN = Code.SCRIPT_VOICE_UNKNOWN
UNMARKED = Code.SCRIPT_VOICE_UNMARKED


def test_a_direction_naming_no_declared_voice_is_refused_with_the_nearest_name() -> None:
    script = "## 1. Talk\n\n[voice narrator]\n\nHello.\n\n[voice gest]\n\nHi.\n"
    assert voice_refusals(script, NAMES) == [
        (7, UNKNOWN, "[voice gest] names no voice, and the voices are narrator, guest. Did you mean 'guest'?")
    ]


@pytest.mark.parametrize("claimed", ["[Voice over the chart.]", "[voice.guest]", "[voice]", "[voice guest please]"])
def test_a_line_that_opens_with_voice_and_is_no_direction_is_refused(claimed: str) -> None:
    inner = claimed[1:-1]
    rows = voice_refusals(f"## 1. Talk\n\nHello.\n\n{claimed}\n\nHi.\n", NAMES)
    assert rows == [(5, UNKNOWN, f"[{inner}] is not a voice direction, which is written [voice NAME]")]


def test_a_section_that_names_a_voice_names_one_before_its_first_turn() -> None:
    script = "## 1. Open\n\nNobody names me.\n\n## 2. Talk\n\nHello.\n\n[voice guest]\n\nHi.\n"
    assert voice_refusals(script, NAMES) == [
        (7, UNMARKED, "section 2 names a voice, and the turn that starts here names none")
    ]


def test_a_section_that_names_no_voice_needs_no_direction() -> None:
    assert voice_refusals("## 1. Open\n\nHello.\n\n[beat]\n\nHi.\n", NAMES) == []
    assert voice_refusals("## 1. Open\n\n[voice guest]\n\nHello.\n", NAMES) == []


def test_a_voice_direction_inside_a_paragraph_keeps_its_refusal() -> None:
    with pytest.raises(InputError) as refused:
        check_script("script.md", "## 1. Talk\n\n[voice guest] Hello there.\n", NAMES)
    assert "line 3: [voice guest] inside a paragraph" in str(refused.value)


def test_narrate_is_refused_every_voice_direction_it_cannot_read_at_once() -> None:
    script = "## 1. Talk\n\nHello.\n\n[voice gest]\n\nHi.\n\n[voice]\n\nBye.\n"
    with pytest.raises(InputError) as refused:
        check_script("script.md", script, NAMES)
    assert str(refused.value) == (
        "script.md has 3 voice directions narrate cannot read:\n"
        "  line 3: section 1 names a voice, and the turn that starts here names none\n"
        "  line 5: [voice gest] names no voice, and the voices are narrator, guest. Did you mean 'guest'?\n"
        "  line 9: [voice] is not a voice direction, which is written [voice NAME]"
    )
    assert refused.value.hint == (
        "A voice direction is a line holding only [voice NAME], where NAME is a [voices.NAME] table of "
        "decktalk.toml. A section that names a voice names one before every turn, its first included."
    )
