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
from ..media.browser import START_JS, await_ready, chromium, open_page, screenshot
from ..media.origin import page_url
from ..model import PageSection, Project
from ..verdicts import Findings
from .record import scene_params, scene_url

log = logging.getLogger(__name__)


def screenshot_slides(
    project: Project,
    pages: list[str] | None = None,
    slides: list[str] | None = None,
    cues: list[str] | None = None,
) -> list[Path]:
    """One PNG per slide, or one per cue of a single slide when cue ids are given."""
    if cues and (not slides or len(slides) != 1):
        raise ConfigError("a cue screenshot needs exactly one slide: pass one --slide with --after")
    cfg = project.settings.record
    video = project.settings.video
    pages = pages or project.page_files
    if not pages:
        raise ConfigError("no HTML pages in decktalk.toml")
    written: list[Path] = []
    with chromium(project.settings.record.browser_path) as browser:
        page, _assets = open_page(browser, project.root, width=video.width, height=video.height)
        page.on("pageerror", lambda e: log.warning("page error: %s", e))
        for rel in pages:
            html = project.path(rel)
            if not html.exists():
                log.warning("%s: not found; skipped", rel)
                continue
            base = page_url(rel)
            page.goto(base)
            catalog = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            if not catalog:
                log.warning("%s: no window.__decktalk.catalog (is decktalk-runtime.js included?)", rel)
                continue
            ids = [s for scene in catalog for s in scene["slides"]]
            if slides:
                ids = [s for s in ids if s in set(slides)]
            out_dir = project.screenshots_dir / html.stem if len(pages) > 1 else project.screenshots_dir
            targets = [(f"{base}?slide={sid}", out_dir / f"slide-{sid}.png") for sid in ids]
            if cues:
                # The runtime freezes the slide at the named cue, so each file shows one moment of the slide.
                targets = [
                    (f"{base}?slide={sid}&after={quote(cue, safe='')}", out_dir / f"slide-{sid}-after-{cue}.png")
                    for sid in ids
                    for cue in cues
                ]
            for url, target in targets:
                screenshot(page, url, target, settle_ms=cfg.screenshot_settle_ms)
                log.info("wrote %s", target.relative_to(project.root))
                written.append(target)
    return written


def screenshot_frames(project: Project, section: int, at: list[float]) -> list[Path]:
    cfg = project.settings.record
    video = project.settings.video
    sec = project.section(section)
    if not isinstance(sec, PageSection):
        raise ConfigError(f"section {section} is not a page section")
    url = scene_url(project, sec, scene_params(sec, project.cue_times()))
    project.screenshots_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with chromium(project.settings.record.browser_path) as browser:
        page, _assets = open_page(browser, project.root, width=video.width, height=video.height)
        page.goto(url, wait_until="load")
        await_ready(page)
        page.wait_for_timeout(cfg.settle_seconds * 1000)
        page.evaluate(START_JS)
        clock0 = page.evaluate("() => performance.now()")
        for t in sorted(at):
            page.wait_for_function("(ms) => performance.now() >= ms", arg=clock0 + t * 1000)
            target = project.screenshots_dir / f"section-{sec.key}-at-{t:g}s.png"
            page.screenshot(path=str(target))
            fired = page.evaluate("() => (window.__decktalk && window.__decktalk.fired) || []")
            log.info("wrote %s  fired: %s", target.relative_to(project.root), ", ".join(fired) or "-")
            written.append(target)
    return written


@dataclass
class ScreenshotsResult:
    """The PNGs one screenshots run wrote."""

    files: list[Path] = field(default_factory=list)

    @property
    def findings(self) -> Findings:
        """None. `screenshots` writes pictures for a person to look at and judges nothing."""
        return Findings()

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {"files": [relative(f, root) for f in self.files]}


def screenshots(
    project: Project,
    *,
    pages: list[str] | None = None,
    slides: list[str] | None = None,
    section: int | None = None,
    at: list[float] | None = None,
    cues: list[str] | None = None,
) -> ScreenshotsResult:
    """One PNG per slide, per cue of one slide, or per second of a playing section."""
    if section is not None:
        return ScreenshotsResult(files=screenshot_frames(project, section, at or [0.5]))
    return ScreenshotsResult(files=screenshot_slides(project, pages, slides, cues))
