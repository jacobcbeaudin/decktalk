"""Screenshots for review: one PNG per step, or frames from a section as it plays.

Step mode loads each page with no query (the runtime's index mode) and reads
window.__decktalk.catalog for the scene and step ids, then opens ?step=ID for each,
which mounts that step with everything revealed. With cue ids and a single step, each
shot opens ?step=ID&cue=CUE instead, which freezes the step at the moment that cue
fires. Frame mode opens the page exactly as the recorder does and screenshots at the
given seconds after narration t=0.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from ..errors import ConfigError
from ..media.browser import START_JS, await_ready, chromium, screenshot
from ..project import PageSection, Project
from .record import scene_params, scene_url

log = logging.getLogger(__name__)


def shoot_steps(
    project: Project,
    pages: list[str] | None = None,
    steps: list[str] | None = None,
    cues: list[str] | None = None,
) -> list[Path]:
    """One PNG per step, or one per cue of a single step when cue ids are given."""
    if cues and (not steps or len(steps) != 1):
        raise ConfigError("a cue screenshot needs exactly one step: pass one --step with --cue")
    cfg = project.settings.record
    video = project.settings.video
    pages = pages or project.page_files
    if not pages:
        raise ConfigError("no HTML pages in decktalk.toml")
    written: list[Path] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": video.width, "height": video.height})
        page.on("pageerror", lambda e: log.warning("page error: %s", e))
        for rel in pages:
            html = project.path(rel)
            if not html.exists():
                log.warning("%s: not found; skipped", rel)
                continue
            base = html.resolve().as_uri()
            page.goto(base)
            catalog = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            if not catalog:
                log.warning("%s: no window.__decktalk.catalog (is decktalk-runtime.js included?)", rel)
                continue
            ids = [s for scene in catalog for s in scene["steps"]]
            if steps:
                ids = [s for s in ids if s in set(steps)]
            out_dir = project.shots_dir / html.stem if len(pages) > 1 else project.shots_dir
            shots = [(f"{base}?step={sid}", out_dir / f"step-{sid}.png") for sid in ids]
            if cues:
                # The runtime freezes the step at the named cue, so each file shows one moment of the step.
                shots = [
                    (f"{base}?step={sid}&cue={quote(cue, safe='')}", out_dir / f"step-{sid}-cue-{cue}.png")
                    for sid in ids
                    for cue in cues
                ]
            for url, target in shots:
                screenshot(page, url, target, settle_ms=cfg.shot_settle_ms)
                log.info("wrote %s", target.relative_to(project.root))
                written.append(target)
    return written


def shoot_frames(project: Project, section: int, at: list[float]) -> list[Path]:
    cfg = project.settings.record
    video = project.settings.video
    sec = project.section(section)
    if not isinstance(sec, PageSection):
        raise ConfigError(f"section {section} is not a page section")
    url = scene_url(project, sec, scene_params(sec, project.beats()))
    project.shots_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": video.width, "height": video.height})
        page.goto(url, wait_until="load")
        await_ready(page)
        page.wait_for_timeout(cfg.settle_seconds * 1000)
        page.evaluate(START_JS)
        clock0 = page.evaluate("() => performance.now()")
        for t in sorted(at):
            page.wait_for_function("(ms) => performance.now() >= ms", arg=clock0 + t * 1000)
            target = project.shots_dir / f"section-{sec.key}-at-{t:g}s.png"
            page.screenshot(path=str(target))
            fired = page.evaluate("() => (window.__decktalk && window.__decktalk.fired) || []")
            log.info("wrote %s  fired: %s", target.relative_to(project.root), ", ".join(fired) or "-")
            written.append(target)
    return written


def shoot(
    project: Project,
    *,
    pages: list[str] | None = None,
    steps: list[str] | None = None,
    section: int | None = None,
    at: list[float] | None = None,
    cues: list[str] | None = None,
) -> list[Path]:
    if section is not None:
        return shoot_frames(project, section, at or [0.5])
    return shoot_steps(project, pages, steps, cues)
