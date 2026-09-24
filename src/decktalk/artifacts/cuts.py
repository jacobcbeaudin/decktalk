"""The cut list: where every section sits in the finished film.

    build/final/cuts.json   one row per section, in the order they play

This is the one record of the shape of a film. The transcript page, a caption reader and anything
that wants to jump to a section read it instead of adding up section files, and `substitute` says
plainly where a slate or a black frame stands in for something the project does not have.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from decktalk.artifacts.stored import Stored
from decktalk.findings import MODEL, ProjectPath
from decktalk.results import SectionKey, SectionKind, SectionNumber, Substitute


class Cut(BaseModel):
    """One section in the finished film: where it plays, what it was made from, and what it says."""

    model_config = MODEL

    section: SectionNumber
    key: SectionKey
    kind: SectionKind = Field(description="Whether this section played a recorded page or a supplied clip.")
    start: float = Field(ge=0, description="When this section starts in the film, in seconds.")
    end: float = Field(ge=0, description="When this section ends in the film, in seconds.")
    source: ProjectPath = Field(description="The recording or the clip this section was cut from.")
    chapter: str = Field(description="The section's title, which the film's chapter marker carries.")
    substitute: Substitute | None = Field(None, description="What played because the real thing was missing.")
    dip_in: bool = Field(False, description="True when the picture dips to black on the way into this section.")
    dip_out: bool = Field(False, description="True when the picture dips to black on the way out of it.")

    @property
    def seconds(self) -> float:
        """How long this section runs in the film."""
        return round(self.end - self.start, 3)


class Cuts(Stored):
    """The cut list of one finished film."""

    fps: int = Field(gt=0, description="The rate the film was encoded at.")
    sections: tuple[Cut, ...] = Field((), description="Every section, in the order they play.")

    @property
    def total_seconds(self) -> float:
        """How long the whole film runs, which is where its last section ends."""
        return self.sections[-1].end if self.sections else 0.0

    @property
    def substituted(self) -> tuple[Cut, ...]:
        """Every section that played a slate or black, which is what a strict run refuses."""
        return tuple(cut for cut in self.sections if cut.substitute is not None)

    def at(self, seconds: float) -> Cut | None:
        """The section playing at one second of the film, or None past its end."""
        return next((cut for cut in self.sections if cut.start <= seconds < cut.end), None)

    def of(self, section: int) -> Cut | None:
        """One section's row, or None when that section is not in the film."""
        return next((cut for cut in self.sections if cut.section == section), None)


__all__ = ["Cut", "Cuts"]
