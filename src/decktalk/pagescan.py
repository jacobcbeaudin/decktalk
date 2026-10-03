"""The static scan: what a slide's measured catalog says, before anybody looks at a picture.

The probe lays every slide out once and reports one row per element it can reveal, with the moments
that element names, the words it draws and the box it occupies in canvas pixels. That is enough to
judge four things a model reading a frame could not state exactly: a motion long enough that the cue
it carries can no longer be measured, a staggered container whose last child lands past that same
ceiling, an element that changes the picture and describes nothing, and a swap whose two halves land
far enough apart to be seen. The fifth judgement here is about the page's own requests rather than
its layout: an asset fetched from another origin is a file the film does not own.

Everything in this module is arithmetic over the published contract and the measured rows, so
`check` and `record` reach the same verdicts from the catalog each of them already has, and neither
imports the other to do it. It ranks above `page`, which is vocabulary and arithmetic alone, and
below the stages that hand it a catalog.

It is also the one reader of the catalog itself: which entry is a section's scene, which slides that
scene declares and which cues each slide owns, and what the page said about itself while it drew.
Every stage that opens a catalog reads it here, so no two of them can disagree about a scene.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path

from decktalk import page
from decktalk.findings import Code, Finding, Location, judge
from decktalk.media.pagereport import ElementRow, MeasuredScene, PageReport
from decktalk.page import SECOND_DIGITS, Attr, measurable
from decktalk.pipeline import Stage

SWAP_APART_SECONDS = page.MEASURABLE_SPAN_SECONDS
"""How far a swap's two halves may land apart before a viewer reads them as two separate changes.

Derived: it is the measurable ceiling itself, because a change still playing when the next cue is
due is the same failure whether the page calls it a swap or calls it a motion.
"""

Slides = dict[str, tuple[str, ...]]
"""Each slide of one scene, in page order, with the cue ids of the cues it declares in cue order."""


def scene_entry(entries: Sequence[MeasuredScene] | None, scene: str) -> MeasuredScene | None:
    """The catalog entry for one scene of one page, or None when the page published no such scene."""
    return next((one for one in entries or () if str(one.scene) == str(scene)), None)


def measured_rows(entry: MeasuredScene) -> list[ElementRow]:
    """Every element the probe measured on one scene, every slide's rows in page order."""
    return [row for slide in entry.elements.values() for row in slide]


def slide_cues(entry: MeasuredScene | None) -> Slides | None:
    """Each slide of one scene with the cues it declares, or None when the page published no such scene.

    Ownership is declared: a slide owns exactly the cues the catalog lists against it, which are the
    moments its own elements name plus whatever `data-owns` adds. Nothing here reads an id prefix,
    because a cue id is a slide and a local name and never an arithmetic about a number.
    """
    if entry is None:
        return None
    order = entry.slides or tuple(entry.elements)
    return {slide: tuple(dict.fromkeys(entry.cues.get(slide) or _moments(entry, slide))) for slide in order}


def scene_cues(entry: MeasuredScene) -> tuple[str, ...]:
    """Every cue id one scene declares, in the order the catalog names them and without repeats.

    A moment reaches the catalog twice, once as the attribute of the element that draws it and once
    in the scene's own cue map, and the two agree. Both are read because a scene whose cues are
    served by a handler alone declares them in the map and on no element.
    """
    named = [cue_id for ids in entry.cues.values() for cue_id in ids]
    found = [cue_id for row in measured_rows(entry) for cue_id in row.moments.values() if cue_id]
    return tuple(dict.fromkeys(found + named))


def _moments(entry: MeasuredScene, slide: str) -> list[str]:
    """The cue ids one slide's own elements name, for a scene that lists its cues nowhere else."""
    return [cue_id for row in entry.elements.get(slide, ()) for cue_id in row.moments.values() if cue_id]


def page_findings(
    report: PageReport, *, page: str, section: int | None = None, stage: Stage | None = None
) -> list[Finding]:
    """One judgement per thing the page could not honour, dispatched on the code the page carried.

    The media layer has already refused a code the page has no business raising, so every row here
    is a page code the contract publishes and the sentence is the page's own, written for a person.
    """
    return [
        judge(
            row.code,
            row.message,
            Location(where=row.slide or row.cue or page, file=Path(page), section=section, cue=row.cue),
            stage=stage,
        )
        for row in report.warnings
    ]


def motion_findings(rows: Iterable[ElementRow], *, where: str, section: int | None, scale: float) -> list[Finding]:
    """One judgement per element whose motion runs past the ceiling its own cue is measured under."""
    found: list[Finding] = []
    for row in rows:
        span = row.span(scale)
        if row.cue is None or measurable(span):
            continue
        staggered = bool(row.attrs.get(Attr.STAGGER.value))
        code = Code.PAGE_STAGGER_OVERRUN if staggered else Code.PAGE_MOTION_OVERRUN
        found.append(
            judge(
                code=code,
                message=(
                    f"the moment {row.cue} is still moving {span:.2f}s after it fires, which is past the "
                    f"{page.MEASURABLE_SPAN_SECONDS:.2f}s ceiling, so no frame can show where it landed."
                ),
                location=Location(where=where, section=section, cue=row.cue),
            )
        )
    return found


def description_findings(rows: Iterable[ElementRow], *, where: str, section: int | None) -> list[Finding]:
    """One judgement per element that changes the picture and says nothing about what it changed."""
    return [
        judge(
            code=Code.PAGE_NO_DESCRIPTION,
            message=(
                f"the moment {row.cue} changes the picture and carries no description, so the transcript "
                "and a viewer who cannot see it lose the change."
            ),
            location=Location(where=where, section=section, cue=row.cue),
        )
        for row in rows
        if row.cue is not None and not row.described and not row.text
    ]


def swap_findings(
    rows: Iterable[ElementRow], times: Mapping[str, float], *, where: str, section: int | None
) -> list[Finding]:
    """One judgement per swap whose two halves land far enough apart that a viewer sees the gap."""
    found: list[Finding] = []
    for row in rows:
        partner = row.attrs.get(Attr.SWAPS.value)
        if not partner or row.cue is None:
            continue
        mine, theirs = times.get(row.cue), times.get(partner)
        if mine is None or theirs is None:
            continue
        apart = round(abs(mine - theirs), SECOND_DIGITS)
        if apart <= SWAP_APART_SECONDS:
            continue
        found.append(
            judge(
                code=Code.PAGE_SWAP_APART,
                message=(
                    f"the swap at {row.cue} lands {apart:.2f}s from {partner}, which is over the "
                    f"{SWAP_APART_SECONDS:.2f}s a viewer reads as one change, so the two look separate."
                ),
                location=Location(where=where, section=section, cue=row.cue),
            )
        )
    return found


def overlap_findings(
    rows: Iterable[ElementRow], times: Mapping[str, float], *, where: str, section: int | None, scale: float
) -> list[Finding]:
    """One judgement per pair of cues closer together than the first one's own motion lasts."""
    spans = {row.cue: row.span(scale) for row in rows if row.cue is not None}
    ordered = sorted(times.items(), key=lambda item: (item[1], item[0]))
    found: list[Finding] = []
    for (first, at), (second, then) in pairwise(ordered):
        apart = round(then - at, SECOND_DIGITS)
        playing = spans.get(first, 0.0)
        if apart >= playing or playing == 0.0:
            continue
        found.append(
            judge(
                code=Code.CUE_OVERLAP,
                message=(
                    f"the moment {second} fires {apart:.2f}s after {first}, which is still playing for "
                    f"{playing:.2f}s, so the two run into each other."
                ),
                location=Location(where=where, section=section, cue=second),
            )
        )
    return found


def asset_findings(origins: Iterable[str], *, where: str, section: int | None = None) -> list[Finding]:
    """One judgement per other origin a page reached for, which the film does not own and cannot replay."""
    return [
        judge(
            code=Code.PAGE_CDN_ASSET,
            message=(
                f"the page loaded an asset from {origin}, which the project does not own, so the film "
                "depends on a host that may change or stop answering."
            ),
            location=Location(where=where, section=section),
        )
        for origin in sorted(set(origins))
    ]


def slide_findings(
    rows: Iterable[ElementRow],
    times: Mapping[str, float],
    *,
    where: str,
    section: int | None,
    scale: float,
) -> list[Finding]:
    """Every static judgement one slide's measured rows and resolved cue times support, in one call."""
    measured = list(rows)
    return [
        *motion_findings(measured, where=where, section=section, scale=scale),
        *description_findings(measured, where=where, section=section),
        *swap_findings(measured, times, where=where, section=section),
        *overlap_findings(measured, times, where=where, section=section, scale=scale),
    ]


__all__ = [
    "SWAP_APART_SECONDS",
    "Slides",
    "asset_findings",
    "description_findings",
    "measured_rows",
    "motion_findings",
    "overlap_findings",
    "page_findings",
    "scene_cues",
    "scene_entry",
    "slide_cues",
    "slide_findings",
    "swap_findings",
]
