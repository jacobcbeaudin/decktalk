"""The shared vocabulary of judgement: verdicts, findings, skip reasons, and the stage result protocol.

Every row a command reports carries one of these, and a row that judges nothing carries `NOTE`,
so a reader dispatches on a code and never on the absence of one.

A certain verdict names something that is wrong for sure, such as a page that threw or a
recording that stopped early, so the read-only commands exit 1 on it. An uncertain verdict
ends in a question mark and names something that is probably wrong, such as a dark frame
that may be a dark slide, so those commands exit 1 on it only with `--strict`. A shortfall
against `min_seconds` in align, a loudness miss in assemble, and a missing component in
doctor are uncertain or certain in the same way, although some of them carry no verdict of
their own. The passing verdicts are never findings, and neither are the warnings the
runtime records in the page.

A verdict is matched by its code, which is the member name, and printed by its label, which
is the member value. Nothing parses a label.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class Verdict(StrEnum):
    """One judgement a command can print. `name` is the code, `value` is the label."""

    # Certain: something is wrong for sure.
    PAGE_ERROR = "PAGE ERROR"
    STALLED = "STALLED"
    TRUNCATED = "TRUNCATED"
    NO_COVER = "NO COVER"
    BLACK = "BLACK"
    SPEECH_AT_CUT = "SPEECH AT CUT"
    POP_AT_CUT = "POP AT CUT"
    OFF_CUE = "OFF CUE"
    NO_CHANGE = "NO CHANGE"
    UNRESOLVED = "UNRESOLVED"
    UNKNOWN_CUE = "UNKNOWN CUE"
    UNCUED_ELEMENT = "UNCUED ELEMENT"
    MISSING = "MISSING"

    # Uncertain: something is probably wrong, and the label ends in a question mark.
    BLACK_UNSURE = "BLACK?"
    SPOKEN_SYMBOL = "SPOKEN SYMBOL?"
    KATEX_UNSURE = "KATEX?"
    THIN_CHANGE = "THIN CHANGE?"

    # Passing: the row was measured and nothing is wrong. These are never findings.
    CHANGED = "changed"
    QUIET = "quiet"
    OK = "ok"
    SKIPPED = "skipped"
    NOTE = "note"

    @property
    def certain(self) -> bool:
        """True when the verdict names something that is wrong for sure."""
        return self in _CERTAIN

    @property
    def passing(self) -> bool:
        """True when the verdict is not a finding at all."""
        return self in _PASSING

    def to_dict(self) -> dict[str, Any]:
        """The verdict as a reader receives it: its code, its label and whether it is certain."""
        return {"code": self.name, "label": self.value, "certain": self.certain}


_CERTAIN = frozenset(
    {
        Verdict.PAGE_ERROR,
        Verdict.STALLED,
        Verdict.TRUNCATED,
        Verdict.NO_COVER,
        Verdict.BLACK,
        Verdict.SPEECH_AT_CUT,
        Verdict.POP_AT_CUT,
        Verdict.OFF_CUE,
        Verdict.NO_CHANGE,
        Verdict.UNRESOLVED,
        Verdict.UNKNOWN_CUE,
        Verdict.UNCUED_ELEMENT,
        Verdict.MISSING,
    }
)
_PASSING = frozenset({Verdict.CHANGED, Verdict.QUIET, Verdict.OK, Verdict.SKIPPED, Verdict.NOTE})


class SkipReason(StrEnum):
    """Why a row measured nothing. `verify` and `preflight` name the shared ones identically."""

    # verify
    REFERENCE_CLAMPED = "REFERENCE_CLAMPED"
    SECTION_NOT_ASSEMBLED = "SECTION_NOT_ASSEMBLED"
    TOO_CLOSE_TO_END = "TOO_CLOSE_TO_END"
    NO_CLICK = "NO_CLICK"
    # preflight
    AT_SECTION_START = "AT_SECTION_START"
    NO_SLIDE = "NO_SLIDE"
    NOT_IN_SLIDE_CUES = "NOT_IN_SLIDE_CUES"
    NO_CATALOG = "NO_CATALOG"
    NO_CUES = "NO_CUES"
    CLIP = "CLIP"
    # both
    OPTED_OUT = "OPTED_OUT"


@dataclass(frozen=True)
class Finding:
    """One row a command reports, in the shape every reader receives.

    `detail` is the one human sentence, with any measured number written into it. `where` is the
    file, the page or the artifact the row is about, already relative to the project root, because
    this module sits below the one that knows how to make a path relative. A row that judges
    nothing carries `NOTE`, which is a passing verdict, so every row has a code a reader can
    dispatch on and an advisory note is still tallied with neither kind of finding.
    """

    detail: str
    verdict: Verdict = Verdict.NOTE
    section: int | None = None
    cue: str | None = None
    where: str | None = None

    @property
    def text(self) -> str:
        """The sentence with its cue, as a table prints it."""
        return f"{self.cue}: {self.detail}" if self.cue else self.detail

    def to_dict(self) -> dict[str, Any]:
        """The row as a reader receives it: the verdict opened out, then where and what."""
        return {
            **self.verdict.to_dict(),
            "section": self.section,
            "cue": self.cue,
            "where": self.where,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Findings:
    """How many certain and uncertain findings a command produced, which is not a list of rows.

    A command reports its rows as `Finding` objects and its totals as this pair of counts, and
    the two are separate on purpose, because `build` adds the counts of five stages without
    holding every row. Counts add.
    """

    certain: int = 0
    uncertain: int = 0

    def __add__(self, other: Findings) -> Findings:
        return Findings(self.certain + other.certain, self.uncertain + other.uncertain)

    def to_dict(self) -> dict[str, int]:
        return {"certain": self.certain, "uncertain": self.uncertain}

    @classmethod
    def of(cls, verdicts: Iterable[Verdict | None]) -> Findings:
        """The tally of a run's verdicts. A passing verdict and a bare note count as neither."""
        certain = uncertain = 0
        for verdict in verdicts:
            if verdict is None or verdict.passing:
                continue
            if verdict.certain:
                certain += 1
            else:
                uncertain += 1
        return cls(certain, uncertain)


@runtime_checkable
class StageResult(Protocol):
    """What every stage returns: what it judged, and the same thing as JSON-ready data.

    The CLI counts findings, chooses an exit code and prints one envelope without importing
    any stage or knowing any result's shape, and `build` tallies a whole run the same way.
    """

    @property
    def findings(self) -> Findings:
        """The tally of what this run judged."""
        ...

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The result as JSON-ready data, with every path relative to the project root."""
        ...
