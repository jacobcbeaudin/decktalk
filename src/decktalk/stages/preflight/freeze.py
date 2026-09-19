"""Which frozen states of a page each cue is measured between, with no browser and no file.

The frozen frames follow the runtime's cue mode. The first cued slide mounts at t=0, and every other
slide mounts at its earliest cue. The frame before a cue is its slide with the cues before it fired,
or the slide that was on screen before it when the cue mounts its own slide. A freeze fires a
slide's cues in preview order, so a slide whose cue times run in another order gets a note.

Every function here takes the catalog the page published and the cue times `align` resolved, and
gives back the states to render, so what `preflight` will compare can be read without rendering it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from ...verdicts import SkipReason

ORDER_NOTE = "the slide's cues fire in another order when frozen, so these frames may differ from the recording"

Slides = dict[str, list[str]]  # Each slide id of a scene, in page order, with its cue ids in preview order.


@dataclass(frozen=True)
class Freeze:
    """One frozen state of a page: a slide, with its cues fired up to `cue`, or before `before`, or all of them."""

    slide: str
    cue: str | None = None
    before: str | None = None

    def query(self) -> dict[str, str]:
        if self.cue is not None:
            return {"slide": self.slide, "after": self.cue}
        if self.before is not None:
            return {"slide": self.slide, "before": self.before}
        return {"slide": self.slide}

    @property
    def label(self) -> str:
        """The query as one file-name-safe word, such as slide-2.1-after-2.1aloud."""
        text = "-".join(part for pair in self.query().items() for part in pair)
        return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


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


def slide_cues(catalog: list[dict[str, Any]] | None, scene: str) -> tuple[Slides | None, str]:
    """Each slide of a scene with its cue ids in preview order, or the reason the scene gives none.

    A catalog entry carries `slides` and `cues`, which the runtime page contract defines. A page
    that registers a scene without them tells `preflight` nothing it can freeze, so that scene
    reads as a scene with no catalog.
    """
    if not catalog:
        return None, "is missing or has no runtime catalog"
    entry = next((c for c in catalog if str(c.get("scene")) == scene), None)
    if entry is None:
        return None, f"registers no scene {scene}"
    slides, cues = entry.get("slides"), entry.get("cues")
    if not isinstance(slides, list) or not isinstance(cues, dict):
        return None, f"registers scene {scene} without a slides list and a cues map"
    return {str(sid): [str(c) for c in cues.get(str(sid), [])] for sid in slides}, ""


def owner_slide(cue: str, slides: Slides) -> str | None:
    """The slide that owns a cue, by the runtime's rules: the same id or a listed cue first, else the longest prefix."""
    best: str | None = None
    for sid, cues in slides.items():
        if sid == cue or cue in cues:
            return sid
        if cue.startswith(sid) and (best is None or len(sid) > len(best)):
            best = sid
    return best


def mounts(slides: Slides, cue_times: dict[str, float]) -> list[tuple[str, float]]:
    """(slide, mount time) in mount order: each slide at its earliest cue, and the first at 0 at the latest."""
    at: dict[str, float] = {}
    for cue, t in sorted(cue_times.items(), key=lambda item: item[1]):
        sid = owner_slide(cue, slides)
        if sid is not None:
            at[sid] = min(at.get(sid, math.inf), t)
    seq = sorted(at.items(), key=lambda item: item[1])
    if seq:
        seq[0] = (seq[0][0], min(seq[0][1], 0.0))
    return seq


def fired_by(slides: Slides, cue_times: dict[str, float], sid: str, until: float, *, inclusive: bool) -> list[str]:
    """The slide's listed cues that have fired by `until`, in preview order."""
    return [
        c
        for c in slides[sid]
        if c in cue_times
        and owner_slide(c, slides) == sid
        and (cue_times[c] <= until + 1e-9 if inclusive else cue_times[c] < until - 1e-9)
    ]


def plan_frames(slides: Slides, cue_times: dict[str, float], fps: int) -> list[FramePair]:
    """One FramePair per cue of a section, in cue time order."""
    seq = mounts(slides, cue_times)
    mount = dict(seq)
    pairs: list[FramePair] = []
    for cue, t in sorted(cue_times.items(), key=lambda item: item[1]):
        sid = owner_slide(cue, slides)
        if sid is None:
            detail = "no slide of the scene owns the cue"
            pairs.append(FramePair(cue, t, None, reason=SkipReason.NO_SLIDE, detail=detail))
            continue
        order = slides[sid]
        if cue not in order:
            # The id prefix gives the slide the cue, and the slide declares no element that waits for
            # it, so there is no state to freeze between. `align` reports the cue itself.
            detail = f"slide {sid} declares no element for the cue"
            pairs.append(FramePair(cue, t, sid, reason=SkipReason.NO_SLIDE, detail=detail))
            continue
        if t * fps < 1:
            detail = "the cue fires within the first frame, so no frame comes before it"
            pairs.append(FramePair(cue, t, sid, reason=SkipReason.AT_SECTION_START, detail=detail))
            continue
        k = order.index(cue)
        earlier = [(s, m) for s, m in seq if m < t - 1e-9 and s != sid]
        if mount[sid] < t - 1e-9 or not earlier:
            before = Freeze(sid, cue=order[k - 1]) if k > 0 else Freeze(sid, before=cue)
        else:
            # The cue mounts its slide, so the frame before it shows the slide that was on screen.
            prev = earlier[-1][0]
            fired = fired_by(slides, cue_times, prev, t, inclusive=False)
            if fired:
                before = Freeze(prev, cue=fired[-1])
            elif slides[prev]:
                before = Freeze(prev, before=slides[prev][0])
            else:
                before = Freeze(prev)
        by_time = set(fired_by(slides, cue_times, sid, t, inclusive=True))
        frozen = {c for c in order[: k + 1] if c in cue_times and owner_slide(c, slides) == sid}
        note = "" if by_time == frozen else ORDER_NOTE
        pairs.append(FramePair(cue, t, sid, before, Freeze(sid, cue=cue), note=note))
    return pairs


def last_state(slides: Slides, cue_times: dict[str, float]) -> Freeze | None:
    """The frozen state a section ends on: its last mounted slide with every listed cue fired."""
    seq = mounts(slides, cue_times)
    if not seq:
        return None
    sid = seq[-1][0]
    fired = fired_by(slides, cue_times, sid, math.inf, inclusive=True)
    return Freeze(sid, cue=fired[-1]) if fired else Freeze(sid)


def first_state(slides: Slides, cue_times: dict[str, float], fps: int) -> Freeze | None:
    """The frozen state a section opens on: its first slide with the cues of its first frame fired."""
    seq = mounts(slides, cue_times)
    if not seq:
        return None
    sid = seq[0][0]
    fired = fired_by(slides, cue_times, sid, 1.0 / fps, inclusive=False)
    if fired:
        return Freeze(sid, cue=fired[-1])
    return Freeze(sid, before=slides[sid][0]) if slides[sid] else Freeze(sid)
