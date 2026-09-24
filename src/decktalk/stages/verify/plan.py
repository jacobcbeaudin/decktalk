"""The arithmetic behind a cue check, with no ffmpeg, no file and no project.

Where the reference frame sits, which probes after the cue are worth measuring, which of them a
neighbouring cue would spoil, where the control spans fall, and how a series of changed shares names
the frame a reveal began on. Every function here takes numbers and gives numbers, so the rules a
reveal is judged by can be read and tested without rendering anything.

The neighbour allowance is asymmetric. Backward it is the reference lead, which is the offset limit
plus the grid guard plus whatever extra lead the project asked for, because a reveal may land that
early and still pass. Forward it is the neighbouring effect's own declared span, taken from the page
contract, because an effect stops moving when its own animation ends and not a fixed distance later.
One symmetric constant was wrong in both directions at once: too short for a draw and too long for a
cut.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import cast

from decktalk.artifacts import CueTimes
from decktalk.inputs import Inputs
from decktalk.settings import NUMBERS_BY_ID, Settings, VerifyConfig

EPSILON = 1e-6
"""Truth: the slack two measured seconds need to compare equal, which is far under one frame."""

MILLISECONDS = 1000
"""Truth: milliseconds in one second, which is the one conversion between a limit and a measurement."""

PROBE_TAIL_SECONDS = 0.05
"""Calibration: how close to the end of a section a probe may still fall, so a probe never reads the next one.

A probe at the very last frame of a section reads the cut rather than the reveal, and a twentieth of
a second is over one frame at every rate DeckTalk encodes at.
"""

REFERENCE_LEAD = "verify.reference_lead_seconds"
"""The published number that says how far before its cue the reference frame is read."""

PROBE_WIDTH = "verify.probe_width"
PROBE_HEIGHT = "verify.probe_height"
BLOCK_WIDTH = "verify.block_width"
BLOCK_HEIGHT = "verify.block_height"
"""The published numbers that say what size a frame is compared at, which no key states."""


def reference_lead(settings: Settings) -> float:
    """How far before its cue the reference frame is read, which is a published derived number.

    The formula lives once, in the settings layer's own `NUMBERS` table, so a reader who asks what
    decides the lead meets the arithmetic rather than a second copy of it here.
    """
    return cast("float", NUMBERS_BY_ID[REFERENCE_LEAD].at(settings))


def frame_size(settings: Settings) -> dict[str, int]:
    """The width and the height every probe comparison is made at, as `frames` takes them."""
    return {
        "width": cast("int", NUMBERS_BY_ID[PROBE_WIDTH].at(settings)),
        "height": cast("int", NUMBERS_BY_ID[PROBE_HEIGHT].at(settings)),
    }


def block_size(settings: Settings) -> dict[str, int]:
    """The width and the height of the block-averaged copy, which cancels the encoder's ringing."""
    return {
        "width": cast("int", NUMBERS_BY_ID[BLOCK_WIDTH].at(settings)),
        "height": cast("int", NUMBERS_BY_ID[BLOCK_HEIGHT].at(settings)),
    }


@dataclass(frozen=True)
class Neighbour:
    """Another cue of the same section, with the seconds its own effect keeps moving after it fires.

    The span comes from the page contract, through the catalog the page published, so a draw that
    plays for most of half a second reaches further forward than a cut that plays for no time at all.
    """

    at: float
    span: float

    def reaches(self, start: float, end: float, backward: float) -> bool:
        """Whether this neighbour's own reveal can fall anywhere inside the span from `start` to `end`."""
        return self.at + self.span > start + EPSILON and self.at - backward < end - EPSILON


def apart(neighbours: Iterable[Neighbour], cue_at: float, backward: float) -> list[Neighbour]:
    """The neighbours far enough from this cue to be a separate reveal rather than part of this one.

    A cue closer than the backward allowance cannot be told apart from the one being measured, so
    treating it as a spoiler would leave every pair of cues in a tight passage unmeasurable.
    """
    return [row for row in neighbours if abs(row.at - cue_at) > backward + EPSILON]


def reference_time(
    sec_start: float, cue_t: float, fade_in: bool, dip: float, settings: Settings, fps: int
) -> float | None:
    """Where the reference frame for a cue sits in the final file, or None when no frame fits.

    The reference sits the whole reference lead before the cue, because a reveal that lands at the
    early edge of the offset limit would otherwise already show in it and the scan would measure the
    change only once the reveal settled. It may not sit inside the section's fade-in, where the
    picture is still coming up from black, and it must sit at least one frame before the cue.
    """
    floor = sec_start + (dip if fade_in else 0.0)
    latest = sec_start + cue_t - 1.0 / fps
    reference = max(floor, sec_start + cue_t - reference_lead(settings))
    if reference > latest + EPSILON:
        return None
    return round(reference, 4)


def control_spans(before: float, span: float, floor: float) -> list[tuple[float, float]]:
    """The measured control spans: two back-to-back spans of `span` that end at the reference, past the floor."""
    spans = []
    for step in (1, 2):
        end = before - (step - 1) * span
        start = end - span
        if start >= floor:
            spans.append((start, end))
    return spans


def probe_plan(
    cue_at: float,
    before: float,
    floor: float,
    sec_end: float,
    neighbours: Iterable[Neighbour],
    settings: VerifyConfig,
    fps: int,
    *,
    lead: float,
) -> tuple[list[float], bool]:
    """The probe delays for the cue at `cue_at`, and whether any was fitted between neighbouring cues.

    Every time is in the final file. A probe is spoiled when a neighbour's own reveal can fall inside
    its span, where it would count as this cue's change, or inside every measured control span, where
    it would count as motion that was always there. A probe of `probe_delays_seconds` that nothing
    spoils is kept exactly, so a cue with room around it is measured at the delay the settings name.
    A spoiled probe becomes the longest shorter delay that nothing spoils, down to one frame past the
    offset limit, so a reveal at the late edge of that limit still shows.
    """
    others = apart(neighbours, cue_at, lead)

    def holds(start: float, end: float) -> bool:
        return any(row.reaches(start, end, lead) for row in others)

    def spoiled(delay: float) -> bool:
        after = cue_at + delay
        spans = control_spans(before, after - before, floor)
        return holds(before, after) or (bool(spans) and all(holds(a, b) for a, b in spans))

    configured = [d for d in settings.probe_delays_seconds if cue_at + d <= sec_end - PROBE_TAIL_SECONDS]
    if not any(spoiled(d) for d in configured):
        return configured, False
    shortest = settings.cue_offset_max_ms / MILLISECONDS + 1.0 / fps
    delays: list[float] = []
    for delay in configured:
        fit = delay if not spoiled(delay) else _fitted(delay, cue_at, before, others, lead, fps, shortest, spoiled)
        if fit is not None and round(fit, 4) not in delays:
            delays.append(round(fit, 4))
    return (sorted(delays), True) if delays else (configured, False)


def _fitted(
    delay: float,
    cue_at: float,
    before: float,
    others: Sequence[Neighbour],
    lead: float,
    fps: int,
    shortest: float,
    spoiled: Callable[[float], bool],
) -> float | None:
    """The longest delay shorter than `delay` that no neighbour spoils, or None when none fits.

    The candidates are every frame between the shortest usable delay and the one asked for, plus the
    two edges a neighbour draws: where its own reveal can begin, and where the nearer control span
    clears it.
    """
    bounds = {delay - step / fps for step in range(1, int(delay * fps) + 1)}
    bounds |= {row.at - lead - cue_at for row in others}
    bounds |= {2 * before - row.at - lead - cue_at for row in others}
    # The caller owns the rule that says what a spoiled probe is, because it closes over the floor
    # and the reference frame, which are the section's and not the neighbour's.
    return next((x for x in sorted(bounds, reverse=True) if shortest - EPSILON <= x < delay and not spoiled(x)), None)


def thin_change(changed: float, margin: float, settings: VerifyConfig) -> bool:
    """Whether a cue that passed the change test passed by less than the factor times either floor.

    The small allowance keeps a share of exactly the factor times a floor from reading thin through
    the rounding of two floats that are meant to be equal.
    """
    factor = settings.thin_change_factor
    return (
        changed < factor * settings.changed_share_min_percent - EPSILON
        or margin < factor * settings.margin_min_points - EPSILON
    )


def opted_out(inputs: Inputs) -> set[tuple[int, str]]:
    """(section number, cue id) for every cue that `cues.json` marks `verify = false`.

    A reveal too small or too slow for a frame difference to see is the author's own call, so the
    row is skipped rather than measured and failed.
    """
    return {(block.number, cue.cue) for block in inputs.cues() for cue in block.cues if not cue.verify}


def default_checks(cue_times: CueTimes | None, only: Sequence[int] | None = None) -> list[tuple[int, str]]:
    """Every resolved cue as (section number, cue id), in section order and then cue time."""
    if cue_times is None:
        return []
    wanted = set(only or ())
    checks: list[tuple[int, str]] = []
    for block in sorted(cue_times.sections, key=lambda row: row.section):
        if wanted and block.section not in wanted:
            continue
        times = cue_times.times(block.section)
        checks += [(block.section, cue) for cue, _at in sorted(times.items(), key=lambda item: item[1])]
    return checks


def onset_offset_seconds(
    series: Sequence[tuple[float, float]],
    before: float,
    cue_at: float,
    onset: float,
    tolerance: float = 0.0,
    blocks: dict[float, float] | None = None,
) -> float | None:
    """Where the reveal begins, in seconds from the cue, read from the changed share per frame.

    A reveal is a step: between two consecutive frames the changed share jumps by at least `onset`.
    Motion that is always there, such as a curve still drawing, is a slope that grows a little every
    frame and never jumps. The first jump after the reference frame is the onset, and it may sit
    before the cue. When nothing jumps, the first frame whose share passes the pre-cue floor is used
    instead, which catches a reveal that grows slowly such as a number counting up.

    `blocks`, when given, is the changed share of the same frames scaled down to whole transform
    blocks. A frame whose blocks did not change is the encoder's own ringing and is never the onset.
    """

    def changed(at: float) -> bool:
        return blocks is None or blocks.get(at, 0.0) > 0.0

    previous: float | None = None
    for at, share in series:
        if previous is not None and at > before and share - previous >= onset and changed(at):
            return round(at - cue_at, 3)
        previous = share
    floor = max((share for at, share in series if at <= cue_at - tolerance), default=0.0)
    threshold = max(onset, floor)
    for at, share in series:
        if at > before and share > threshold and changed(at):
            return round(at - cue_at, 3)
    return None


__all__ = [
    "EPSILON",
    "MILLISECONDS",
    "PROBE_TAIL_SECONDS",
    "Neighbour",
    "apart",
    "block_size",
    "control_spans",
    "default_checks",
    "frame_size",
    "onset_offset_seconds",
    "opted_out",
    "probe_plan",
    "reference_lead",
    "reference_time",
    "thin_change",
]
