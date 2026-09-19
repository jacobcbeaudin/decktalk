"""Screenshots for review: one PNG per slide, or frames from a section as it plays.

Slide mode loads each page with no query (the runtime's index mode) and reads
window.__decktalk.catalog for the scene and slide ids, then opens ?slide=ID for each,
which mounts that slide with everything revealed. With cue ids and a single slide, each
screenshot opens ?slide=ID&after=CUE instead, which freezes the slide at the moment that cue
fires. Frame mode opens the page exactly as the recorder does and screenshots at the
given seconds after narration t=0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..errors import ConfigError
from ..jsonio import relative
from ..media.browser import START_JS, await_ready, chromium, open_page, page_error_text, screenshot
from ..media.origin import page_url
from ..model import PageSection, Project
from ..verdicts import Findings
from .record import scene_params, scene_url

log = logging.getLogger(__name__)

FRAME_WAIT_SLACK_MS = 10_000  # How much longer than the frame's own time the wait for it may run.


def screenshot_slides(
    project: Project,
    pages: list[str] | None = None,
    slides: list[str] | None = None,
    cues: list[str] | None = None,
    before: list[str] | None = None,
) -> list[Screenshot]:
    """One PNG per slide, or one per cue of a single slide when cue ids are given.

    `cues` freezes the slide at the moment each cue fires and `before` freezes it just before, which
    is the pair of moments an author compares to see what one reveal changed.
    """
    if (cues or before) and (not slides or len(slides) != 1):
        raise ConfigError("a cue screenshot needs exactly one slide: pass one --slide with --after or --before")
    cfg = project.settings.record
    video = project.settings.video
    pages = pages or project.page_files
    if not pages:
        raise ConfigError("no HTML pages in decktalk.toml")
    written: list[Screenshot] = []
    errors: list[str] = []
    with chromium(project.settings.record.browser_path) as browser:
        page, _assets = open_page(browser, project.root, width=video.width, height=video.height)
        page.on("pageerror", lambda e: errors.append(page_error_text(e)))
        for rel in pages:
            html = project.path(rel)
            if not html.exists():
                log.warning("%s is not there, so it is skipped", rel)
                continue
            base = page_url(rel)
            page.goto(base)
            catalog = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            if not catalog:
                log.warning("%s: no window.__decktalk.catalog (is decktalk-runtime.js included?)", rel)
                continue
            ids = [s for scene in catalog for s in scene["slides"]]
            if slides:
                wanted = set(slides)
                ids = [s for s in ids if s in wanted]
                if not ids:
                    # A request that matches nothing wrote nothing, and a caller that read exit 0
                    # would take an empty answer for a finished one.
                    every = [s for scene in catalog for s in scene["slides"]]
                    raise ConfigError(
                        f"{rel}: no slide matches {sorted(wanted)}.",
                        hint=(
                            f"The slide ids on this page are {', '.join(every)}."
                            if every
                            else "The page has no slides."
                        ),
                        path=html,
                    )
            out_dir = project.screenshots_dir / html.stem if len(pages) > 1 else project.screenshots_dir
            targets = [(f"{base}?slide={sid}", out_dir / f"slide-{sid}.png", sid, None) for sid in ids]
            if cues or before:
                # The runtime freezes the slide at the named cue, so each file shows one moment of the slide.
                moments = [("after", cue) for cue in cues or []] + [("before", cue) for cue in before or []]
                targets = [
                    (
                        f"{base}?slide={sid}&{edge}={quote(cue, safe='')}",
                        out_dir / f"slide-{sid}-{edge}-{cue}.png",
                        sid,
                        cue,
                    )
                    for sid in ids
                    for edge, cue in moments
                ]
            for url, target, sid, cue in targets:
                errors_before = len(errors)
                screenshot(page, url, target, settle_ms=cfg.screenshot_settle_ms)
                log.info("wrote %s", target.relative_to(project.root))
                written.append(
                    Screenshot(path=target, page=rel, slide=sid, cue=cue, page_errors=tuple(errors[errors_before:]))
                )
    for message in sorted(set(errors)):
        log.warning("page error: %s", message)
    return written


def screenshot_frames(project: Project, section: int, at: list[float]) -> list[Screenshot]:
    cfg = project.settings.record
    video = project.settings.video
    sec = project.section(section)
    if not isinstance(sec, PageSection):
        raise ConfigError(f"section {section} is not a page section")
    url = scene_url(project, sec, scene_params(sec, project.cue_times()))
    project.screenshots_dir.mkdir(parents=True, exist_ok=True)
    written: list[Screenshot] = []
    errors: list[str] = []
    with chromium(project.settings.record.browser_path) as browser:
        page, _assets = open_page(browser, project.root, width=video.width, height=video.height)
        page.on("pageerror", lambda e: errors.append(page_error_text(e)))
        page.goto(url, wait_until="load")
        await_ready(page)
        page.wait_for_timeout(cfg.settle_seconds * 1000)
        page.evaluate(START_JS)
        clock0 = page.evaluate("() => performance.now()")
        for t in sorted(at):
            # The wait lasts as long as the frame is far into the section, so it gets a deadline of its
            # own rather than Playwright's default 30 s, which would end it for any frame past about 30 s.
            deadline_ms = t * 1000 + FRAME_WAIT_SLACK_MS
            page.wait_for_function("(ms) => performance.now() >= ms", arg=clock0 + t * 1000, timeout=deadline_ms)
            target = project.screenshots_dir / f"section-{sec.key}-at-{t:g}s.png"
            page.screenshot(path=str(target))
            fired = page.evaluate("() => (window.__decktalk && window.__decktalk.fired) || []")
            log.info("wrote %s  fired: %s", target.relative_to(project.root), ", ".join(fired) or "-")
            written.append(Screenshot(path=target, page=sec.page, section=sec.number, at=t, page_errors=tuple(errors)))
    for message in sorted(set(errors)):
        log.warning("page error: %s", message)
    return written


@dataclass(frozen=True)
class Screenshot:
    """One PNG and what it shows: the page, the slide, the cue it was frozen at or the second it was taken."""

    path: Path
    page: str = ""
    slide: str | None = None
    cue: str | None = None  # The cue the slide was frozen at, with --after.
    section: int | None = None  # The section played, with --section.
    at: float | None = None  # Seconds after narration t=0, with --section.
    page_errors: tuple[str, ...] = ()

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "file": relative(self.path, root),
            "page": self.page,
            "slide": self.slide,
            "cue": self.cue,
            "section": self.section,
            "at": self.at,
            "page_errors": list(self.page_errors),
        }


@dataclass
class ScreenshotsResult:
    """The PNGs one screenshots run wrote, each with what it shows."""

    files: list[Screenshot] = field(default_factory=list)

    @property
    def paths(self) -> list[Path]:
        """The written files alone, which is what a caller that only wants the paths reads."""
        return [written.path for written in self.files]

    @property
    def findings(self) -> Findings:
        """None. `screenshots` writes pictures for a person to look at and judges nothing."""
        return Findings()

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {"files": [written.to_dict(root) for written in self.files]}


def screenshots(
    project: Project,
    *,
    pages: list[str] | None = None,
    slides: list[str] | None = None,
    section: int | None = None,
    at: list[float] | None = None,
    cues: list[str] | None = None,
    before: list[str] | None = None,
) -> ScreenshotsResult:
    """One PNG per slide, per cue of one slide, or per second of a playing section."""
    if section is not None:
        return ScreenshotsResult(files=screenshot_frames(project, section, at or [0.5]))
    return ScreenshotsResult(files=screenshot_slides(project, pages, slides, cues, before))
