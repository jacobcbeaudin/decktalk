"""Everything `record` did for one section, and everything it judged about the result.

    build/recordings/NN.json   one log per recorded section, written when that section is finished

The log is the recorder's whole account of one section: what it opened, which project files the page
loaded, how long it asked for, every judgement the page and the frames produced, what each cue and
each synced line did, where narration t=0 landed in the webm, and the frames it measured. One
command writes all of it, so a measurement always belongs to the recording beside it and a reader of
a long run sees each section's log as soon as that section is done.

`RecordingInputs` is what the section was recorded from, and its digest is what a skip is keyed on:
the page URL with its cues and its words, the frame geometry, the markup of the one scene the
section plays, the rest of its page, which every scene shares, and the content of every file the
page loaded. A section whose digest is unchanged would be recorded again for nothing.

Every judgement the recorder makes is a `Finding`, so the page's own warnings, the exceptions it
threw and the checks over the frames are one list a reader dispatches on by code, rather than three
lists of sentences only a person can read. What the page said about itself is the media layer's
`PageReport`, kept whole rather than copied row by row, because the layer that read it is the layer
that decides what a row of it looks like.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, Field

from decktalk.artifacts.stored import Stored
from decktalk.findings import MODEL, Finding, ProjectPath
from decktalk.media.pagereport import PageReport

HASH_DIGITS = 16
"""How much of the sha256 keys a recording, which is far more than enough within one project."""

GONE = "gone"
"""What a file the page asked for and the project no longer has is digested as."""

MILLISECONDS = 1000
"""Truth: milliseconds in one second, which is the one conversion between a page's clock and a film's."""


def file_digest(path: Path) -> str:
    """The head of a file's sha256, or `gone` when the project no longer has it."""
    if not path.is_file():
        return GONE
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:HASH_DIGITS]


def text_digest(text: str) -> str:
    """The head of a string's sha256, which is how a slice of a page joins the key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_DIGITS]


def input_hash(parts: Sequence[str], files: Mapping[str, Path]) -> str:
    """The digest of what a section is recorded from: these strings, and the content of these files.

    `files` maps each project-relative name to the file on disk, so a page that swaps one picture for
    another moves the digest although no line of markup changed. The names are sorted, so the order
    the page happened to ask for them in is not part of the key.
    """
    lines = [*parts, *(f"{name}:{file_digest(files[name])}" for name in sorted(files))]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:HASH_DIGITS]


class Luma(BaseModel):
    """How bright a recording is at a tenth, a half and nine tenths of its length."""

    model_config = MODEL

    at_tenth: float = Field(ge=0, description="The mean luma one tenth of the way through.")
    at_half: float = Field(ge=0, description="The mean luma half way through.")
    at_nine_tenths: float = Field(ge=0, description="The mean luma nine tenths of the way through.")
    peak_at_half: float = Field(ge=0, description="The brightest pixel half way through, which a dark slide has.")


class RecordingChecks(BaseModel):
    """What the frames of one recording measured, against what the recorder asked for."""

    model_config = MODEL

    duration_seconds: float = Field(ge=0, description="How long the recording runs.")
    wanted_seconds: float = Field(ge=0, description="How long the recorder asked for.")
    luma: Luma = Field(description="How bright the recording is at three points along it.")


class RecordingLog(Stored):
    """What `record` did for one section, where narration t=0 sits in the webm, and how it checked out."""

    section: int = Field(ge=1, description="The section this recording plays.")
    url: str = Field(description="The page URL the recorder opened, with its cues and its words.")
    input_hash: str = Field(description="The digest of what this section was recorded from, which keys a skip.")
    requested_seconds: float = Field(ge=0, description="How long the recorder asked the page to play for.")
    settle_seconds: float = Field(ge=0, description="How long the page was left to settle before the clock started.")
    load_seconds: float = Field(ge=0, description="How long the page took to load.")
    clock_start_seconds: float = Field(ge=0, description="The recorder's own estimate of where narration t=0 is.")
    t0_seconds: float | None = Field(None, ge=0, description="The first clean frame after the cover, or null.")
    t0_method: str | None = Field(None, description="How t=0 was found, in one sentence, or null.")
    t0_guessed: bool = Field(False, description="True when no cover was found, so every reveal in the section moves.")
    assets: tuple[ProjectPath, ...] = Field((), description="Every project file the page loaded, project-relative.")
    external: tuple[str, ...] = Field((), description="Every other origin the page reached for while recording.")
    findings: tuple[Finding, ...] = Field((), description="Every judgement the page and the frames made.")
    checks: RecordingChecks | None = Field(None, description="What the frames measured, or null when none were.")
    report: PageReport = Field(description="What the page said about itself, read once as it was recorded.")

    @property
    def worst_stall_milliseconds(self) -> int:
        """The longest stall a viewer can see, counting only the part of each gap after narration t=0.

        Frames before t=0 sit under the cover and are trimmed from the cut, so a scene may warm up
        there. A gap is recorded when it ends, so a gap that began before t=0 counts only the
        milliseconds after t=0, and a gap that ended before t=0 counts nothing at all.
        """
        gaps = self.report.frame_gaps
        visible = (0.0 if gap.at is None else min(gap.ms, gap.at * MILLISECONDS) for gap in gaps)
        return int(max((seen for seen in visible if seen > 0), default=0))

    @property
    def trim_seconds(self) -> float:
        """Where the assembler cuts the head off this recording, which is narration t=0 in the webm."""
        return self.t0_seconds if self.t0_seconds is not None else self.clock_start_seconds


__all__ = [
    "GONE",
    "Luma",
    "RecordingChecks",
    "RecordingLog",
    "file_digest",
    "input_hash",
    "text_digest",
]
