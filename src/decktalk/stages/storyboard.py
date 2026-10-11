"""Every slide at every cue, frozen onto one page, which is the checkpoint before anything is bought.

    build/storyboard/NN/<state>.png   one still per frozen moment
    build/storyboard.html             the contact sheet a person reads

A storyboard judges nothing. It draws what the film will show, in the order it will show it, so a
person can see the whole deck before a single second of speech is bought. One panel of a storyboard
is still a storyboard, which is why the name survives a run that asks for one section.

This module chooses the panels. A frozen state, the frames the project keeps and the contact sheet
are the pictures module's, which `check` draws its frames through as well, so a state either command
drew is one the other reads back rather than draws again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from decktalk.events import Level
from decktalk.inputs import Inputs
from decktalk.inputs.document import PageSection
from decktalk.machine.run import Run
from decktalk.media.pagereport import MeasuredScene, PageReport
from decktalk.page import SECOND_DIGITS
from decktalk.pagescan import Slides, scene_entry, slide_cues
from decktalk.pictures import pictures
from decktalk.pictures.address import Freeze
from decktalk.pictures.sheet import Sheet, section_voices, write_page
from decktalk.results import Panel, StoryboardResult
from decktalk.stages import SECTION_START_SECONDS, selects


@dataclass(frozen=True)
class Selection:
    """Which panels of the whole sheet a run draws, which is every panel when it names none.

    Every selector narrows. `slides` chooses which slides contribute at all, `after` and `before`
    choose the moment of a cue, which are the two states an author compares to see what one reveal
    changed, and `at` chooses whatever is on screen at a second of the section's own clock. A
    selector that matches nothing draws nothing, which is what a section number that matches no
    section already does, so one storyboard of nothing never means two things.
    """

    slides: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()
    at: tuple[float, ...] = ()

    @classmethod
    def of(
        cls,
        slide: Sequence[str] | None,
        after: Sequence[str] | None,
        before: Sequence[str] | None,
        at: Sequence[float] | None,
    ) -> Selection:
        """One selection from the four selectors as a caller passes them, each of which repeats."""
        return cls(tuple(slide or ()), tuple(after or ()), tuple(before or ()), tuple(at or ()))

    @property
    def names_a_cue(self) -> bool:
        """Whether a cue moment was asked for, which is what replaces the whole run of a slide's states."""
        return bool(self.after or self.before)


EVERY_PANEL = Selection()
"""What a run that names no selector draws, which is every slide at every cue it declares."""


def panels_of(
    slides: Slides, times: Mapping[str, float], chosen: Selection = EVERY_PANEL
) -> list[tuple[Freeze, str | None, float]]:
    """(the state to draw, the cue it shows, the second it sits at) for every panel of one scene.

    Each slide contributes the state it opens on and then one state per cue it declares, so a reader
    meets the slide as a viewer first meets it and then once per change. A cue with no resolved
    second still gets its panel, because a storyboard is read before `cue` has run as well as after.

    A selection narrows that set rather than replacing it. A run that names a cue draws that cue's
    own moment and drops the opening states, because a reader who asked for one reveal is asking
    about the change and not about the deck.
    """
    out: list[tuple[Freeze, str | None, float]] = []
    for slide, cue_ids in slides.items():
        if chosen.slides and slide not in chosen.slides:
            continue
        opening = min((times[cue_id] for cue_id in cue_ids if cue_id in times), default=SECTION_START_SECONDS)
        at = {cue_id: round(times.get(cue_id, opening), SECOND_DIGITS) for cue_id in cue_ids}
        if chosen.names_a_cue:
            out += [(Freeze(slide, cue=cue_id), cue_id, at[cue_id]) for cue_id in cue_ids if cue_id in chosen.after]
            out += [(Freeze(slide, before=cue_id), cue_id, at[cue_id]) for cue_id in cue_ids if cue_id in chosen.before]
            continue
        out.append((Freeze.state(slide, (), cue_ids), None, round(opening, SECOND_DIGITS)))
        out += [(Freeze(slide, cue=cue_id), cue_id, at[cue_id]) for cue_id in cue_ids]
    return _at(out, chosen.at) if chosen.at else out


def _at(
    panels: list[tuple[Freeze, str | None, float]], seconds: Sequence[float]
) -> list[tuple[Freeze, str | None, float]]:
    """The panel on screen at each named second, which is the latest one that has already started.

    A second before the first panel of the scene names nothing, because the scene was not playing
    yet and the nearest panel would be a picture of a moment the caller did not ask about.
    """
    ordered = sorted(panels, key=lambda panel: panel[2])
    wanted: list[tuple[Freeze, str | None, float]] = []
    for second in seconds:
        showing = [panel for panel in ordered if panel[2] <= second]
        if showing and showing[-1] not in wanted:
            wanted.append(showing[-1])
    return wanted


def storyboard(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    slide: Sequence[str] | None = None,
    after: Sequence[str] | None = None,
    before: Sequence[str] | None = None,
    at: Sequence[float] | None = None,
) -> StoryboardResult:
    """Freeze every slide at every cue onto one page, and write nothing else.

    It opens the pages the recorder opens, with the same params, the same words and the same motion,
    so a panel is a frame of the film being built rather than a picture of something near it. It
    judges nothing, so a page that will not draw is a line on the stream and never a finding.

    One panel of a storyboard is still a storyboard, so the five selectors narrow what it draws and
    the sheet it writes is the same sheet with fewer panels on it.
    """
    chosen = Selection.of(slide, after, before, at)
    wanted = selects(only)
    sections = [one for one in inputs.document.page_sections if wanted(one.number)]
    panels = _draw(inputs, run, sections, chosen) if sections else []
    voices = section_voices(inputs, inputs.spoken())
    page = write_page(inputs.workspace, panels, title=inputs.document.name, voices=voices) if panels else None

    if page is not None:
        run.wrote(page)
    return run.result(
        StoryboardResult,
        storyboard=None if page is None else inputs.relative(page),
        panels=tuple(panels),
    )


def _draw(inputs: Inputs, run: Run, sections: Sequence[PageSection], chosen: Selection) -> list[Panel]:
    """Every panel of every named section, drawn by one browser holding one page open."""
    times = inputs.cue_times()
    with pictures(inputs, run) as made:
        page = made.page()
        reports = page.reports([one.page for one in sections])
        sheet = Sheet(inputs, run, dict.fromkeys((one.page for one in sections), page), inputs.workspace.storyboard_dir)
        for section in sections:
            run.check()
            slides = slide_cues(scene_entry(_catalog(reports, section.page), section.scene))
            if slides is None:
                run.note(
                    f"section {section.number} plays scene {section.scene} of {section.page}, which published no "
                    "catalog, so it has no slide to draw.",
                    level=Level.ERROR,
                )
                continue
            resolved = times.times(section.number) if times is not None else {}
            for freeze, cue_id, at in panels_of(slides, resolved, chosen):
                sheet.frozen(section, freeze)
                sheet.panel(section, freeze, cue_id, at)
    return sheet.panels


def _catalog(reports: Mapping[str, PageReport], page: str) -> tuple[MeasuredScene, ...] | None:
    """What one page published, or None when it published nothing this run could read."""
    report = reports.get(page)
    return report.catalog if report is not None and report.catalog else None


__all__ = [
    "EVERY_PANEL",
    "Selection",
    "panels_of",
    "storyboard",
]
