"""`Takes` and `Take`, the index of what the voice recorded, and the narration clock it makes.

    build/narration/takes.json   one row per narrated section, keyed by its two-digit key

A row says which take a section plays, and a take is named by the content hash that produced it,
so the index is a map from a section number to a piece of content and never the other way round.
Renumbering a section rewrites one row and moves no file, and two sections with the same words
name one take. `voiced` is false on a placeholder take that a run without voice wrote, and
`estimated` is true when any row is such a take, which is what tells `assemble` that the times it
is cutting to are guesses.

The index is also the time base. A section runs for its lead, then its take up to where the take's
sound ends, then its tail, and the sections run in key order, so where each one sits in the joined
narration is arithmetic over the rows rather than a second file that can disagree with them. Every
one of those three numbers is a pure function of the take's own bytes and its own section's
settings, so a section lands the same way whether the run voiced its take or found it cached, and
whatever happened to the sections either side of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from ..jsonio import as_json, read_json, write_json


@dataclass
class Take:
    """One section's recorded narration: its files, its cache key, and how long it runs."""

    index: int  # The section this take plays for, which is display only: the hash is the identity.
    chapter: str
    file: str
    words_file: str
    hash: str
    word_count: int
    estimated_seconds: float
    duration_seconds: float
    voiced: bool = True  # False on the placeholder a run without voice wrote.
    target_seconds: float | None = None
    speech_end_seconds: float | None = None  # Where the take's last word ends, from its words file.
    sound_end_seconds: float | None = None  # Where the take's sound ends, measured from its own bytes.
    lead_seconds: float = 0.0  # Silence placed before the take, which is not part of the file.
    tail_seconds: float = 0.0  # Silence placed after sound_end_seconds, which is not part of the file either.
    spoken: str = ""  # The words the voice says, with the script's punctuation, which captions borrow.

    @property
    def span_seconds(self) -> float:
        """How long the section runs in the joined narration: its lead, its take to its last sound, and its tail."""
        end = self.duration_seconds if self.sound_end_seconds is None else self.sound_end_seconds
        return round(self.lead_seconds + end + self.tail_seconds, 3)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Takes:
    """The take index: what was voiced, with what, and how long the whole narration runs."""

    script: str
    model: str
    output_format: str
    estimated: bool = False
    estimate_basis: str = ""
    sections: dict[str, Take] = field(default_factory=dict)
    total_seconds: float = 0.0

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = read_json(path)
        rows = {k: Take.from_dict(v) for k, v in d.get("sections", {}).items()}
        return cls(
            script=str(d.get("script", "")),
            model=str(d.get("model", "")),
            output_format=str(d.get("output_format", "")),
            estimated=bool(d.get("estimated", False)),
            estimate_basis=str(d.get("estimate_basis", "")),
            sections=dict(sorted(rows.items())),
            total_seconds=float(d.get("total_seconds", 0.0)),
        )

    def save(self, path: Path) -> None:
        """Write the index, with the two totals it carries recomputed from its rows.

        The narration runs for every section's span, which is its lead, its take to its last sound
        and its tail.
        """
        self.sections = dict(sorted(self.sections.items()))
        self.total_seconds = round(sum(s.span_seconds for s in self.sections.values()), 3)
        self.estimated = any(not s.voiced for s in self.sections.values())
        write_json(path, as_json(self))

    @property
    def voiced_keys(self) -> list[str]:
        """The sections that hold a paid take, which a run without voice must not replace."""
        return sorted(key for key, take in self.sections.items() if take.voiced)

    # ---- the narration clock ----------------------------------------------------------

    @property
    def keys(self) -> list[str]:
        """Every narrated section, in the order the takes are joined, which is section order."""
        return sorted(self.sections)

    def span(self, key: str) -> float | None:
        """How long the section runs in the joined narration: its lead, its take to its last sound, and its tail."""
        take = self.sections.get(key)
        return None if take is None else take.span_seconds

    def start(self, key: str) -> float | None:
        """Where the section begins in the joined narration, or None when it has no take."""
        return self.starts.get(key)

    def end(self, key: str) -> float | None:
        """Where the section ends in the joined narration, or None when it has no take."""
        start, span = self.starts.get(key), self.span(key)
        return None if start is None or span is None else round(start + span, 3)

    @property
    def starts(self) -> dict[str, float]:
        """Each section's start in the joined narration, added up in the order the takes are joined."""
        at, out = 0.0, {}
        for key in self.keys:
            out[key] = round(at, 3)
            at += self.span(key) or 0.0
        return out

    def speech_end_seconds(self, key: str) -> float | None:
        """Where the last word of the section lands in the joined narration, or None when it says nothing."""
        take, start = self.sections.get(key), self.starts.get(key)
        if take is None or start is None or take.speech_end_seconds is None:
            return None
        return round(start + take.lead_seconds + take.speech_end_seconds, 3)
