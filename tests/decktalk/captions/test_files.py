"""The caption, chapter and transcript files written beside the final mp4."""

from __future__ import annotations

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


def test_a_time_is_written_to_the_nearest_second_the_one_way_a_person_reads_it() -> None:
    """The terminal truncated where the transcript rounded, so 59.6 s read as 0:59 in one and 1:00 in the other."""
    assert (clock(59.6), clock(61.0), clock(3725.0)) == ("1:00", "1:01", "1:02:05")
