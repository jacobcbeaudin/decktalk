"""Freezing the states a check compares, and reading what the difference between two of them means.

    build/frames/NN/<state>.png   the frozen states, kept so a reader can look at what was measured

A frozen frame shows every reveal in its end state, so the share of pixels that change between the
frame before a cue and the frame at it estimates what `verify` will measure on the finished film,
with a control of zero. The same two frames either side of a cut estimate whether a section that
carries the picture before it will show its join.

Every frame is drawn from the URL the recorder would open, with the section's own params and its own
words, so a frozen frame is a frame of the film being built rather than a picture of something near
it. The query is typed, so no key outside the contract can reach a page.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

from decktalk.findings import Code, Finding, Location
from decktalk.inputs import Inputs
from decktalk.inputs.document import PageSection
from decktalk.machine import Run
from decktalk.media import MILLISECONDS, frames
from decktalk.media.browser import screenshot
from decktalk.media.pagereport import MeasuredScene, PageReport
from decktalk.page import Attr
from decktalk.pagescan import asset_findings, slide_findings
from decktalk.results import Panel, SkipReason
from decktalk.settings import Settings
from decktalk.stages import SECOND_DIGITS, judge
from decktalk.stages.check.freeze import FramePair, first_state, last_state, plan_frames
from decktalk.stages.cue.catalog import measured_rows
from decktalk.stages.storyboard import Freeze, Slides, freeze_url
from decktalk.stages.verify import thin_change
from decktalk.stages.verify.plan import frame_size

DRAW_STYLE = "draw"
"""The entrance whose element is a stroke, which is the one reveal thin enough to be under the floor.

A stroke sweeps a thin area, so a `draw` that changes less of the picture than the floor is a stroke
too thin to see rather than a reveal that never happened, which is why the two carry their own codes.
The page cannot raise it, because the floor it is read against is a settings key no browser reads.
"""

FROZEN_CONTROL_PERCENT = 0.0
"""Truth: two frozen frames hold nothing in motion, so the control share between them is zero."""


@dataclass
class Sheet:
    """One browser page drawing frozen states, with what it drew and where it put each one.

    A state is drawn once however many cues are measured between it and another, because the same
    URL is the same picture, and every file it writes is reported through the run.
    """

    inputs: Inputs
    run: Run
    pages: Mapping[str, Page]
    settle_ms: int
    drawn: dict[str, Path] = field(default_factory=dict)
    panels: list[Panel] = field(default_factory=list)

    def frozen(self, section: PageSection, freeze: Freeze) -> Path:
        """The file holding one frozen state of one section, drawn now unless it is already there."""
        url = freeze_url(self.inputs, section, freeze)
        if url not in self.drawn:
            target = self.inputs.workspace.frames_dir / section.key / f"{freeze.label}.png"
            screenshot(self.pages[section.page], url, target, settle_ms=self.settle_ms)
            self.run.wrote(target)
            self.drawn[url] = target
        return self.drawn[url]

    def panel(self, section: PageSection, freeze: Freeze, cue: str | None, at: float) -> None:
        """Keep one drawn state as a panel of the storyboard this check writes."""
        image = self.drawn.get(freeze_url(self.inputs, section, freeze))
        if image is None:
            return
        self.panels.append(
            Panel(
                section=section.number,
                slide=freeze.slide,
                cue=cue,
                at=round(at, SECOND_DIGITS),
                image=self.inputs.relative(image),
            )
        )


def settle_milliseconds(inputs: Inputs) -> int:
    """How long a page is left to draw itself before its frame is taken, in the unit Chromium waits in."""
    return int(inputs.settings.record.screenshot_settle_seconds * MILLISECONDS)


def share_code(share: float, settings: Settings, *, drawn: bool) -> Code | None:
    """What one frozen share means, or None when the reveal is plainly there.

    The control between two frozen frames is zero, so the margin is the share itself and one number
    answers both floors.
    """
    cfg = settings.verify
    if share < cfg.changed_share_min_percent or share < cfg.margin_min_points:
        return Code.PAGE_THIN_DRAW if drawn else Code.CUE_NO_CHANGE
    return Code.CUE_THIN_CHANGE if thin_change(share, share, cfg) else None


def share_message(code: Code, cue: str, share: float, settings: Settings) -> str:
    """The sentence one frozen share earns, with the number measured and the floor it is read against."""
    cfg = settings.verify
    floor = max(cfg.changed_share_min_percent, cfg.margin_min_points)
    if code is Code.PAGE_THIN_DRAW:
        return (
            f"the stroke at {cue} sweeps {share:.2f} percent of the frame, which is under the "
            f"{cfg.changed_share_min_percent:.2f} percent a change has to cross to be seen."
        )
    if code is Code.CUE_NO_CHANGE:
        return (
            f"freezing the slide either side of {cue} changes {share:.2f} percent of the frame, which is under "
            f"the {floor:.2f} percent floor, so the reveal would not be measured at all."
        )
    return (
        f"freezing the slide either side of {cue} changes {share:.2f} percent of the frame, which passes the "
        f"{floor:.2f} percent floor by less than the {cfg.thin_change_factor:g} times a clean reveal clears it."
    )


def page_findings(report: PageReport, *, where: str, section: int | None = None) -> list[Finding]:
    """Everything the page said about itself while it was frozen, as the codes it named.

    The page carries its own code on every warning, so nothing here reads a sentence to work out
    what happened, which is the channel that used to be prose classified by substring.
    """
    return [
        judge(row.code, row.message, Location(where=row.slide or where, section=section, cue=row.cue))
        for row in report.warnings
    ]


def origin_findings(origins: Iterable[str], *, where: str, section: int | None = None) -> list[Finding]:
    """One judgement per other origin a page reached for, which the film does not own and cannot replay."""
    return asset_findings(origins, where=where, section=section)


def static_findings(
    entry: MeasuredScene, times: Mapping[str, float], *, where: str, section: int | None, settings: Settings
) -> list[Finding]:
    """Every judgement the measured catalog supports on its own, before any frame is compared."""
    return slide_findings(measured_rows(entry), times, where=where, section=section, scale=settings.motion.scale)


def drawn_cues(entry: MeasuredScene) -> set[str]:
    """Every cue whose element arrives as a stroke, which is the reveal a thin share is about."""
    return {
        row.cue
        for row in measured_rows(entry)
        if row.cue is not None and row.attrs.get(Attr.IN_STYLE.value) == DRAW_STYLE
    }


def element_cues(entry: MeasuredScene) -> set[str]:
    """Every cue an element of this scene declares, which is every cue a frozen frame can show.

    A still fires the cues up to the one it is frozen at and runs no handler, so a cue that only a
    handler serves, which is what `data-owns` declares, draws nothing in a frozen frame however well
    it plays in the film. Such a cue is unmeasurable from a still rather than a reveal that failed,
    and `verify` measures it on the finished film where the handler does run.
    """
    return {wire for row in measured_rows(entry) for wire in row.moments.values() if wire}


def landing_findings(
    sheet: Sheet,
    section: PageSection,
    entry: MeasuredScene,
    slides: Slides,
    times: Mapping[str, float],
    *,
    skipped: set[tuple[int, str]],
) -> list[Finding]:
    """One judgement per cue whose frozen frames say the reveal would not be measured as it stands."""
    settings = sheet.inputs.settings
    size = {"level": settings.verify.probe_diff_luma, **frame_size(settings)}
    strokes = drawn_cues(entry)
    declared = element_cues(entry)
    found: list[Finding] = []
    for pair in plan_frames(slides, times, settings.video.output_fps):
        if (section.number, pair.cue) in skipped:
            continue
        if pair.cue not in declared:
            _say_handler_only(sheet.run, section, pair)
            continue
        if not pair.measured:
            _say_why(sheet.run, section, pair)
            continue
        found += _judged(sheet, section, pair, size=size, strokes=strokes)
    return found


def _judged(
    sheet: Sheet, section: PageSection, pair: FramePair, *, size: dict[str, int], strokes: set[str]
) -> list[Finding]:
    """The judgement one measured pair earns, with both of its frames drawn and kept."""
    before, after = pair.before, pair.after
    if before is None or after is None:
        return []
    first, second = sheet.frozen(section, before), sheet.frozen(section, after)
    sheet.panel(section, after, pair.cue, pair.seconds)
    if pair.note:
        sheet.run.note(f"section {section.number}, cue {pair.cue}: {pair.note}")
    share = frames.changed_images_percent(first, second, **size)
    code = share_code(share, sheet.inputs.settings, drawn=pair.cue in strokes)
    if code is None:
        return []
    place = Location(where=pair.cue, file=sheet.inputs.relative(second), section=section.number, cue=pair.cue)
    return [judge(code, share_message(code, pair.cue, share, sheet.inputs.settings), place)]


def _say_handler_only(run: Run, section: PageSection, pair: FramePair) -> None:
    """Why a cue no element declares is passed over, which is that a still runs no handler."""
    run.note(
        f"section {section.number}, cue {pair.cue}: not measured ({SkipReason.NO_SLIDE.value}), because no element "
        "declares it and a frozen frame runs no handler, so `decktalk verify` measures it on the film instead."
    )


def _say_why(run: Run, section: PageSection, pair: FramePair) -> None:
    """Why one cue was not measured, which is a line rather than a judgement about the deck."""
    reason = pair.reason.value if pair.reason is not None else SkipReason.NO_CUES.value
    run.note(f"section {section.number}, cue {pair.cue}: not measured ({reason}), because {pair.detail}.")


def opening_panels(sheet: Sheet, section: PageSection, slides: Slides, times: Mapping[str, float]) -> None:
    """Keep the state each slide of one section opens on, so the storyboard shows the whole deck.

    A check freezes the frames either side of every cue, and the frame in front of the first cue of a
    slide is the picture that slide opens on, so the sheet is complete without drawing anything twice.
    """
    for slide, wires in slides.items():
        resolved = [times[wire] for wire in wires if wire in times]
        opening = Freeze(slide, before=wires[0]) if wires else Freeze(slide)
        sheet.panel(section, opening, None, min(resolved) if resolved else 0.0)


def seam_findings(
    sheet: Sheet,
    previous: PageSection,
    section: PageSection,
    slides: Mapping[int, Slides],
    times: Mapping[int, Mapping[str, float]],
) -> list[Finding]:
    """One judgement when the cut into a section that declares `seamless` would show.

    The two frames are the state the section before it ends on and the state this one opens on,
    which is exactly what a viewer sees across the cut.
    """
    settings = sheet.inputs.settings
    ending, opening = slides.get(previous.number), slides.get(section.number)
    if ending is None or opening is None:
        sheet.run.note(f"section {section.number} declares seamless and a side of its cut published no catalog.")
        return []
    last = last_state(ending, times.get(previous.number, {}))
    first = first_state(opening, times.get(section.number, {}), settings.video.output_fps)
    if last is None or first is None:
        sheet.run.note(f"section {section.number} declares seamless and a side of its cut has no resolved cue.")
        return []
    size = {"level": settings.verify.probe_diff_luma, **frame_size(settings)}
    share = frames.changed_images_percent(sheet.frozen(previous, last), sheet.frozen(section, first), **size)
    limit = settings.verify.cut_change_max_percent
    if share <= limit:
        return []
    return [
        judge(
            Code.CUT_POP,
            f"section {section.number} declares seamless and {share:.2f} percent of the picture would change "
            f"across its cut, which is over the {limit:.2f} percent a join may move, so the cut shows.",
            Location(where=section.key, section=section.number),
        )
    ]


def judged_pages(sections: Sequence[PageSection], extra: Sequence[str]) -> tuple[str, ...]:
    """Every page one check judged, in the order it met them and without repeats."""
    return tuple(dict.fromkeys([section.page for section in sections] + list(extra)))


__all__ = [
    "DRAW_STYLE",
    "FROZEN_CONTROL_PERCENT",
    "Sheet",
    "drawn_cues",
    "judged_pages",
    "landing_findings",
    "opening_panels",
    "origin_findings",
    "page_findings",
    "seam_findings",
    "settle_milliseconds",
    "share_code",
    "share_message",
    "static_findings",
]
