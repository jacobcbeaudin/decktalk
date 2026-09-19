"""`Takes` and `Take`, the index of what the voice recorded and what each take cost.

    build/narration/takes.json   one row per narrated section, keyed by its two-digit key

The index is also the cache: a row's `hash` covers everything that changes the audio, so a run
that finds the same hash under the same file name spends nothing. A row of a build without voice
carries the hash `silent`, and `estimated` says the whole index is placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from ..jsonio import as_json, read_json, write_json


@dataclass
class Take:
    """One section's recorded narration: its files, its cache key, and how long it runs."""

    index: int
    chapter: str
    file: str
    words_file: str
    hash: str
    word_count: int
    estimated_seconds: float
    duration_seconds: float
    target_seconds: float | None = None
    speech_end_seconds: float | None = None
    tail_padded_seconds: float = 0.0
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
        self.sections = dict(sorted(self.sections.items()))
        self.total_seconds = round(sum(s.duration_seconds for s in self.sections.values()), 3)
        write_json(path, as_json(self))
