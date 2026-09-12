"""Screenshots for review: one PNG per step, or frames from a section as it plays.

    decktalk shots                          # every step of every page in scenes.json -> build/shots/
    decktalk shots --page deck/index.html   # one page
    decktalk shots --section 3 --at 4.5 --at 10   # frames while section 3 plays with its resolved cues

Step mode loads each page with no query (the runtime's index mode) and reads
window.__decktalk.catalog for the scene and step ids, then opens ?step=ID for each,
which mounts that step with everything revealed. Frame mode opens the page exactly
as the recorder does (?scene=&beats=&t0=) and screenshots at the given seconds
after narration t=0, so you can check that a reveal lands on its word before
spending a render.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .project import Project
from .record import DEFAULT_SETTLE, READY_JS, scene_url

WIDTH, HEIGHT = 1920, 1080


def _launch(pw: Any) -> Any:
    try:
        return pw.chromium.launch()
    except Exception as exc:
        raise SystemExit(f"error: could not launch Chromium ({exc}). Run `decktalk setup`.") from exc


def shots_steps(project: Project, pages: list[str] | None = None, steps: list[str] | None = None) -> int:
    from playwright.sync_api import sync_playwright

    pages = pages or project.page_files
    if not pages:
        raise SystemExit("error: no HTML pages in scenes.json")
    out_root = project.shots_dir
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.on("pageerror", lambda e: print(f"  [page error] {e}"))
        for rel in pages:
            html = project.path(rel)
            if not html.exists():
                print(f"[skip] {rel}: not found")
                continue
            base = html.resolve().as_uri()
            page.goto(base)
            catalog = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            if not catalog:
                print(f"[skip] {rel}: no window.__decktalk.catalog (is decktalk-runtime.js included?)")
                continue
            ids = [s for scene in catalog for s in scene["steps"]]
            if steps:
                ids = [s for s in ids if s in set(steps)]
            out_dir = out_root / html.stem if len(pages) > 1 else out_root
            out_dir.mkdir(parents=True, exist_ok=True)
            for sid in ids:
                page.goto(f"{base}?step={sid}")
                page.evaluate("() => document.fonts.ready")
                try:
                    page.evaluate(READY_JS)
                except Exception:
                    pass
                page.wait_for_timeout(400)
                target = out_dir / f"step-{sid}.png"
                page.screenshot(path=str(target))
                print(f"wrote {target.relative_to(project.root)}")
        browser.close()
    return 0


def shots_frames(project: Project, section: int, at: list[float], settle: float = DEFAULT_SETTLE) -> int:
    from playwright.sync_api import sync_playwright

    sec = project.section(section)
    if sec is None or not sec.file:
        raise SystemExit(f"error: section {section} is not a page section in scenes.json")
    params = sec.params
    beats = project.beats_data().get(sec.key)
    if beats and "beats" not in params:
        params["beats"] = beats
    url = scene_url(project, sec.file, sec.scene, params, settle)
    out_dir = project.shots_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = _launch(pw)
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.goto(url, wait_until="load")
        try:
            page.evaluate(READY_JS)
        except Exception:
            pass
        page.wait_for_timeout(settle * 1000)
        clock0 = page.evaluate("() => performance.now()")
        for t in sorted(at):
            page.wait_for_function("(ms) => performance.now() >= ms", arg=clock0 + t * 1000)
            target = out_dir / f"section-{sec.key}-at-{t:g}s.png"
            page.screenshot(path=str(target))
            fired = page.evaluate("() => (window.__decktalk && window.__decktalk.fired) || []")
            print(f"wrote {target.relative_to(project.root)}  fired: {', '.join(fired) or '-'}")
        browser.close()
    return 0


def shots(
    project: Project,
    *,
    pages: list[str] | None = None,
    steps: list[str] | None = None,
    section: int | None = None,
    at: list[float] | None = None,
) -> int:
    if section is not None:
        return shots_frames(project, section, at or [0.5])
    return shots_steps(project, pages, steps)


__all__ = ["shots", "shots_frames", "shots_steps", "Path"]
