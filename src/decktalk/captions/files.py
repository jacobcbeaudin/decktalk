"""The caption and chapter files `assemble` writes beside the final mp4.

build/out/<name>.srt           the caption cues as SubRip
build/out/<name>.vtt           the same cues as WebVTT
build/out/<name>.chapters.txt  an ffmetadata file with one [CHAPTER] per section
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .layout import CaptionCue


def _stamp(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(path: Path, cues: list[CaptionCue]) -> None:
    blocks = [f"{i}\n{_stamp(c.start, ',')} --> {_stamp(c.end, ',')}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")


def write_vtt(path: Path, cues: list[CaptionCue]) -> None:
    blocks = [f"{_stamp(c.start, '.')} --> {_stamp(c.end, '.')}\n{c.text}\n" for c in cues]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("WEBVTT\n\n" + "\n".join(blocks), encoding="utf-8")


@dataclass(frozen=True)
class Chapter:
    start: float
    end: float
    title: str


def ffmetadata_escape(value: str) -> str:
    """Escape the characters ffmetadata treats specially, so a title survives the round trip."""
    out = value.replace("\\", "\\\\")
    for ch in "=;#":
        out = out.replace(ch, "\\" + ch)
    return out.replace("\n", "\\\n")


def write_chapters(path: Path, chapters: list[Chapter]) -> None:
    """An ffmetadata file whose [CHAPTER] blocks ffmpeg muxes with -map_metadata."""
    lines = [";FFMETADATA1"]
    for ch in chapters:
        lines += [
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(ch.start * 1000))}",
            f"END={int(round(ch.end * 1000))}",
            f"title={ffmetadata_escape(ch.title)}",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
