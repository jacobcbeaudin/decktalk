"""Captions, chapters and the files they are written to.

`layout.py` turns one section's words into caption cues, wrapped and grouped, and `files.py`
writes those cues as SubRip and WebVTT and the section chapters as ffmetadata.
"""

from __future__ import annotations

from .files import Chapter, ffmetadata_escape, write_chapters, write_srt, write_vtt
from .layout import CAPTION_MAX_CHARS, CaptionCue, caption_cues, display_words

__all__ = [
    "CAPTION_MAX_CHARS",
    "CaptionCue",
    "Chapter",
    "caption_cues",
    "display_words",
    "ffmetadata_escape",
    "write_chapters",
    "write_srt",
    "write_vtt",
]
