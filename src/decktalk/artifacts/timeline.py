"""`Timeline` and `TimelineSection`, where every section and every word sits in the joined narration.

    build/narration/timeline.json   each section's start, end and words, absolute in narration.mp3

`align` resolves cue phrases against it, the recorder records each section for its span, and the
assembler cuts the picture to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from ..jsonio import as_json, read_json, write_json
from .words import Word


@dataclass
class TimelineSection:
    """One section on the narration clock, with its words at their absolute times."""

    title: str
    start: float
    end: float
    duration: float
    speech_end: float | None
    words: list[Word] = field(default_factory=list)
    lead_seconds: float = 0.0  # Silence joined in before the take, part of `duration`. The words already include it.

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            title=str(d["title"]),
            start=float(d["start"]),
            end=float(d["end"]),
            duration=float(d["duration"]),
            speech_end=None if d.get("speech_end") is None else float(d["speech_end"]),
            words=[Word.from_dict(w) for w in d.get("words", [])],
            lead_seconds=float(d.get("lead_seconds", 0.0)),
        )


@dataclass
class Timeline:
    """Section and word times, absolute in narration.mp3."""

    narration: str
    total_seconds: float
    sections: dict[str, TimelineSection]
    estimated: bool = False

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = read_json(path)
        return cls(
            narration=str(d.get("narration", "narration.mp3")),
            total_seconds=float(d.get("total_seconds", 0.0)),
            estimated=bool(d.get("estimated", False)),
            sections={k: TimelineSection.from_dict(v) for k, v in d.get("sections", {}).items()},
        )

    def save(self, path: Path) -> None:
        write_json(path, as_json(self), indent=1)

    def span(self, key: str) -> float | None:
        sec = self.sections.get(key)
        return None if sec is None else sec.end - sec.start

    @property
    def keys(self) -> list[str]:
        return sorted(self.sections)
