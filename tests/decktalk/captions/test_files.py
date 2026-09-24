"""The caption, chapter and transcript files written beside the final mp4."""

from __future__ import annotations

from decktalk.captions import CaptionCue, Chapter, Said, TranscriptSection, write_chapters, write_srt, write_vtt
from decktalk.captions.files import clock, ffmetadata_escape, write_transcript

CUES = [
    CaptionCue(start=0.5, end=2.25, lines=("A first block,", "a second beside it.")),
    CaptionCue(start=2.5, end=4.0, lines=("[ball bounces]",)),
]


def test_srt_numbers_every_cue_and_stamps_it_with_a_comma(tmp_path):
    path = tmp_path / "f.srt"
    write_srt(path, CUES)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("1\n00:00:00,500 --> 00:00:02,250\nA first block,\na second beside it.\n")
    assert "2\n00:00:02,500 --> 00:00:04,000\n[ball bounces]\n" in text


def test_vtt_opens_with_its_header_and_stamps_with_a_full_stop(tmp_path):
    path = tmp_path / "f.vtt"
    write_vtt(path, CUES)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT\n\n00:00:00.500 --> 00:00:02.250\n")


def test_a_chapter_title_survives_the_ffmetadata_escape(tmp_path):
    assert ffmetadata_escape("a=b;c#d") == "a\\=b\\;c\\#d"
    # A backslash is escaped too, or the escape of the next character would be read as part of it.
    assert ffmetadata_escape("a=b;c#d\\e") == "a\\=b\\;c\\#d\\\\e"
    path = tmp_path / "f.txt"
    write_chapters(path, [Chapter(0.0, 2.0, "One = two")])
    text = path.read_text(encoding="utf-8")
    assert text.startswith(";FFMETADATA1")
    assert "START=0\nEND=2000\ntitle=One \\= two" in text


def test_the_clock_drops_the_hour_under_an_hour():
    assert clock(0) == "0:00"
    assert clock(65.4) == "1:05"
    assert clock(3600) == "1:00:00"


def test_the_transcript_is_one_plain_page_a_viewer_can_read(tmp_path):
    path = tmp_path / "film-transcript.html"
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
    write_transcript(path, "film", sections, language="fr")
    text = path.read_text(encoding="utf-8")
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
