"""`script.md`: the sections it declares, the directions between its paragraphs, and what the voice hears."""

from __future__ import annotations

from decktalk.inputs.script import BREAK_RE, parse_script, strip_markdown
from decktalk.settings import Settings

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
    assert [s.index for s in segs] == [0, 1]
    one = segs[1]
    assert one.title == "Open" and one.target_seconds == 20 and one.placeholders == ["NUMBER"]
    assert "**" not in one.text and "`" not in one.text and "http" not in one.text
    assert "<break" not in one.text and one.text.count(" —") == 1  # a direction between paragraphs becomes one beat
    assert one.word_count == 15


def test_strip_markdown_keeps_placeholders():
    assert "[VENUE]" in strip_markdown("At [VENUE] tonight. [not spoken]")


def test_pause_direction_yields_a_timed_break():
    seg = parse_script("## 1. A\n\nThink about it.\n\n[pause 3]\n\nOnly two x is left. [beat] Done.")[0]
    assert BREAK_RE.findall(seg.text) == ["3"]  # only a timed pause becomes a break tag
    assert seg.text.count(" —") == 1  # the beat is a dash
    assert 'Think about it. <break time="3s" />' in seg.text
    assert seg.spoken == "Think about it. Only two x is left. Done."
    # The silent placeholder honours the declared pauses, so the pause lengthens the section.
    short = parse_script("## 1. A\n\nThink about it.\n\nOnly two x is left. Done.")[0]
    cfg = Settings().narration
    assert abs((seg.silent_seconds(cfg) - short.silent_seconds(cfg)) - 3.7) < 1e-6


def test_pause_direction_accepts_decimals_and_case():
    seg = parse_script("## 1. A\n\nOne. [Pause 1.5] Two.")[0]
    assert '<break time="1.5s" />' in seg.text
    assert seg.spoken == "One. Two."
