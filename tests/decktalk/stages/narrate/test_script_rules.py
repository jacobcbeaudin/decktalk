"""The rules a script must obey: what the voice must never receive, and what is only a note."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk import ConfigError
from decktalk.model.script import Segment, parse_script
from decktalk.stages.narrate.script_rules import check_script, script_refusals, symbol_findings
from decktalk.verdicts import Verdict


def _segment(text: str, index: int = 1) -> Segment:
    return Segment(index=index, title="T", slug="t", text=text)


@pytest.mark.parametrize(
    ("line", "wanted"),
    [
        ("Learning <!-- rewrite this --> nudges the knobs.", "an HTML comment"),
        ("It guesses {name} again.", "a brace"),
        ("A bowl. [maybe cut this] A ball.", "[maybe cut this] inside a paragraph"),
    ],
)
def test_the_script_refuses_what_the_voice_would_read_or_swallow(line, wanted):
    markdown = f"# Notes\n\nA {{brace}} up here is never spoken.\n\n## 1. Open\n\n{line}\n"
    ((number, what),) = script_refusals(markdown)
    assert number == 7 and what.startswith(wanted)
    with pytest.raises(ConfigError) as caught:
        check_script(str(Path("script.md")), markdown)
    assert "line 7" in str(caught.value) and "[beat] or [pause N]" in str(caught.value)


def test_the_script_allows_a_direction_a_beat_a_pause_a_placeholder_and_a_link():
    markdown = (
        "## 1. Open\n\n[Deck scene 1. A bowl and a ball appear.]\n\n"
        "A bowl. [beat] A ball. [pause 1.5] Now [NUMBER] of them, see the [docs](http://x).\n"
    )
    assert script_refusals(markdown) == []
    assert len(parse_script(markdown)[0].placeholders) == 1


def test_digits_and_symbols_are_a_note_and_not_a_refusal():
    segment = _segment("It costs 41% more, and 100% of nothing.")
    (row,) = symbol_findings([segment], "script.md")
    assert row.verdict is Verdict.SPOKEN_SYMBOL and not row.verdict.certain
    assert row.section == 1 and row.where == "script.md"
    assert "100%" in row.detail and "41%" in row.detail
    assert symbol_findings([_segment("Forty one percent more.")], "script.md") == []
    assert script_refusals("## 1. A\n\nIt costs 41% more.\n") == []
