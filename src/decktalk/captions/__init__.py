"""Captions, chapters and the files they are written to.

`layout.py` turns one section's words into caption cues, wrapped and grouped, and `files.py`
writes those cues as SubRip and WebVTT, the section chapters as ffmetadata, and the transcript
page a viewer reads instead of the film.

A word is the word model every result carries, so the times a caption is cut on are the times the
voice reported and the times `verify` measures, with nothing converted in between.
"""

from __future__ import annotations

from .files import (
    Chapter,
    Said,
    TranscriptSection,
    ffmetadata_escape,
    write_chapters,
    write_srt,
    write_transcript,
    write_vtt,
)
from .layout import CAPTION_MAX_CHARS, CaptionCue, caption_cues, display_words

__all__ = [
    "CAPTION_MAX_CHARS",
    "CaptionCue",
    "Chapter",
    "Said",
    "TranscriptSection",
    "caption_cues",
    "display_words",
    "ffmetadata_escape",
    "write_chapters",
    "write_srt",
    "write_transcript",
    "write_vtt",
]
