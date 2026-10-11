"""The caption files a build writes, read back as the cues they hold, for the suites that read a finished film."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple


class Caption(NamedTuple):
    """One cue of a .srt or a .vtt file: its start and end in seconds and its text as the file writes it."""

    start: float
    end: float
    text: str


def stamp_seconds(value: str) -> float:
    """One timestamp in seconds, which is hours, minutes, seconds and the millis after a comma or a full stop."""
    hms, millis = value.strip().replace(",", ".").rsplit(".", 1)
    hours, minutes, seconds = hms.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def caption_cues(path: Path) -> list[Caption]:
    """Every cue of a SubRip or WebVTT file, in the order it plays."""
    rows: list[Caption] = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        stamps = [line for line in lines if " --> " in line]
        if not stamps:
            continue
        start, end = (stamp_seconds(value.split()[0]) for value in stamps[0].split(" --> "))
        rows.append(Caption(start, end, "\n".join(lines[lines.index(stamps[0]) + 1 :])))
    return rows
