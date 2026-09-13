"""Headless Chromium (Playwright): recording a page, screenshots, and rendering slates."""

from __future__ import annotations

import html
import logging
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..artifacts import Sidecar, gap_time
from ..errors import ToolError

log = logging.getLogger(__name__)

# The page may expose a Promise the recorder awaits before starting the narration clock.
READY_JS = "() => (window.__sceneReady instanceof Promise ? window.__sceneReady : null)"
FONTS_JS = "() => document.fonts.ready"
# The page is covered in magenta from its first paint until the narration clock starts, so the
# first clean frame in the recording is t=0 no matter when the recorder began capturing.
COVER_JS = """() => {
  const add = () => {
    if (document.getElementById("__t0cover")) return;
    const d = document.createElement("div");
    d.id = "__t0cover";
    d.style.cssText = "position:fixed;inset:0;background:#ff00ff;z-index:2147483647;pointer-events:none";
    // Chromium's screencast only emits a frame when the compositor paints one, and a static
    // cover paints once. If that single paint lands before capture has attached, the cover is
    // never recorded. A small element that never stops moving keeps frames flowing, so the
    // first captured frame is magenta no matter when capture began.
    const s = document.createElement("style");
    s.textContent = "@keyframes __t0spin{to{transform:rotate(360deg)}}";
    d.appendChild(s);
    const m = document.createElement("div");
    m.style.cssText = "position:absolute;left:8px;top:8px;width:6px;height:6px;background:#ff10ff;"
      + "animation:__t0spin .5s linear infinite";
    d.appendChild(m);
    (document.body || document.documentElement).appendChild(d);
  };
  if (document.documentElement) add(); else document.addEventListener("DOMContentLoaded", add, { once: true });
}"""
# Remove the cover and start the page clock in the same tick.
START_JS = """() => new Promise((resolve) => {
  const d = document.getElementById("__t0cover");
  if (d) d.remove();
  // The frame that shows the cover gone is composited on the next animation frame, and
  // that frame is the recording's t=0, so the clock starts there rather than now.
  requestAnimationFrame(() => {
    if (window.DeckTalk && window.DeckTalk.startClock) window.DeckTalk.startClock();
    resolve(performance.now());
  });
})"""
# What the runtime could not honor: unknown cue ids, cues no step owns, KaTeX that never loaded.
WARNINGS_JS = "() => (window.__decktalk && window.__decktalk.warnings) || []"
# Whether the runtime is present and the page registered at least one scene.
HAS_CATALOG_JS = "() => !!(window.__decktalk && window.__decktalk.catalog && window.__decktalk.catalog.length)"
NO_CATALOG = "no window.__decktalk.catalog (is decktalk-runtime.js included, and does the page register a scene?)"

SLATE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;background:{bg};color:#f4f6f8;
font-family:Inter,-apple-system,Helvetica,Arial,sans-serif;overflow:hidden}}
.wrap{{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 160px;gap:28px}}
.eyebrow{{font-size:30px;color:#9aa4b2;letter-spacing:.08em;text-transform:uppercase}}
.title{{font-size:96px;font-weight:700;letter-spacing:-2px;line-height:1.05}}
.sub{{font-size:44px;color:#9aa4b2}}
.foot{{position:absolute;left:160px;bottom:120px;font-size:30px;color:#5b6573}}
</style></head><body><div class="wrap">
<div class="eyebrow">{eyebrow}</div><div class="title">{title}</div><div class="sub">{sub}</div>
</div><div class="foot">{foot}</div></body></html>"""


@contextmanager
def chromium() -> Iterator[Any]:
    """A launched headless Chromium, closed on exit. ToolError with the fix when unavailable."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            raise ToolError(f"could not launch Chromium ({str(exc).splitlines()[0]}). Run `decktalk setup`.") from exc
        try:
            yield browser
        finally:
            browser.close()


def await_ready(page: Any) -> None:
    for js in (FONTS_JS, READY_JS):
        try:
            page.evaluate(js)
        except Exception:
            pass


def page_error_text(err: Any) -> str:
    """One line for an uncaught page exception: the message, and the file and line when Chromium gives them."""
    message = str(getattr(err, "message", None) or err).strip().splitlines()[0] if str(err).strip() else "error"
    name = getattr(err, "name", None)
    if name and not message.startswith(f"{name}:"):
        message = f"{name}: {message}"
    stack = str(getattr(err, "stack", "") or "")
    m = re.search(r"((?:file|https?)://\S+?):(\d+)(?::\d+)?\)?\s*$", stack, re.MULTILINE)
    if m:
        message += f" ({m.group(1).split('?', 1)[0].rsplit('/', 1)[-1]}:{m.group(2)})"
    return message


def page_errors(page: Any, caught: list[str], label: str) -> list[str]:
    """The page's uncaught exceptions, plus one entry when the runtime catalog is missing. Each is logged."""
    errors = list(caught)
    try:
        if not page.evaluate(HAS_CATALOG_JS):
            errors.append(NO_CATALOG)
    except Exception:
        errors.append(NO_CATALOG)
    for e in errors:
        log.warning("[page] %s  page error: %s", label, e)
    return errors


def page_warnings(page: Any, label: str) -> list[str]:
    """The runtime's warnings for this page, each logged as a warning under `label`."""
    try:
        found = page.evaluate(WARNINGS_JS)
    except Exception:
        return []
    warnings = [str(w) for w in found] if isinstance(found, list) else []
    for w in warnings:
        log.warning("[page] %s  %s", label, w)
    return warnings


def record_page(
    browser: Any,
    url: str,
    seconds: float,
    out: Path,
    *,
    settle_seconds: float,
    min_lead_seconds: float,
    width: int,
    height: int,
    color_scheme: str,
) -> Sidecar:
    """Record `url` for `seconds` after the narration clock starts; write out and its sidecar."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="decktalk-rec-"))
    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=1,
        color_scheme=color_scheme,
        reduced_motion="no-preference",
        record_video_dir=str(tmp_dir),
        record_video_size={"width": width, "height": height},
    )
    created = time.monotonic()
    context.add_init_script(COVER_JS + "\n;(" + COVER_JS + ")();")
    page = context.new_page()
    caught: list[str] = []
    page.on("pageerror", lambda e: caught.append(page_error_text(e)))
    page.goto(url, wait_until="load")
    loaded = time.monotonic()
    await_ready(page)
    # Settle after load, and never start the clock before the recorder has certainly begun
    # capturing (Windows starts its capture late); the cover makes the wait invisible.
    wait = max(settle_seconds, min_lead_seconds - (time.monotonic() - created))
    page.wait_for_timeout(wait * 1000)
    page.evaluate(START_JS)
    started = time.monotonic()
    page.wait_for_timeout(seconds * 1000)
    warnings = page_warnings(page, out.stem)
    errors = page_errors(page, caught, out.stem)
    gaps = page.evaluate("() => (window.__decktalk && window.__decktalk.frameGaps) || []")
    sync_log = page.evaluate("() => (window.__decktalk && window.__decktalk.syncLog) || []")
    for entry in sync_log if isinstance(sync_log, list) else []:
        log.debug(
            "[sync] %s  cue %.3f  run %.3f  first word on %s",
            entry.get("text"),
            entry.get("cueAt", 0),
            entry.get("runAt", 0),
            entry.get("firstOn"),
        )
    frame_gaps = [(gap_time(g.get("at")), int(g["ms"])) for g in gaps if isinstance(g, dict)]
    after_start = [(at, ms) for at, ms in frame_gaps if at is not None and at > 0]
    if after_start:
        worst = max(ms for _, ms in after_start)
        log.warning("[page] %s  %d frame stall(s) after narration t=0, worst %d ms", out.stem, len(after_start), worst)
    if len(after_start) < len(frame_gaps):
        log.debug("[page] %s  %d frame stall(s) under the cover", out.stem, len(frame_gaps) - len(after_start))
    video = page.video
    context.close()
    src = Path(video.path()) if video else None
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    if src is None or not src.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise ToolError(f"Chromium produced no video for {out.name}")
    shutil.move(str(src), str(out))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    sidecar = Sidecar(
        url=url,
        requested_seconds=seconds,
        settle_seconds=round(started - loaded, 3),
        load_seconds=round(loaded - created, 3),
        lead_seconds=round(started - created, 3),
        warnings=warnings,
        page_errors=errors,
        frame_gaps=frame_gaps,
        sync_log=[dict(e) for e in sync_log if isinstance(e, dict)],
    )
    sidecar.save(out.with_suffix(".json"))
    return sidecar


def screenshot(page: Any, url: str, out: Path, *, settle_ms: int) -> None:
    page.goto(url)
    await_ready(page)
    page.wait_for_timeout(settle_ms)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out))
    page_warnings(page, out.stem)


def render_slate(
    out: Path,
    *,
    title: str,
    sub: str = "",
    eyebrow: str = "slate",
    foot: str = "",
    width: int,
    height: int,
    background: str = "#0e1116",
) -> Path:
    """A titled placeholder frame, for a section whose clip is missing."""
    doc = SLATE_HTML.format(
        w=width,
        h=height,
        bg=background,
        eyebrow=html.escape(eyebrow),
        title=html.escape(title),
        sub=html.escape(sub),
        foot=html.escape(foot),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with chromium() as browser:
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(doc)
        await_ready(page)
        page.screenshot(path=str(out))
    return out
