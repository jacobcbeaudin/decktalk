"""The cut list: where every section sits in the finished film, and what each section cut was made from.

    build/final/cuts.json   one row per section, in the order they play
    build/sections/NN.json  the key of the section cut beside it, which decides whether it is kept

This is the one record of the shape of a film. The transcript page, a caption reader and anything
that wants to jump to a section read it instead of adding up section files, and `substitute` says
plainly where a slate or a black frame stands in for something the project does not have.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import Field

from decktalk.artifacts.stored import Stored, engine_digest, file_digest
from decktalk.findings import Model, ProjectPath
from decktalk.results import SectionKey, SectionKind, SectionNumber, Substitute


class Cut(Model):
    """One section in the finished film: where it plays, what it was made from, and what it says."""

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


class Cuts(Stored):
    """The cut list of one finished film."""

    fps: int = Field(gt=0, description="The rate the film was encoded at.")
    sections: tuple[Cut, ...] = Field((), description="Every section, in the order they play.")

    @property
    def total_seconds(self) -> float:
        """How long the whole film runs, which is where its last section ends."""
        return self.sections[-1].end if self.sections else 0.0


class CutKey(Stored):
    """What one section cut was encoded from, which is how an unchanged cut is told from a stale one.

    The key is the whole argument list of the encode and the content of every file it read, so a
    change to any filter, any encoder setting, the trim, the fades or the recording itself moves it.
    A key taken over a hand-picked subset of those would one day miss an input and ship a stale
    picture, which is why nothing here chooses which arguments count. The engine joins the key as
    well, because a newer engine may encode the same arguments differently.
    """

    digest: str = Field(description="The sha256 of the encode's arguments, its inputs' content digests and the engine.")

    @classmethod
    def of(cls, args: Sequence[str], sources: Sequence[Path]) -> CutKey:
        """The key of one encode, from the arguments it would run with and the files it would read."""
        return cls(digest=engine_digest(*args, *(f"{path.name}:{file_digest(path)}" for path in sources)))


__all__ = ["Cut", "CutKey", "Cuts"]
