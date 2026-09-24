"""Every slide at every cue, frozen onto one page, which is the checkpoint before credits are spent.

    build/storyboard/NN/<state>.png   one still per frozen moment
    build/storyboard.html             the contact sheet a person reads

A storyboard judges nothing. It draws what the film will show, in the order it will show it, so a
person can see the whole deck before a single second of speech is bought. One panel of a storyboard
is still a storyboard, which is why the name survives a run that asks for one section.

This module also owns the vocabulary of a frozen state, because a frozen state is what a panel is:
`Freeze` names one, `slide_cues` reads what a scene declares off the catalog the page published, and
`write_page` lays a set of panels out. `check` freezes the same states to compare them, so it reads
all three from here rather than keeping a second spelling of any of them.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page

from decktalk.events import Level
from decktalk.inputs import Inputs
from decktalk.inputs.document import PageSection
from decktalk.inputs.workspace import Workspace
from decktalk.machine import Run
from decktalk.media import MILLISECONDS
from decktalk.media.browser import await_ready, chromium, open_page, read_report, screenshot
from decktalk.media.origin import Allowed, page_url
from decktalk.media.pagereport import PageReport, SceneCatalog
from decktalk.page import Q
from decktalk.results import Panel, StoryboardResult
from decktalk.stages import SECOND_DIGITS, selects
from decktalk.stages.record.capture import as_query, served_paths, words_query

SLIDES_FIELD = "slides"
"""What the catalog entry calls the slides of a scene, in the order the page declares them."""

CUES_FIELD = "cues"
"""What the catalog entry calls the map of the cues each slide of a scene declares."""

SECTION_START_SECONDS = 0.0
"""Where a section's own clock begins, which is when its first slide is already on screen."""

LABEL_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
"""Everything a frozen state's name may not carry into a file name, which becomes one underscore."""

PAGE_TITLE = "storyboard"
"""What the contact sheet calls itself, beside the name of the project it is a storyboard of."""

Slides = dict[str, tuple[str, ...]]
"""Each slide of one scene, in page order, with the wire ids of the cues it declares in cue order."""


@dataclass(frozen=True)
class Freeze:
    """One frozen state of a page: a slide, with its cues fired up to `cue`, or before `before`, or none.

    A frozen frame shows every reveal in its end state and nothing in motion, which is what makes two
    of them comparable and what makes one of them worth looking at.
    """

    slide: str
    cue: str | None = None
    before: str | None = None

    def query(self) -> dict[Q, str]:
        """What this state asks the page for, in the contract's own query keys and no others."""
        if self.cue is not None:
            return {Q.SLIDE: self.slide, Q.AFTER: self.cue}
        if self.before is not None:
            return {Q.SLIDE: self.slide, Q.BEFORE: self.before}
        return {Q.SLIDE: self.slide}

    @property
    def label(self) -> str:
        """This state as one file name, such as `slide-4.1-after-4.1_expand`."""
        text = "-".join(part for key, value in self.query().items() for part in (key.value, value))
        return LABEL_SAFE.sub("_", text)


def slide_cues(entry: SceneCatalog | None) -> Slides | None:
    """Each slide of one scene with the cues it declares, or None when the page published no such scene.

    Ownership is declared: a slide owns exactly the cues the catalog lists against it, which are the
    moments its own elements name plus whatever `data-owns` adds. Nothing here reads an id prefix,
    because a wire id is a slide and a local name and never an arithmetic about a number.
    """
    if entry is None:
        return None
    extra = entry.model_extra or {}
    order = _names(extra.get(SLIDES_FIELD)) or list(entry.elements)
    declared = extra.get(CUES_FIELD)
    listed = declared if isinstance(declared, Mapping) else {}
    return {slide: tuple(dict.fromkeys(_names(listed.get(slide)) or _moments(entry, slide))) for slide in order}


def _names(given: object) -> list[str]:
    """One list of names as the page wrote it, which is nothing at all when it wrote something else."""
    if isinstance(given, str | bytes) or not isinstance(given, Sequence):
        return []
    return [str(one) for one in given]


def _moments(entry: SceneCatalog, slide: str) -> list[str]:
    """The wire ids one slide's own elements name, for a scene that lists its cues nowhere else."""
    return [wire for row in entry.elements.get(slide, ()) for wire in row.moments.values() if wire]


def reports_of(page: Page, inputs: Inputs, files: Sequence[str]) -> dict[str, PageReport]:
    """What each page says about itself, read once per file through the one reader of a page's own words.

    A page the project has not got is absent from the map rather than mapped to nothing, so a caller
    can tell a page that published no scene from a page nobody could open.
    """
    out: dict[str, PageReport] = {}
    for named in dict.fromkeys(files):
        if not inputs.path(named).exists():
            continue
        page.goto(page_url(named))
        await_ready(page)
        out[named] = read_report(page, Path(named).stem)
    return out


def freeze_url(inputs: Inputs, section: PageSection, freeze: Freeze) -> str:
    """The URL of one frozen state of one section, with the section's own params and its own words.

    A still asks the page for a state where a recording asks it to play, so the recorder's own scene
    and clock keys are left off and the freeze keys go on instead. Everything else is what the
    recorder passes, because a still that differed from the film would not be a still of it.
    """
    query: dict[Q, str] = dict(as_query(section.freeze_params))
    words = words_query(inputs, section)
    if words and Q.WORDS not in query:
        query[Q.WORDS] = words
    return page_url(section.page, {**query, **freeze.query()})


def panels_of(slides: Slides, times: Mapping[str, float]) -> list[tuple[Freeze, str | None, float]]:
    """(the state to draw, the cue it shows, the second it sits at) for every panel of one scene.

    Each slide contributes the state it opens on and then one state per cue it declares, so a reader
    meets the slide as a viewer first meets it and then once per change. A cue with no resolved
    second still gets its panel, because a storyboard is read before `cue` has run as well as after.
    """
    out: list[tuple[Freeze, str | None, float]] = []
    for slide, wires in slides.items():
        resolved = [times[wire] for wire in wires if wire in times]
        opening = min(resolved) if resolved else SECTION_START_SECONDS
        out.append((Freeze(slide, before=wires[0]) if wires else Freeze(slide), None, round(opening, SECOND_DIGITS)))
        for wire in wires:
            out.append((Freeze(slide, cue=wire), wire, round(times.get(wire, opening), SECOND_DIGITS)))
    return out


def write_page(workspace: Workspace, panels: Sequence[Panel], *, title: str) -> Path:
    """Lay every panel out on one page and give back where it was written.

    The page sits beside the stills rather than among them, so a reader opens one file and a rebuild
    replaces the whole sheet. One writer draws it whether `storyboard` or `check` froze the frames,
    because two contact sheets of one deck would be two answers to one question.
    """
    build = workspace.build.relative_to(workspace.root) if workspace.build.is_relative_to(workspace.root) else None
    figures = "\n".join(_figure(panel, build) for panel in panels)
    page = workspace.storyboard_path
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_document(title, len(panels), figures), encoding="utf-8")
    return page


def _figure(panel: Panel, build: Path | None) -> str:
    """One panel as a figure, with the picture above the moment it shows."""
    image = Path(panel.image)
    src = image.relative_to(build) if build is not None and image.is_relative_to(build) else image
    moment = f"cue {panel.cue}" if panel.cue else "opening"
    caption = f"section {panel.section} &middot; slide {panel.slide} &middot; {moment} &middot; {panel.at:.2f}s"
    return (
        f'<figure><img loading="lazy" src="{html.escape(src.as_posix())}" alt="{html.escape(caption)}">'
        f"<figcaption>{caption}</figcaption></figure>"
    )


def _document(title: str, count: int, figures: str) -> str:
    """The whole contact sheet, which is one plain page that needs nothing to open it."""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        f"<title>{html.escape(title)} {PAGE_TITLE}</title>\n"
        "<style>\n"
        "body{margin:0;padding:32px;background:#11141a;color:#e8ecf2;"
        "font-family:Inter,-apple-system,Helvetica,Arial,sans-serif}\n"
        "h1{font-size:20px;font-weight:600;margin:0 0 4px}\n"
        "p{color:#9aa4b2;margin:0 0 28px;font-size:14px}\n"
        ".sheet{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px}\n"
        "figure{margin:0}\n"
        "img{width:100%;display:block;border-radius:6px;background:#000}\n"
        "figcaption{color:#9aa4b2;font-size:12px;padding-top:6px}\n"
        "</style></head><body>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        f"<p>{count} panel(s), in the order the film plays them.</p>\n"
        f'<div class="sheet">\n{figures}\n</div>\n'
        "</body></html>\n"
    )


def storyboard(inputs: Inputs, run: Run, *, only: Sequence[int] | None = None) -> StoryboardResult:
    """Freeze every slide at every cue onto one page, and write nothing else.

    It opens the pages the recorder opens, with the same params, the same words and the same motion,
    so a panel is a frame of the film being built rather than a picture of something near it. It
    judges nothing, so a page that will not draw is a line on the stream and never a finding.
    """
    wanted = selects(only)
    sections = [one for one in inputs.document.page_sections if wanted(one.number)]
    panels = _draw(inputs, run, sections) if sections else []
    page = write_page(inputs.workspace, panels, title=inputs.document.name) if panels else None
    if page is not None:
        run.wrote(page)
    return run.result(
        StoryboardResult,
        storyboard=None if page is None else inputs.relative(page),
        panels=tuple(panels),
    )


def _draw(inputs: Inputs, run: Run, sections: Sequence[PageSection]) -> list[Panel]:
    """Every panel of every named section, drawn by one browser holding one page open."""
    video, cfg = inputs.settings.video, inputs.settings.record
    settle = int(cfg.screenshot_settle_seconds * MILLISECONDS)
    times = inputs.cue_times()
    drawn: list[Panel] = []
    with chromium(cfg.browser_path) as browser:
        page, _assets = open_page(
            browser,
            Allowed.of(inputs.root, served_paths(inputs)),
            width=video.width,
            height=video.height,
            color_scheme=cfg.color_scheme,
            motion=inputs.settings.motion,
            documents=inputs.documents(),
        )
        reports = reports_of(page, inputs, [one.page for one in sections])
        for section in sections:
            run.check()
            slides = slide_cues(_entry(_catalog(reports, section.page), section.scene))
            if slides is None:
                run.note(
                    f"section {section.number} plays scene {section.scene} of {section.page}, which published no "
                    "catalog, so it has no slide to draw.",
                    level=Level.ERROR,
                )
                continue
            resolved = times.times(section.number) if times is not None else {}
            drawn += _section_panels(inputs, run, page, section, slides, resolved, settle=settle)
    return drawn


def _section_panels(
    inputs: Inputs,
    run: Run,
    page: Page,
    section: PageSection,
    slides: Slides,
    times: Mapping[str, float],
    *,
    settle: int,
) -> list[Panel]:
    """Every panel of one section, each still written under that section's own directory."""
    out: list[Panel] = []
    for freeze, wire, at in panels_of(slides, times):
        target = inputs.workspace.storyboard_dir / section.key / f"{freeze.label}.png"
        screenshot(page, freeze_url(inputs, section, freeze), target, settle_ms=settle)
        run.wrote(target)
        out.append(
            Panel(section=section.number, slide=freeze.slide, cue=wire, at=at, image=inputs.relative(target)),
        )
    return out


def _entry(entries: Sequence[SceneCatalog] | None, scene: str) -> SceneCatalog | None:
    """The catalog entry for one scene of one page, or None when the page published no such scene."""
    return next((one for one in entries or () if str(one.scene) == str(scene)), None)


def _catalog(reports: Mapping[str, PageReport], page: str) -> tuple[SceneCatalog, ...] | None:
    """What one page published, or None when it published nothing this run could read."""
    report = reports.get(page)
    return report.catalog if report is not None and report.catalog else None


__all__ = [
    "CUES_FIELD",
    "SLIDES_FIELD",
    "Freeze",
    "Slides",
    "reports_of",
    "freeze_url",
    "panels_of",
    "slide_cues",
    "storyboard",
    "write_page",
]
