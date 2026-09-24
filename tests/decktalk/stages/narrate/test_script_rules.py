"""What the voice must never receive, and the two scans `check` judges a script by."""

from __future__ import annotations

import pytest

from decktalk.errors import InputError
from decktalk.inputs.script import parse_script
from decktalk.stages.narrate.script_rules import (
    ascending,
    check_script,
    script_refusals,
    shown,
    spoken_lines,
    symbol_tokens,
)

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
    numbered = dict(spoken_lines("# Notes\n\nnever spoken\n\n## 1. Open\n\nspoken\n"))
    assert "spoken" in numbered.values()
    assert "never spoken" not in numbered.values()


def test_a_refused_script_names_every_line_and_the_rule() -> None:
    with pytest.raises(InputError) as refused:
        check_script("script.md", "## 1. Open\n\nA ball {x} [which is red] falls.\n")
    assert "script.md has 2 thing(s)" in str(refused.value)
    assert "line 3" in str(refused.value)
    assert refused.value.hint is not None
    assert "[beat]" in refused.value.hint


def test_a_clean_script_raises_nothing() -> None:
    check_script("script.md", CLEAN)


def test_a_digit_or_a_symbol_is_named_word_by_word() -> None:
    (segment,) = parse_script("## 1. Open\n\nIt costs 40% of $2 today.\n")
    assert symbol_tokens(segment) == ("$2", "40%")


def test_a_section_the_voice_can_read_names_nothing() -> None:
    (segment,) = parse_script("## 1. Open\n\nForty per cent of two dollars.\n")
    assert symbol_tokens(segment) == ()


def test_a_finding_names_the_first_few_words_rather_than_all_of_them() -> None:
    assert shown(["a", "b", "c", "d", "e", "f"]) == "a, b, c, d, e"


def test_headings_that_ascend_are_in_the_one_order_the_index_holds() -> None:
    assert ascending(parse_script("## 1. One\n\nx\n\n## 2. Two\n\ny\n")) is None


def test_headings_that_count_backwards_name_the_pair_that_does_not_ascend() -> None:
    out_of_order = ascending(parse_script("## 2. Two\n\nx\n\n## 1. One\n\ny\n"))
    assert out_of_order is not None
    first, second = out_of_order
    assert (first.index, second.index) == (2, 1)
