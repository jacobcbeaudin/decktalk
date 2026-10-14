"""The caption, chapter and transcript files written beside the final mp4."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from decktalk.captions import CaptionCue, Chapter, Said, TranscriptSection, chapters_text, srt_text, vtt_text
from decktalk.captions.files import clock, ffmetadata_escape, transcript_html

CUES = [
    CaptionCue(start=0.5, end=2.25, lines=("A first block,", "a second beside it.")),
    CaptionCue(start=2.5, end=4.0, lines=("[ball bounces]",)),
]


def test_srt_numbers_every_cue_and_stamps_it_with_a_comma():
    text = srt_text(CUES)
    assert text.startswith("1\n00:00:00,500 --> 00:00:02,250\nA first block,\na second beside it.\n")
    assert "2\n00:00:02,500 --> 00:00:04,000\n[ball bounces]\n" in text


def test_vtt_opens_with_its_header_and_stamps_with_a_full_stop():
    text = vtt_text(CUES)
    assert text.startswith("WEBVTT\n\n00:00:00.500 --> 00:00:02.250\n")


VOICED = CaptionCue(start=0.0, end=2.0, lines=("[door opens]", "Says who?", "Every demo says that.", "[ball bounces]"),
                    voice="engineer", turn=1, named=True, spoken=(1, 3))  # fmt: skip
"""A cue a voice speaks, with a sound caption before its speech and another after it."""


def test_vtt_tags_the_lines_a_voice_speaks_with_its_name():
    """A WebVTT voice span names who speaks, and the sounds either side of the speech stay outside it."""
    text = vtt_text([VOICED])
    assert "[door opens]\n<v engineer>Says who?\nEvery demo says that.</v>\n[ball bounces]\n" in text


def test_srt_puts_the_voice_before_the_first_line_it_speaks():
    text = srt_text([VOICED])
    assert "[door opens]\nengineer: Says who?\nEvery demo says that.\n[ball bounces]\n" in text


VTT_ESCAPES = {"&amp;": "&", "&lt;": "<", "&gt;": ">"}
"""The character references the WebVTT spec names for the three characters cue text may not carry bare."""


def vtt_read(payload: str) -> tuple[str, list[str]]:
    """What a WebVTT parser reads from one cue's text: the words, and every tag, refusing a bare `&`, `<` or `>`."""
    words: list[str] = []
    tags: list[str] = []
    at = 0
    while at < len(payload):
        char = payload[at]
        if char == "<":
            end = payload.index(">", at)
            tags.append(payload[at : end + 1])
            at = end + 1
        elif char == "&":
            escape = next((name for name in VTT_ESCAPES if payload.startswith(name, at)), None)
            assert escape is not None, f"a bare & at {at} of {payload!r}"
            words.append(VTT_ESCAPES[escape])
            at += len(escape)
        else:
            assert char != ">", f"a bare > at {at} of {payload!r}"
            words.append(char)
            at += 1
    return "".join(words), tags


def test_vtt_escapes_the_characters_a_cue_may_not_carry_and_keeps_its_voice_span():
    """`Q&A` and `a < b` were written bare, which a player reads as a broken escape and a tag."""
    said = CaptionCue(start=0.0, end=2.0, lines=("Q&A: is a < b?", "Yes, b > a & a <v x> b."),
                      voice="engineer", turn=1, named=True)  # fmt: skip
    payload = vtt_text([said]).split("\n", 3)[3].rstrip("\n")
    words, tags = vtt_read(payload)
    assert words == "\n".join(said.lines)
    assert tags == ["<v engineer>", "</v>"]


@given(st.lists(st.text(alphabet=st.characters(exclude_characters="\n\r"), min_size=1), min_size=1, max_size=3))
def test_any_caption_line_survives_the_vtt_escape(lines):
    cue = CaptionCue(start=0.0, end=1.0, lines=tuple(lines))
    words, tags = vtt_read(vtt_text([cue]).split("\n", 3)[3].rstrip("\n"))
    assert (words, tags) == ("\n".join(lines), [])


@pytest.mark.parametrize("voice", [None, "engineer"])
def test_a_cue_no_voice_is_named_on_is_written_as_it_reads(voice):
    """A cue knows the voice that speaks it, and the files name that voice only where the project asks them to."""
    plain = CaptionCue(start=0.0, end=1.0, lines=("Hello.",), voice=voice, turn=1 if voice else None)
    assert "<v" not in vtt_text([plain]) and ": " not in srt_text([plain]).split("\n", 2)[2]


def ffmetadata_read(escaped: str) -> str:
    """What ffmpeg reads back from one escaped value, refusing a special character left bare."""
    read, rest = [], iter(escaped)
    for char in rest:
        assert char not in "=;#\n", f"{char!r} is bare in {escaped!r}"
        read.append(next(rest) if char == "\\" else char)
    return "".join(read)


@given(st.text())
def test_any_title_survives_the_ffmetadata_escape(title):
    """A backslash is escaped too, or the escape of the next character would be read as part of it."""
    assert ffmetadata_read(ffmetadata_escape(title)) == title


def test_a_chapter_title_is_written_escaped():
    text = chapters_text([Chapter(0.0, 2.0, "One = two")])
    assert text.startswith(";FFMETADATA1")
    assert "START=0\nEND=2000\ntitle=One \\= two" in text


def test_the_clock_drops_the_hour_under_an_hour():
    assert clock(0) == "0:00"
    assert clock(65.4) == "1:05"
    assert clock(3600) == "1:00:00"


def test_the_transcript_is_one_plain_page_a_viewer_can_read():
    sections = [
        TranscriptSection(
            chapter="Open & close",
            start=0.0,
            end=12.5,
            said=(Said("A bowl. A ball."), Said("A second section under the same heading.")),
            describes=((2.4, "The bowl draws itself"), (5.1, "A ball steps down it")),
        ),
        TranscriptSection(
            chapter="B-roll", start=12.5, end=15.0, said=(Said("A clip plays here: media/b.mp4.", note=True),)
        ),
    ]
    text = transcript_html("film", sections, language="fr")
    assert text.startswith("<!doctype html>") and '<html lang="fr">' in text
    assert "<title>film transcript</title>" in text
    assert "<h2>Open &amp; close</h2>" in text  # a title is escaped, never injected
    assert "<p>A bowl. A ball.</p>" in text
    assert "<p>A second section under the same heading.</p>" in text
    assert "0:00 to 0:12" in text and "0:12 to 0:15" in text
    assert "The bowl draws itself" in text and "A ball steps down it" in text
    assert "A clip plays here: media/b.mp4." in text
    # The page carries no script and no external reference, so it opens anywhere and offline.
    assert "<script" not in text and "http" not in text


def test_a_turn_of_a_dialogue_names_its_voice_in_an_element_of_its_own():
    """A screen reader reads the voice, then the line, and the two never run together into one word."""
    said = (Said("Says who?", voice="engineer"), Said("Nobody does.", voice="narrator"))
    text = transcript_html("film", [TranscriptSection(chapter="Talk", start=0.0, end=4.0, said=said)])
    assert '<p><span class="who">engineer:</span> Says who?</p>' in text
    assert '<p><span class="who">narrator:</span> Nobody does.</p>' in text


def test_a_paragraph_of_one_voice_names_nobody():
    text = transcript_html("film", [TranscriptSection(chapter="Open", start=0.0, end=4.0, said=(Said("Hello."),))])
    assert "<p>Hello.</p>" in text and 'class="who"' not in text


def test_a_time_is_written_to_the_nearest_second_the_one_way_a_person_reads_it() -> None:
    """The terminal truncated where the transcript rounded, so 59.6 s read as 0:59 in one and 1:00 in the other."""
    assert (clock(59.6), clock(61.0), clock(3725.0)) == ("1:00", "1:01", "1:02:05")
