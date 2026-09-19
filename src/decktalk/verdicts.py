"""The shared vocabulary of judgement: verdicts, findings, skip reasons, and the stage result protocol.

Every row a command reports carries one of these, and a row that judges nothing carries `NOTE`,
so a reader dispatches on a code and never on the absence of one.

A certain verdict names something that is wrong for sure, such as a page that threw or a
recording that stopped early, so the read-only commands exit 1 on it. `MISSING`, `UNREADABLE` and
`INCONSISTENT` are kept apart, because a file that is not there is written, a file that will not
parse is repaired, and a file that parses and contradicts another is reconciled with it. A reader
that confused the first with either of the others would overwrite the author's work. An uncertain verdict
ends in a question mark and names something that is probably wrong, such as a dark frame
that may be a dark slide, so those commands exit 1 on it only with `--strict`. Every judgement a
command counts has a verdict here, so a reader dispatches on a code for all of them and never reads
one judgement out of a row and another out of a number. The passing verdicts are never findings,
and neither are the warnings the runtime records in the page.

A verdict is matched by its code, which is the member name, and printed by its label, which it
carries beside its certainty. Nothing parses a label. `Verdict` is a plain enum and not a string
one, so a raw string never compares equal to a verdict: code that holds a code, a label or a JSON
object where it meant a verdict fails where it is written rather than comparing false. JSON meets a
verdict as the one `{code, label, certain}` object, which `Verdict.to_dict` writes and
`Verdict.from_dict` reads back, refusing a code that names no verdict and an object that disagrees
with its own code. `SkipReason` is a plain enum for the same reason, and its value is its code.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

VERDICT_KEYS = ("code", "label", "certain")
"""The keys of the one object every verdict is written as."""
FINDING_KEYS = (*VERDICT_KEYS, "section", "cue", "where", "detail")
"""The keys of the one row every command reports, in the order a reader meets them."""


class Certainty(Enum):
    """How sure a verdict is. A certain and an uncertain verdict are findings, and a passing one is not."""

    CERTAIN = "certain"
    UNCERTAIN = "uncertain"
    PASSING = "passing"


class Verdict(Enum):
    """One judgement a command can print. `name` is the code, and `label` is what a table prints."""

    label: str
    certainty: Certainty

    def __init__(self, label: str, certainty: Certainty) -> None:
        self.label = label
        self.certainty = certainty

    # Certain: something is wrong for sure.
    PAGE_ERROR = "PAGE ERROR", Certainty.CERTAIN
    STALLED = "STALLED", Certainty.CERTAIN
    TRUNCATED = "TRUNCATED", Certainty.CERTAIN
    NO_COVER = "NO COVER", Certainty.CERTAIN
    BLACK = "BLACK", Certainty.CERTAIN
    SPEECH_AT_CUT = "SPEECH AT CUT", Certainty.CERTAIN
    POP_AT_CUT = "POP AT CUT", Certainty.CERTAIN
    OFF_CUE = "OFF CUE", Certainty.CERTAIN
    NO_CHANGE = "NO CHANGE", Certainty.CERTAIN
    UNRESOLVED = "UNRESOLVED", Certainty.CERTAIN
    UNKNOWN_CUE = "UNKNOWN CUE", Certainty.CERTAIN
    UNCUED_ELEMENT = "UNCUED ELEMENT", Certainty.CERTAIN
    OFF_STAGE = "OFF STAGE", Certainty.CERTAIN
    CDN_ASSET = "CDN ASSET", Certainty.CERTAIN
    MISSING = "MISSING", Certainty.CERTAIN
    KATEX_ERROR = "KATEX ERROR", Certainty.CERTAIN
    KATEX_NOT_LOADED = "KATEX NOT LOADED", Certainty.CERTAIN
    UNREADABLE = "UNREADABLE", Certainty.CERTAIN
    INCONSISTENT = "INCONSISTENT", Certainty.CERTAIN
    PLACEHOLDER = "PLACEHOLDER", Certainty.CERTAIN

    # Uncertain: something is probably wrong, and the label ends in a question mark.
    SLATE = "SLATE?", Certainty.UNCERTAIN
    BLACK_UNSURE = "BLACK?", Certainty.UNCERTAIN
    SPOKEN_SYMBOL = "SPOKEN SYMBOL?", Certainty.UNCERTAIN
    THIN_CHANGE = "THIN CHANGE?", Certainty.UNCERTAIN
    NO_CAPTION = "NO CAPTION?", Certainty.UNCERTAIN
    CUT_WORD = "CUT WORD?", Certainty.UNCERTAIN
    CUES_OVERLAP = "CUES OVERLAP?", Certainty.UNCERTAIN
    IN_CAPTION_BAND = "IN CAPTION BAND?", Certainty.UNCERTAIN
    SHORT_SECTION = "SHORT SECTION?", Certainty.UNCERTAIN
    LOUDNESS_MISS = "LOUDNESS MISS?", Certainty.UNCERTAIN

    # Passing: the row was measured and nothing is wrong. These are never findings.
    CHANGED = "changed", Certainty.PASSING
    QUIET = "quiet", Certainty.PASSING
    OK = "ok", Certainty.PASSING
    SKIPPED = "skipped", Certainty.PASSING
    NOTE = "note", Certainty.PASSING

    def __repr__(self) -> str:
        return f"{type(self).__name__}.{self.name}"

    @property
    def certain(self) -> bool:
        """True when the verdict names something that is wrong for sure."""
        return self.certainty is Certainty.CERTAIN

    @property
    def passing(self) -> bool:
        """True when the verdict is not a finding at all."""
        return self.certainty is Certainty.PASSING

    def to_dict(self) -> dict[str, Any]:
        """The verdict as a reader receives it: its code, its label and whether it is certain."""
        return {"code": self.name, "label": self.label, "certain": self.certain}

    @classmethod
    def from_dict(cls, data: Any) -> Verdict:
        """The verdict a `{code, label, certain}` object names.

        An object that is missing a key, carries another, names no verdict, or whose label or
        certainty is not its code's is refused, because each of those is a writer that is wrong.
        """
        if not isinstance(data, Mapping) or sorted(data) != sorted(VERDICT_KEYS):
            raise ValueError(f"a verdict is an object of exactly {', '.join(VERDICT_KEYS)}, not {data!r}")
        code = data["code"]
        verdict = cls.__members__.get(code) if isinstance(code, str) else None
        if verdict is None:
            raise ValueError(f"{code!r} is not a verdict code")
        if data["label"] != verdict.label or data["certain"] is not verdict.certain:
            raise ValueError(f"{dict(data)!r} is not what the verdict {code} writes, which is {verdict.to_dict()!r}")
        return verdict


class SkipReason(Enum):
    """Why a row measured nothing. `verify` and `preflight` name the shared ones alike, and the value is the code."""

    # verify
    REFERENCE_CLAMPED = "REFERENCE_CLAMPED"
    SECTION_NOT_ASSEMBLED = "SECTION_NOT_ASSEMBLED"
    TOO_CLOSE_TO_END = "TOO_CLOSE_TO_END"
    NO_CLICK = "NO_CLICK"
    # preflight
    AT_SECTION_START = "AT_SECTION_START"
    NO_SLIDE = "NO_SLIDE"
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

    @classmethod
    def from_dict(cls, data: Any) -> Finding:
        """The row a reader received, refusing one that is missing a key, carries another or says nothing.

        A judged row always carries its sentence, so a row whose `detail` is not a string is refused
        rather than read as an empty one.
        """
        if not isinstance(data, Mapping) or sorted(data) != sorted(FINDING_KEYS):
            raise ValueError(f"a finding row is an object of exactly {', '.join(FINDING_KEYS)}, not {data!r}")
        verdict = Verdict.from_dict({key: data[key] for key in VERDICT_KEYS})
        section, cue, where, detail = data["section"], data["cue"], data["where"], data["detail"]
        if not isinstance(detail, str):
            raise ValueError(f"the {verdict.name} row carries no sentence: {dict(data)!r}")
        if section is not None and (isinstance(section, bool) or not isinstance(section, int)):
            raise ValueError(f"the {verdict.name} row's section is {section!r}, not a section number")
        for key, value in (("cue", cue), ("where", where)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"the {verdict.name} row's {key} is {value!r}, not a string")
        return cls(detail=detail, verdict=verdict, section=section, cue=cue, where=where)


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
    `to_dict` gives data `json.dumps` writes as it is, with every verdict already the
    `{code, label, certain}` object, so the payload a caller parses is the one the result wrote.
    """

    @property
    def findings(self) -> Findings:
        """The tally of what this run judged."""
        ...

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The result as JSON-ready data, with every path relative to the project root."""
        ...
