"""Which two frozen states each cue is measured between, worked out with no browser and no file.

A frozen frame shows every reveal in its end state and nothing in motion, so the share of pixels
that change between the frame before a cue and the frame at it estimates what `verify` will measure
on the finished film once the reveal has settled. This module decides which two frames those are.

Ownership is declared. A slide owns exactly the cues the catalog lists against it, which are the
moments its own elements name plus whatever `data-owns` adds, so nothing here reads an id prefix or
counts a longest match. The first cued slide mounts at the section's own start and every other
slide mounts at its earliest cue, which is the order the runtime plays them in.

Every function takes the catalog the page published and the seconds the cues resolved to, and gives
back states to render, so what a check will compare can be read without rendering any of it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from decktalk.results import SkipReason
from decktalk.stages.storyboard import Freeze, Slides

ORDER_NOTE = "the slide's cues fire in another order when frozen, so these frames may differ from the recording."
"""Why a pair of frames may not be the pair the recorder would show, said once where it is noticed."""

EPSILON = 1e-9
"""Truth: the slack two seconds meant to be equal are compared with, which is finer than any clock."""

FIRST_FRAME = 1
"""Truth: a cue inside the first frame of a section has no frame before it to be measured against."""


@dataclass(frozen=True)
class FramePair:
    """The two frozen states whose difference estimates one cue's reveal, or why there are none."""

    cue: str
    seconds: float
    slide: str | None
    before: Freeze | None = None
    after: Freeze | None = None
    reason: SkipReason | None = None
    detail: str = ""
    note: str = ""

    @property
    def measured(self) -> bool:
        """Whether this pair has two states to compare, which is what makes a share worth reading."""
        return self.before is not None and self.after is not None


def owner_slide(cue: str, slides: Slides) -> str | None:
    """The slide that owns one cue, which is the one the catalog lists it against, or None.

    Ownership is declared rather than inferred, so a cue no slide lists belongs to no slide and is
    reported as such rather than handed to whichever slide its id happens to start with.
    """
    return next((slide for slide, wires in slides.items() if cue in wires), None)


def mounts(slides: Slides, times: Mapping[str, float]) -> list[tuple[str, float]]:
    """(slide, the second it mounts) in mount order, each slide arriving at its own earliest cue.

    The first slide of a section is already on screen when the section starts, so it mounts at zero
    however late its first cue is.
    """
    at: dict[str, float] = {}
    for cue, second in sorted(times.items(), key=lambda row: (row[1], row[0])):
        slide = owner_slide(cue, slides)
        if slide is not None:
            at[slide] = min(at.get(slide, math.inf), second)
    order = sorted(at.items(), key=lambda row: (row[1], row[0]))
    if order:
        order[0] = (order[0][0], min(order[0][1], 0.0))
    return order


def fired_by(slides: Slides, times: Mapping[str, float], slide: str, until: float, *, inclusive: bool) -> list[str]:
    """The cues of one slide that have fired by `until`, in the order the slide declares them."""
    return [
        cue
        for cue in slides[slide]
        if cue in times and (times[cue] <= until + EPSILON if inclusive else times[cue] < until - EPSILON)
    ]


def plan_frames(slides: Slides, times: Mapping[str, float], fps: int) -> list[FramePair]:
    """One pair per resolved cue of one section, in the order the cues fire."""
    order = mounts(slides, times)
    mounted = dict(order)
    pairs: list[FramePair] = []
    for cue, second in sorted(times.items(), key=lambda row: (row[1], row[0])):
        slide = owner_slide(cue, slides)
        if slide is None:
            detail = "no slide of the scene declares this cue, so there is no state to freeze it between"
            pairs.append(FramePair(cue, second, None, reason=SkipReason.NO_SLIDE, detail=detail))
            continue
        if second * fps < FIRST_FRAME:
            detail = f"the cue fires {second:.2f}s into the section, which is inside its first frame"
            pairs.append(FramePair(cue, second, slide, reason=SkipReason.AT_SECTION_START, detail=detail))
            continue
        pairs.append(_pair(slides, times, order, mounted, cue, second, slide))
    return pairs


def _pair(
    slides: Slides,
    times: Mapping[str, float],
    order: Sequence[tuple[str, float]],
    mounted: Mapping[str, float],
    cue: str,
    second: float,
    slide: str,
) -> FramePair:
    """The pair for one cue whose slide is known and whose second leaves a frame in front of it."""
    declared = slides[slide]
    at = declared.index(cue)
    earlier = [(name, when) for name, when in order if when < second - EPSILON and name != slide]
    if mounted[slide] < second - EPSILON or not earlier:
        before = Freeze(slide, cue=declared[at - 1]) if at else Freeze(slide, before=cue)
    else:
        # This cue mounts its own slide, so the frame in front of it shows the slide that was on
        # screen until then rather than an empty stage nobody would have seen.
        before = _previous_state(slides, times, earlier[-1][0], second)
    by_time = set(fired_by(slides, times, slide, second, inclusive=True))
    frozen = {one for one in declared[: at + 1] if one in times}
    return FramePair(cue, second, slide, before, Freeze(slide, cue=cue), note="" if by_time == frozen else ORDER_NOTE)


def _previous_state(slides: Slides, times: Mapping[str, float], slide: str, second: float) -> Freeze:
    """The state the slide before this one was left in, which is what a viewer was looking at."""
    fired = fired_by(slides, times, slide, second, inclusive=False)
    if fired:
        return Freeze(slide, cue=fired[-1])
    return Freeze(slide, before=slides[slide][0]) if slides[slide] else Freeze(slide)


def last_state(slides: Slides, times: Mapping[str, float]) -> Freeze | None:
    """The state a section ends on, which is its last mounted slide with every cue of it fired."""
    order = mounts(slides, times)
    if not order:
        return None
    slide = order[-1][0]
    fired = fired_by(slides, times, slide, math.inf, inclusive=True)
    return Freeze(slide, cue=fired[-1]) if fired else Freeze(slide)


def first_state(slides: Slides, times: Mapping[str, float], fps: int) -> Freeze | None:
    """The state a section opens on, which is its first slide with the cues of its first frame fired."""
    order = mounts(slides, times)
    if not order:
        return None
    slide = order[0][0]
    fired = fired_by(slides, times, slide, FIRST_FRAME / fps, inclusive=False)
    if fired:
        return Freeze(slide, cue=fired[-1])
    return Freeze(slide, before=slides[slide][0]) if slides[slide] else Freeze(slide)


__all__ = [
    "EPSILON",
    "FIRST_FRAME",
    "ORDER_NOTE",
    "FramePair",
    "fired_by",
    "first_state",
    "last_state",
    "mounts",
    "owner_slide",
    "plan_frames",
]
