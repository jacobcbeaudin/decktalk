"""`Takes` and `Take`, the index of what the voice recorded and what each take cost.

    build/narration/takes.json   one row per narrated section, keyed by its two-digit key

A row says which take a section plays, and a take is named by the content hash that produced it,
so the index is a map from a section number to a piece of content and never the other way round.
Renumbering a section rewrites one row and moves no file, and two sections with the same words
name one take. `voiced` is false on a placeholder take that a run without voice wrote, and
`estimated` is true when any row is such a take, which is what tells `assemble` that the times it
is cutting to are guesses.
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
    speech_end_seconds: float | None = None
    tail_padded_seconds: float = 0.0  # Silence padded into the file, which only this project's own take gets.
    tail_joined_seconds: float = 0.0  # Silence joined in after the take, which a shared take gets instead.
    lead_seconds: float = 0.0  # Silence joined in before the take, which is not part of the file.
    spoken: str = ""  # The words the voice says, with the script's punctuation, which captions borrow.

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

        A take holds no silence of its own, so the narration runs for every take plus every lead and
        every tail that the join rather than the file carries.
        """
        self.sections = dict(sorted(self.sections.items()))
        spans = (s.duration_seconds + s.lead_seconds + s.tail_joined_seconds for s in self.sections.values())
        self.total_seconds = round(sum(spans), 3)
        self.estimated = any(not s.voiced for s in self.sections.values())
        write_json(path, as_json(self))

    @property
    def voiced_keys(self) -> list[str]:
        """The sections that hold a paid take, which a run without voice must not replace."""
        return sorted(key for key, take in self.sections.items() if take.voiced)
