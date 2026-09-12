"""Headless Chromium (Playwright): recording a page, screenshots, and rendering slates."""

from __future__ import annotations

import html
import logging
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..artifacts import Sidecar
from ..errors import ToolError

log = logging.getLogger(__name__)

# The page may expose a Promise the recorder awaits before starting the narration clock.
READY_JS = "() => (window.__sceneReady instanceof Promise ? window.__sceneReady : null)"
FONTS_JS = "() => document.fonts.ready"
# Full-frame magenta for `ms` milliseconds exactly when the narration clock starts.
MARKER_JS = """(ms) => {
  const d = document.createElement("div");
  d.id = "__t0marker";
  d.style.cssText = "position:fixed;inset:0;background:#ff00ff;z-index:2147483647;pointer-events:none";
  document.documentElement.appendChild(d);
  setTimeout(() => d.remove(), ms);
}"""

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


def record_page(
    browser: Any,
    url: str,
    seconds: float,
    out: Path,
    *,
    settle_seconds: float,
    marker_ms: int,
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
    page = context.new_page()
    page.on("pageerror", lambda e: log.warning("page error: %s", e))
    page.goto(url, wait_until="load")
    loaded = time.monotonic()
    await_ready(page)
    page.wait_for_timeout(settle_seconds * 1000)
    started = time.monotonic()
    try:
        page.evaluate(MARKER_JS, marker_ms)
    except Exception:
        pass
    page.wait_for_timeout(seconds * 1000)
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
        settle_seconds=settle_seconds,
        load_seconds=round(loaded - created, 3),
        lead_seconds=round(started - created, 3),
    )
    sidecar.save(out.with_suffix(".json"))
    return sidecar


def screenshot(page: Any, url: str, out: Path, *, settle_ms: int) -> None:
    page.goto(url)
    await_ready(page)
    page.wait_for_timeout(settle_ms)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out))


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
