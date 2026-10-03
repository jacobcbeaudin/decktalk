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
lists of sentences only a person can read. What the recorder knew is the media layer's `Recording`,
the page's own report inside it, kept whole rather than copied field by field, because the layer that
read it is the layer that decides what it looks like.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from decktalk.artifacts.stored import Stored, engine_digest, file_digest
from decktalk.findings import Finding, Model
from decktalk.media.pagereport import Recording

HASH_DIGITS = 16
"""How much of the sha256 keys a recording, which is far more than enough within one project."""


def input_digest(parts: Sequence[str], files: Mapping[str, Path]) -> str:
    """The digest of what a section is recorded from: these strings, the content of these files, and the engine.

    `files` maps each project-relative name to the file on disk, so a page that swaps one picture for
    another moves the digest although no line of markup changed. The names are sorted, so the order
    the page happened to ask for them in is not part of the key. The engine's version is part of it
    too, because a recording carries the recorder, the probe and the runtime contract that made it,
    and a newer engine keeping an older engine's recording would measure a film it did not make.
    """
    return engine_digest(*parts, *(f"{name}:{file_digest(files[name])}" for name in sorted(files)))[:HASH_DIGITS]


class Luma(Model):
    """How bright a recording is at a tenth, a half and nine tenths of its length."""

    at_tenth: float = Field(ge=0, description="The mean luma one tenth of the way through.")
    at_half: float = Field(ge=0, description="The mean luma half way through.")
    at_nine_tenths: float = Field(ge=0, description="The mean luma nine tenths of the way through.")
    peak_at_half: float = Field(ge=0, description="The brightest pixel half way through, which a dark slide has.")


class RecordingChecks(Model):
    """What the frames of one recording measured, against what the recorder asked for."""

    duration_seconds: float = Field(ge=0, description="How long the recording runs.")
    wanted_seconds: float = Field(ge=0, description="How long the recorder asked for.")
    luma: Luma = Field(description="How bright the recording is at three points along it.")


class Start(Model):
    """Where narration t=0 sits in one recording, how it was found, and whether it was measured."""

    seconds: float = Field(
        ge=0, description="Narration t=0 in the webm, which is the first clean frame after the cover."
    )
    method: str = Field(
        description="How t=0 was found, in one sentence for a reader, which nothing matches a code against."
    )
    guessed: bool = Field(False, description="True when no cover was found, so every reveal in the section moves.")


class RecordingLog(Stored):
    """What `record` did for one section, where narration t=0 sits in the webm, and how it checked out."""

    label: ClassVar[str] = "the log of one section's recording"

    section: int = Field(ge=1, description="The section this recording plays.")
    digest: str = Field(description="The digest of what this section was recorded from, which keys a skip.")
    recording: Recording = Field(description="What the recorder knew: the page, its files, its timings and its report.")
    start: Start | None = Field(None, description="Where narration t=0 sits in the webm, or null before it was found.")
    findings: tuple[Finding, ...] = Field((), description="Every judgement the page and the frames made.")
    checks: RecordingChecks | None = Field(None, description="What the frames measured, or null when none were.")

    @property
    def trim_seconds(self) -> float:
        """Where the assembler cuts the head off this recording, which is narration t=0 in the webm."""
        return self.start.seconds if self.start is not None else self.recording.clock_start_seconds


__all__ = [
    "Luma",
    "RecordingChecks",
    "RecordingLog",
    "Start",
    "input_digest",
]
