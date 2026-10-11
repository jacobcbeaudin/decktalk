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

from collections.abc import Iterable, Set
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import Field, TypeAdapter, ValidationError

from decktalk.artifacts.stored import Stored
from decktalk.errors import ToolError
from decktalk.findings import Finding, Model
from decktalk.media import ffmpeg
from decktalk.media.clipframes import landings_of
from decktalk.media.pagereport import Box, Landing, MotionLane, Recording, Size
from decktalk.page import SECOND_DIGITS
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


LANDINGS = TypeAdapter(tuple[MotionLane, ...])
"""A landings file read as a page's lanes are, whoever wrote it: a page, a render command, a clip's maker or a build."""


def later(lane: MotionLane, seconds: float) -> MotionLane:
    """One lane moved `seconds` later on its clock, its landings with it."""

    def at(second: float | None) -> float | None:
        return None if second is None else round(second + seconds, SECOND_DIGITS)

    return lane.model_copy(
        update={
            "departs": at(lane.departs),
            "arrives": at(lane.arrives),
            "moved": at(lane.moved),
            "landings": tuple(
                Landing(cue=one.cue, kind=one.kind, at=round(one.at + seconds, SECOND_DIGITS)) for one in lane.landings
            ),
        }
    )


def inside(lane: MotionLane, pane: Box, frame: tuple[int, int]) -> MotionLane:
    """One lane of a clip drawn in a pane, its boxes and size moved from the clip's own pixels into the pane's."""
    sx, sy = pane.w / frame[0], pane.h / frame[1]

    def placed(box: Box | None) -> Box | None:
        return None if box is None else Box(
            x=round(pane.x + box.x * sx), y=round(pane.y + box.y * sy), w=round(box.w * sx), h=round(box.h * sy),
        )  # fmt: skip

    size = None if lane.size is None else Size(w=round(lane.size.w * sx), h=round(lane.size.h * sy))
    return lane.model_copy(update={"box": placed(lane.box), "rest": placed(lane.rest), "size": size})


@dataclass(frozen=True)
class PaneLandings:
    """The landings inside every pane clip one recording played, and why any clip's landings could not be placed."""

    lanes: tuple[MotionLane, ...]
    unplaced: tuple[str, ...]


def pane_landings(recording: Recording, root: Path) -> PaneLandings:
    """Every landing a pane clip carries beside it, moved onto the section's clock and into its pane.

    A clip's own landings are on its own clock and in its own pixels, as the build that made it or
    the command that made it wrote them. The clip starts on its cue and fills its pane, so a landing
    moves by the cue's second and its box into the pane where it stood from the cue. Every landing of
    the clip falls between its cue and its end, so the pane need hold still only while the clip plays
    and may move with the camera once it has played. A pane that moved before its clip ended has no
    one box to move them into, and a landings file that does not read as lanes has nothing to move, so
    each is said rather than measured.
    """
    lanes: list[MotionLane] = []
    unplaced: list[str] = []
    for lane in recording.pane_clips():
        clip = root / (lane.clip or "")
        written = landings_of(clip)
        if not written.is_file():
            continue
        try:
            own = LANDINGS.validate_json(written.read_bytes())
        except ValidationError as unread:
            unplaced.append(
                f"{lane.clip}: its landings file is not in the shape of lanes ({unread.error_count()} problems)"
            )
            continue
        try:
            frame, seconds = ffmpeg.probe_size(clip), ffmpeg.probe_duration(clip)
        except ToolError as cannot:
            unplaced.append(f"{lane.clip}: {cannot}")
            continue
        start = lane.landings[0].at
        end = round(start + seconds, SECOND_DIGITS)
        if lane.rest is None:
            unplaced.append(f"{lane.clip}: its pane was never seen while its clip played")
            continue
        if lane.moved is not None and lane.moved < end:
            unplaced.append(f"{lane.clip}: its pane moved at {lane.moved:g} s, before its clip ended at {end:g} s")
            continue
        lanes += [
            inside(later(row, start), lane.rest, frame).model_copy(
                update={"id": f"{lane.id}/{row.id}", "clip": lane.clip}
            )
            for row in own
        ]
    return PaneLandings(tuple(lanes), tuple(unplaced))


def inner(lane: MotionLane) -> bool:
    """Whether a lane is one inside a pane clip, whose landings name the clip's own cues rather than this page's."""
    return lane.kind != "clip" and lane.clip is not None


@dataclass(frozen=True)
class FilmSection:
    """One recorded section of a film as its landings file reads it: where it starts and the cues verify skips.

    `skipped` is the cues `cues.json` marks `"verify": false` in the section, which verify measures
    nothing at.
    """

    start: float
    log: RecordingLog
    skipped: Set[str]


def film_lanes(sections: Iterable[FilmSection], root: Path) -> tuple[MotionLane, ...]:
    """Every landing a film's verify measures, on the film's own clock, as the lanes the film carries beside itself.

    That is every lane its sections reported, every landing inside their pane clips, and every reveal
    their pages drew, each on the box it was drawn in, so a pane that plays the film in another film
    has every one of them to measure there. A landing on a cue `cues.json` marks `"verify": false` is
    left out, as verify leaves it out.
    """
    lanes: list[MotionLane] = []
    for section in sections:
        report = section.log.recording.report
        drawn = (*report.motions, *pane_landings(section.log.recording, root).lanes, *report.reveals)
        # A lane inside a pane clip lands on the clip's own cues, which no `cues.json` of this film names.
        measured = [
            lane.model_copy(
                update={
                    "landings": tuple(one for one in lane.landings if inner(lane) or one.cue not in section.skipped)
                }
            )
            for lane in drawn
        ]
        lanes += [later(lane, section.start) for lane in measured if lane.landings and lane.box is not None]
    return tuple(lanes)


__all__ = [
    "LANDINGS",
    "FilmSection",
    "Luma",
    "PaneLandings",
    "RecordingChecks",
    "RecordingLog",
    "film_lanes",
    "inner",
    "inside",
    "later",
    "pane_landings",
]
