"""The static scan: what a slide's measured catalog says, before anybody looks at a picture.

The probe lays every slide out once and reports one row per element it can reveal, with the moments
that element names, the words it draws and the box it occupies in stage pixels. That is enough to
judge four things a model reading a frame could not state exactly: a motion long enough that the cue
it carries can no longer be measured, a staggered container whose last child lands past that same
ceiling, an element that changes the picture and describes nothing, and a swap whose two halves land
far enough apart to be seen. The fifth judgement here is about the page's own requests rather than
its layout: an asset fetched from another origin is a file the film does not own.

Everything in this module is arithmetic over the published contract and the measured rows, so
`check` and `record` reach the same verdicts from the catalog each of them already has, and neither
imports the other to do it. It ranks above `page`, which is vocabulary and arithmetic alone, and
below the stages that hand it a catalog.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, Field

from decktalk import page
from decktalk.findings import MODEL, Code, Finding, Location
from decktalk.page import Attr, measurable, stagger_span

MILLISECOND_DIGITS = 3
"""Truth: three decimal places of a second is one millisecond, which is finer than any frame.

Every span this module reports is rounded there, because a reader comparing two of them against a
limit written in seconds should not have to read a number the measurement never had.
"""

SWAP_APART_SECONDS = page.MEASURABLE_SPAN_SECONDS
"""How far a swap's two halves may land apart before a viewer reads them as two separate changes.

Derived: it is the measurable ceiling itself, because a change still playing when the next cue is
due is the same failure whether the page calls it a swap or calls it a motion.
"""


def judged(code: Code, message: str, location: Location) -> Finding:
    """One judgement, built through validation so the code fills its own certainty and its own page.

    A raiser names the code, the sentence and the place. Writing the certainty out beside the code
    would be the second spelling of one fact, which is what the code owning it exists to prevent.
    """
    return Finding.model_validate({"code": code, "message": message, "location": location})


class Measured(BaseModel):
    """One element the probe measured, as the contract's own attribute names spell it."""

    model_config = MODEL

    attrs: dict[str, str] = Field(description="Every contract attribute this element carries, by name.")
    moments: dict[str, str] = Field(description="The wire id of each moment this element names, by attribute.")
    text: str = Field("", description="The words this element draws, which a synced line is judged on.")
    box: tuple[int, int, int, int] | None = Field(None, description="Its box in stage pixels, or null when unlaid.")

    @property
    def cue(self) -> str | None:
        """The wire id of this element's entrance, which is the moment every span is measured from."""
        return self.moments.get(Attr.IN.value)

    @property
    def described(self) -> bool:
        """True when the element says in words what it changes, which the transcript carries."""
        return bool(self.attrs.get(Attr.DESCRIBE.value) or self.attrs.get(Attr.DESCRIBE_CLASS.value))

    @property
    def entrance(self) -> float:
        """How long this element's entrance plays, from the seconds it declares or the style it names."""
        declared = self.attrs.get(Attr.IN_SECONDS.value)
        if declared:
            return float(declared)
        style = self.attrs.get(Attr.IN_STYLE.value) or page.ATTRS[Attr.IN_STYLE].default or ""
        effect = page.ENTRANCES.get(style)
        return effect.seconds if effect else 0.0

    def span(self, scale: float) -> float:
        """The seconds of motion this element puts between its own cue and the next one.

        The scale is applied without the clamp the runtime puts on it, because this is the
        judgement that tells an author the scale they chose has made their own cues unmeasurable.
        A staggered container's span is its step times the children after the first plus one
        entrance, which is exact arithmetic rather than an estimate, so its judgement is certain.
        """
        entrance = self.entrance * scale
        step = self.attrs.get(Attr.STAGGER.value)
        if not step:
            return entrance
        children = int(self.attrs.get(Attr.STEPS.value) or 0)
        return stagger_span(float(step) * scale, children, entrance)


def motion_findings(rows: Iterable[Measured], *, where: str, section: int | None, scale: float) -> list[Finding]:
    """One judgement per element whose motion runs past the ceiling its own cue is measured under."""
    found: list[Finding] = []
    for row in rows:
        span = row.span(scale)
        if row.cue is None or measurable(span):
            continue
        staggered = bool(row.attrs.get(Attr.STAGGER.value))
        code = Code.PAGE_STAGGER_OVERRUN if staggered else Code.PAGE_MOTION_OVERRUN
        found.append(
            judged(
                code=code,
                message=(
                    f"the moment {row.cue} is still moving {span:.2f}s after it fires, which is past the "
                    f"{page.MEASURABLE_SPAN_SECONDS:.2f}s ceiling, so no frame can show where it landed."
                ),
                location=Location(where=where, section=section, cue=row.cue),
            )
        )
    return found


def description_findings(rows: Iterable[Measured], *, where: str, section: int | None) -> list[Finding]:
    """One judgement per element that changes the picture and says nothing about what it changed."""
    return [
        judged(
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
    rows: Iterable[Measured], times: Mapping[str, float], *, where: str, section: int | None
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
        apart = round(abs(mine - theirs), MILLISECOND_DIGITS)
        if apart <= SWAP_APART_SECONDS:
            continue
        found.append(
            judged(
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
    rows: Iterable[Measured], times: Mapping[str, float], *, where: str, section: int | None, scale: float
) -> list[Finding]:
    """One judgement per pair of cues closer together than the first one's own motion lasts."""
    spans = {row.cue: row.span(scale) for row in rows if row.cue is not None}
    ordered = sorted(times.items(), key=lambda item: (item[1], item[0]))
    found: list[Finding] = []
    for (first, at), (second, then) in zip(ordered, ordered[1:], strict=False):
        apart = round(then - at, MILLISECOND_DIGITS)
        playing = spans.get(first, 0.0)
        if apart >= playing or playing == 0.0:
            continue
        found.append(
            judged(
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
        judged(
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
    rows: Iterable[Measured],
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
    "MILLISECOND_DIGITS",
    "SWAP_APART_SECONDS",
    "Measured",
    "asset_findings",
    "description_findings",
    "judged",
    "motion_findings",
    "overlap_findings",
    "slide_findings",
    "swap_findings",
]
