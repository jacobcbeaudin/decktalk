"""`Cuts` and `Cut`, the cut list: where every section sits in the finished film.

    build/out/cuts.json   one row per section, in the order they play

This is the one record of the shape of a film. The transcript page, a caption reader and anything
that wants to jump to a section read it instead of adding up section files, and `substitute` says
plainly where a slate or a black frame stands in for something the project does not have. The two
words are `SectionKind` and `Substitute`, so a row is read into members and never compared as text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from ..jsonio import read_json, write_json
from ..pipeline import SectionKind, Substitute


@dataclass(frozen=True)
class Cut:
    """One section in the finished film: where it plays, what it was made from, and what it says."""

    section: int
    kind: SectionKind
    start: float
    end: float
    source: str  # The recording or the clip this section was cut from, project-relative.
    chapter: str
    substitute: Substitute | None = None  # What played because the real thing was missing.
    dip_in: bool = False
    dip_out: bool = False

    @property
    def key(self) -> str:
        return f"{self.section:02d}"

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            section=int(d["section"]),
            kind=SectionKind(d["kind"]),
            start=float(d["start"]),
            end=float(d["end"]),
            source=str(d.get("source", "")),
            chapter=str(d.get("chapter", "")),
            substitute=None if d.get("substitute") is None else Substitute(d["substitute"]),
            dip_in=bool(d.get("dip_in", False)),
            dip_out=bool(d.get("dip_out", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "kind": self.kind.value,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "source": self.source,
            "substitute": None if self.substitute is None else self.substitute.value,
            "chapter": self.chapter,
            "dip_in": self.dip_in,
            "dip_out": self.dip_out,
        }


@dataclass
class Cuts:
    """The cut list of one finished film."""

    fps: int
    total_seconds: float
    sections: list[Cut] = field(default_factory=list)

    @property
    def substituted(self) -> list[Cut]:
        """Every section that played a slate or black, which is what `--strict` refuses."""
        return [cut for cut in self.sections if cut.substitute is not None]

    def at(self, seconds: float) -> Cut | None:
        """The section playing at a second of the final film, or None past its end."""
        return next((cut for cut in self.sections if cut.start <= seconds < cut.end), None)

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = read_json(path)
        return cls(
            fps=int(d["fps"]),
            total_seconds=float(d["total_seconds"]),
            sections=[Cut.from_dict(row) for row in d.get("sections", [])],
        )

    def save(self, path: Path) -> None:
        write_json(path, self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "fps": self.fps,
            "total_seconds": round(self.total_seconds, 3),
            "sections": [cut.to_dict() for cut in self.sections],
        }
