from decktalk.assemble import timeline_targets
from decktalk.beats import find_phrase, parse_beats_string, resolve_cue
from decktalk.narrate import estimated_words, parse_script, strip_markdown

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
    assert one.title == "Open"
    assert one.target_seconds == 20
    assert one.placeholders == ["NUMBER"]
    assert "**" not in one.text and "`" not in one.text and "http" not in one.text
    # a direction between two spoken paragraphs becomes a break after the first
    assert one.text.count("<break") == 1
    assert one.words == 15


def test_strip_markdown_keeps_placeholders():
    assert "[VENUE]" in strip_markdown("At [VENUE] tonight. [not spoken]")


def test_find_phrase_and_resolve():
    words = [
        {"word": "Hello", "start": 0.0, "end": 0.3},
        {"word": "one", "start": 0.4, "end": 0.6},
        {"word": "in", "start": 0.7, "end": 0.8},
        {"word": "ten", "start": 0.9, "end": 1.2},
        {"word": "one", "start": 1.5, "end": 1.7},
    ]
    assert find_phrase(words, "one in ten") == 1
    assert find_phrase(words, "one", occurrence=2) == 4
    assert find_phrase(words, "missing") is None
    assert resolve_cue({"step": "a", "on": "in ten", "offset": 0.1}, words) == 0.8
    assert resolve_cue({"step": "a", "on": "$end"}, words) == 1.7
    assert resolve_cue({"step": "a", "on": "$start", "offset": 2}, words) == 2.0


def test_parse_beats_string():
    assert parse_beats_string("a@1.5,panel:bought@2,bad,x@y") == {"a": 1.5, "panel:bought": 2.0}


def test_timeline_targets_are_frame_exact():
    tl = {"sections": {"01": {"start": 0, "end": 1.02}, "02": {"start": 1.02, "end": 2.5}}}
    t = timeline_targets(tl)
    assert abs(t["01"] - 1.0333) < 1e-3
    assert abs(t["02"] - 1.4667) < 1e-3


def test_estimated_words_span_the_duration():
    seg = parse_script("## 1. A\n\none two three four five")[0]
    seg.lead_break = True
    words = estimated_words(seg, 5.0)
    assert [w["word"] for w in words] == ["one", "two", "three", "four", "five"]
    assert words[0]["start"] == 0.7 and words[-1]["end"] < 5.0 - 0.35
