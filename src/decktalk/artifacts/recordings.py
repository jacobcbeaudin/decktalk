"""Everything `record` did for one section, and everything it judged about the result.

    build/recordings/NN.json   one log per recorded section, written when that section is finished

The log is the recorder's whole account of one section: what it opened, which project files the page
loaded, how long it asked for, every judgement the page and the frames produced, what each cue and
each synced line did, and the frames it measured. One
command writes all of it, so a measurement always belongs to the recording beside it and a reader of
a long run sees each section's log as soon as that section is done.

`made_from` keeps what the section was recorded from as named parts, each with the digest of what it
held, and a skip is keyed on all of them: every turn's text, voice and word times, the markup of the
one scene the section plays, the rest of its page, which every scene shares, the content of every
file the page loaded, its cues, the seconds asked for, the frame geometry, the colour scheme, the page
policy, the motion setting and the engine. A section whose parts are unchanged would be recorded
again for nothing, and one whose parts moved is named by the parts that moved.

Every judgement the recorder makes is a `Finding`, so the page's own warnings, the exceptions it
threw and the checks over the frames are one list a reader dispatches on by code, rather than three
lists of sentences only a person can read. What the recorder knew is the media layer's `Recording`,
the page's own report inside it, kept whole rather than copied field by field, because the layer that
read it is the layer that decides what it looks like.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field

from decktalk.artifacts.stored import Stored
from decktalk.findings import Finding, Model
from decktalk.media.pagereport import Recording
from decktalk.results import SectionNumber


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


class RecordingLog(Stored):
    """What `record` did for one section and how it checked out."""

    label: ClassVar[str] = "the log of one section's recording"

    section: SectionNumber
    made_from: dict[str, str] = Field(
        description="What this section was recorded from, part by part: each part's name and the digest of what it "
        "held, which keys a skip and names what moved."
    )
    recording: Recording = Field(description="What the recorder knew: the page, its files, its timings and its report.")
    findings: tuple[Finding, ...] = Field((), description="Every judgement the page and the frames made.")
    checks: RecordingChecks | None = Field(None, description="What the frames measured, or null when none were.")


__all__ = ["Luma", "RecordingChecks", "RecordingLog"]
