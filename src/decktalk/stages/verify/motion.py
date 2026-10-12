"""Verify on a moving picture: the masks over what the page said moved, and the landings of every motion.

A reveal is measured as a step in the changed share of the frame, and anything that keeps moving
beside it rises every frame and reads as the reveal starting early. The page declares everything that
moves, and the recorder samples each declared lane exactly, so a cue is measured with every lane alive
around it masked out of its changed, control and onset shares. That is the page's own report of what
moved, never a second hand-kept file, and the verify table says how much of the frame it covered.

A motion is measured on itself. Inside its lane, frame by frame, it moves while more than
`verify.motion_floor_percent` of its own area changes. Its departure is the first frame after its cue
that moved, and its arrival is the last frame that moved. An arrival that eases to rest stops before
its word, which is `MOTION_NO_EDGE`, and anything else outside the offset limit is `MOTION_OFF`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from decktalk.findings import Code, Finding, Location, judge
from decktalk.media.frames import Decoded, Mask, Rect, Size, Wanted
from decktalk.media.pagereport import MotionLane
from decktalk.pipeline import Stage
from decktalk.settings import Settings
from decktalk.settings.numbers import reference_lead_seconds
from decktalk.stages.verify.plan import EPSILON, HALF_FRAME

LandingKind = Literal["depart", "arrive"]
"""Whether a motion leaves at a cue or stops at it, which are the two landings a page names."""

DEPART: LandingKind = "depart"
"""Truth: the landing a motion makes at the cue it leaves on, which the page names."""

ARRIVE: LandingKind = "arrive"
"""Truth: the landing a motion makes at the cue it stops on, which the page names."""

TAIL_FRAMES = 2
"""Calibration: how many frames past the offset limit a landing is still looked for, so a late one is measured late."""


@dataclass(frozen=True)
class Landing:
    """One cue a motion lands on, on the film's clock."""

    cue: str
    kind: LandingKind
    at: float


@dataclass(frozen=True)
class Lane:
    """One declared motion, loop or region on the film's clock, in the pixels a comparison is made at."""

    kind: str
    box: Rect
    area: int
    departs: float
    arrives: float
    landings: tuple[Landing, ...] = ()


@dataclass(frozen=True)
class Landed:
    """Where one motion landing was measured: its cue, its kind, when it was due and when the picture showed it."""

    cue: str
    kind: LandingKind
    spoken: float
    shown: float | None

    @property
    def offset_seconds(self) -> float | None:
        """How far the picture landed from the cue, in seconds, or None when it never moved."""
        return None if self.shown is None else round(self.shown - self.spoken, 3)


def lanes_of(rows: Iterable[MotionLane], *, start: float, recorded: Size, probe: Size) -> list[Lane]:
    """Every lane one recording reported, moved onto the film's clock and scaled to the probe's size.

    `start` is where the section starts in the film and `recorded` is the size the section was recorded
    at. A lane the recorder never saw has no box and masks nothing.
    """
    sx, sy = probe.width / recorded.width, probe.height / recorded.height
    lanes: list[Lane] = []
    for row in rows:
        if row.box is None or row.departs is None or row.arrives is None:
            continue
        box = Rect(round(row.box.x * sx), round(row.box.y * sy), round(row.box.w * sx), round(row.box.h * sy))
        size = row.size or row.box
        area = max(1, round(size.w * sx) * round(size.h * sy))
        landings = tuple(Landing(one.cue, one.kind, round(start + one.at, 3)) for one in row.landings)
        lanes.append(Lane(row.kind, box, area, round(start + row.departs, 3), round(start + row.arrives, 3), landings))
    return lanes


def mask_over(lanes: Iterable[Lane], start: float, end: float, size: Size) -> Mask | None:
    """The mask of every lane alive anywhere from `start` to `end`, at the size the lanes are in, or None."""
    rects = tuple(one.box for one in lanes if one.departs <= end + EPSILON and one.arrives >= start - EPSILON)
    return Mask(rects, size) if rects else None


def window(lane: Lane, landing: Landing, settings: Settings, fps: int) -> tuple[float, float]:
    """The span of the film a landing is looked for in, from a frame before its search starts to the tail after it."""
    tail = settings.verify.cue_offset_max_ms / 1000 + TAIL_FRAMES / fps
    if landing.kind == DEPART:
        start = landing.at - reference_lead_seconds(settings)
        end = max(landing.at, lane.departs) + tail
    else:
        start = min(lane.departs, landing.at - reference_lead_seconds(settings))
        end = max(landing.at, lane.arrives) + tail
    return start - 1 / fps, end


def want(lanes: Iterable[Lane], wanted: Wanted, size: Size, settings: Settings, fps: int) -> None:
    """Add every frame a landing could be measured on to the film's one decode plan."""
    for one in lanes:
        for landing in one.landings:
            wanted.span(size, *window(one, landing, settings, fps))


def measure(
    decoded: Decoded, lane: Lane, *, level: int, floor: float, size: Size, settings: Settings
) -> tuple[Landed, ...]:
    """Where each of one lane's landings shows in the film: its first moving frame or its last."""
    fps = int(decoded.rate)
    rows: list[Landed] = []
    for landing in lane.landings:
        start, end = window(lane, landing, settings, fps)
        energy = decoded.energy(start, end, lane.box, area=lane.area, level=level, size=size)
        moving = [at for at, share in energy if share > floor]
        if landing.kind == DEPART:
            after = [at for at in moving if at >= landing.at - reference_lead_seconds(settings) - EPSILON]
            shown = after[0] if after else None
        else:
            shown = moving[-1] if moving else None
        rows.append(Landed(landing.cue, landing.kind, landing.at, shown))
    return tuple(rows)


def finding(
    row: Landed, *, settings: Settings, fps: int, film: Path | str, section: int | None = None
) -> Finding | None:
    """The judgement one measured landing carries, or None when it landed inside the offset limit."""
    limit_ms = settings.verify.cue_offset_max_ms
    limit = limit_ms / 1000 + HALF_FRAME / fps
    where = Location(where=row.cue, file=Path(film), section=section, cue=row.cue)
    offset = row.offset_seconds
    if offset is None:
        code = Code.MOTION_NO_EDGE if row.kind == ARRIVE else Code.MOTION_OFF
        said = f"the motion at {row.cue} never moved near its {row.kind}, so no frame shows it {row.kind} on its word."
        return judge(code, said, where, stage=Stage.VERIFY)
    if abs(offset) <= limit:
        return None
    if row.kind == ARRIVE and offset < 0:
        return judge(
            Code.MOTION_NO_EDGE,
            f"the motion arriving at {row.cue} came to rest {offset * 1000:+.0f} ms from its word, outside the "
            f"{limit_ms:.0f} ms limit, so it eases to rest rather than landing. Use the firm ease.",
            where,
            stage=Stage.VERIFY,
        )
    return judge(
        Code.MOTION_OFF,
        f"the motion at {row.cue} made its {row.kind} {offset * 1000:+.0f} ms from its word, which is outside the "
        f"{limit_ms:.0f} ms the offset limit allows.",
        where,
        stage=Stage.VERIFY,
    )


def mask_finding(
    mask: Mask | None, *, size: Size, settings: Settings, cue: str, section: int, film: Path | str
) -> Finding | None:
    """The warning that a cue was measured on less of the picture than usual, or None when the mask is small."""
    if mask is None:
        return None
    share = mask.share(size)
    ceiling = settings.verify.motion_mask_max_percent
    if share <= ceiling:
        return None
    return judge(
        Code.MOTION_MASK_LARGE,
        f"declared motion covered {share:.1f} percent of the frame around {cue}, over the {ceiling:.0f} percent "
        "verify masks before it says so, so the cue was judged on what was left.",
        Location(where=cue, file=Path(film), section=section, cue=cue),
        stage=Stage.VERIFY,
    )


def landing_cues(lanes: Sequence[Lane]) -> set[str]:
    """Every cue some lane lands on, which is a cue a motion is measured at."""
    return {landing.cue for one in lanes for landing in one.landings}


__all__ = [
    "ARRIVE",
    "DEPART",
    "TAIL_FRAMES",
    "Landed",
    "Landing",
    "Lane",
    "finding",
    "landing_cues",
    "lanes_of",
    "mask_finding",
    "mask_over",
    "measure",
    "want",
    "window",
]
