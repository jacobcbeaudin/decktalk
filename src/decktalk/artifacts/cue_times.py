"""`CueTimes` and `CueTime`, every resolved cue as an object.

    build/cue-times.json   {"estimated": false, "sections": {"03": [{"cue", "on", "at", "word_at"}]}}

A row's keys mirror `cues.json`: `cue` is the id the page understands and `on` is the phrase it
was matched against. `at` mirrors the `@` of the `?cues=` value the recorder passes to the page,
in seconds after the section starts. `word_at` is where the matched word itself begins, before
the cue's own offset, which is what `verify` listens for when a build without voice clicks on
every word start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from ..jsonio import as_json, read_json, write_json


@dataclass(frozen=True)
class CueTime:
    """One cue resolved against its section's words."""

    cue: str
    on: str
    at: float
    word_at: float | None = None  # The matched word's start, without the cue's offset.

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        word_at = d.get("word_at")
        return cls(
            cue=str(d["cue"]),
            on=str(d.get("on", "")),
            at=float(d["at"]),
            word_at=None if word_at is None else float(word_at),
        )


@dataclass
class CueTimes:
    """The resolved cues of every section, in the order `align` resolved them."""

    sections: dict[str, list[CueTime]] = field(default_factory=dict)
    estimated: bool = False  # The times come from the estimated words of a build without voice.

    @classmethod
    def load(cls, path: Path) -> Self:
        if not path.exists():
            return cls()
        d = read_json(path)
        rows = {k: [CueTime.from_dict(c) for c in v] for k, v in d.get("sections", {}).items()}
        return cls(sections=rows, estimated=bool(d.get("estimated", False)))

    def save(self, path: Path) -> None:
        rows = {k: v for k, v in sorted(self.sections.items()) if v}
        write_json(path, {"estimated": self.estimated, "sections": as_json(rows)})

    def query(self, key: str) -> str | None:
        """The `?cues=` value for a section, or None when it has no resolved cue."""
        rows = self.sections.get(key)
        if not rows:
            return None
        return ",".join(f"{row.cue}@{row.at}" for row in rows)

    def times(self, key: str) -> dict[str, float]:
        """The section's cue ids mapped to their resolved seconds."""
        return {row.cue: row.at for row in self.sections.get(key, [])}

    def row(self, key: str, cue: str) -> CueTime | None:
        return next((r for r in self.sections.get(key, []) if r.cue == cue), None)

    def get(self, key: str, cue: str) -> float | None:
        """The cue's resolved second, or None when it was never resolved."""
        row = self.row(key, cue)
        return None if row is None else row.at

    def word_at(self, key: str, cue: str) -> float | None:
        """Where the cue's matched word begins, or None when the cue has no word behind it."""
        row = self.row(key, cue)
        return None if row is None else row.word_at
