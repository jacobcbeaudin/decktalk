"""The caption, chapter and transcript files `assemble` writes beside the final mp4.

build/final/<name>.srt              the caption cues as SubRip
build/final/<name>.vtt              the same cues as WebVTT
build/final/<name>.chapters.txt     an ffmetadata file with one [CHAPTER] per section
build/final/<name>-transcript.html  the whole film as a page: one heading per chapter, the spoken
                                    text under it, and the description of each reveal

The transcript is the media alternative a viewer who cannot see or cannot hear the film reads
instead. Captions carry speech alone, so the transcript is the only place the picture is written
down, and it is one plain page with no script and no stylesheet of its own.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from .layout import CaptionCue


def _stamp(seconds: float, sep: str) -> str:
    ms = round(seconds * 1000)
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
            f"START={round(ch.start * 1000)}",
            f"END={round(ch.end * 1000)}",
            f"title={ffmetadata_escape(ch.title)}",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Said:
    """One section's paragraph of the transcript: its speech, or a note about what plays instead."""

    text: str
    note: bool = False  # The text says what plays here rather than what was said, such as a clip.


@dataclass(frozen=True)
class TranscriptSection:
    """One chapter of the transcript: its heading, what was said under it, and what was shown.

    A chapter may cover several sections, so `said` is one entry per section in the order those
    sections play, and a section that spoke nothing carries a note about what plays there instead.
    """

    chapter: str
    start: float
    end: float
    said: tuple[Said, ...] = ()
    describes: tuple[tuple[float, str], ...] = ()  # (second in the film, the reveal's own description)


TRANSCRIPT_CSS = (
    "body{margin:0 auto;padding:2rem 1.25rem 4rem;max-width:44rem;"
    "font:16px/1.65 -apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif;color:#16181d}"
    "h1{font-size:1.9rem;line-height:1.2;margin:0 0 .25rem}"
    "h2{font-size:1.15rem;margin:2.25rem 0 .35rem}"
    ".at{color:#5b6573;font-variant-numeric:tabular-nums;font-size:.85rem}"
    "ul{margin:.6rem 0 0;padding-left:1.1rem;color:#333a45}"
    "li{margin:.2rem 0}.note{color:#5b6573;font-style:italic}"
    "@media(prefers-color-scheme:dark){body{background:#0e1116;color:#e8eaee}"
    "h2{color:#f4f6f8}ul{color:#c3c8d0}.at,.note{color:#9aa4b2}}"
)


def clock(seconds: float) -> str:
    """A time a reader can find in a player: h:mm:ss, or m:ss under an hour."""
    whole = round(seconds)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def write_transcript(path: Path, title: str, sections: list[TranscriptSection], *, language: str = "en") -> None:
    """Write the whole film as one plain page: a heading per chapter, the speech, and each description."""
    body = [
        "<!doctype html>",
        f'<html lang="{html.escape(language, quote=True)}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{html.escape(title)} transcript</title>",
        f"<style>{TRANSCRIPT_CSS}</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
        '<p class="at">Transcript of the whole video, in order.</p>',
    ]
    for section in sections:
        body.append(f"<h2>{html.escape(section.chapter)}</h2>")
        body.append(f'<p class="at">{clock(section.start)} to {clock(section.end)}</p>')
        for said in section.said:
            style = ' class="note"' if said.note else ""
            body.append(f"<p{style}>{html.escape(said.text)}</p>")
        if section.describes:
            rows = "".join(
                f'<li><span class="at">{clock(at)}</span> {html.escape(text)}</li>'
                for at, text in sorted(section.describes)
            )
            body.append(f"<ul>{rows}</ul>")
    body.append("</body></html>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
