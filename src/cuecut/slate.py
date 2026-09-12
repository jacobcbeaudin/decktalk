"""Render a slate PNG (a titled placeholder frame) with headless Chromium.

Used when a section's clip is missing: the film still assembles, and the slate says
which clip to drop where, instead of a blank frame that reads as a broken render.
"""

from __future__ import annotations

import html
from pathlib import Path

WIDTH, HEIGHT = 1920, 1080

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1920px;height:1080px;background:#0e1116;color:#f4f6f8;
font-family:Inter,-apple-system,Helvetica,Arial,sans-serif;overflow:hidden}}
.wrap{{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 160px;gap:28px}}
.eyebrow{{font-size:30px;color:#9aa4b2;letter-spacing:.08em;text-transform:uppercase}}
.title{{font-size:96px;font-weight:700;letter-spacing:-2px;line-height:1.05}}
.sub{{font-size:44px;color:#9aa4b2}}
.foot{{position:absolute;left:160px;bottom:120px;font-size:30px;color:#5b6573}}
</style></head><body><div class="wrap">
<div class="eyebrow">{eyebrow}</div><div class="title">{title}</div><div class="sub">{sub}</div>
</div><div class="foot">{foot}</div></body></html>"""


def render_slate(out: Path, *, title: str, sub: str = "", eyebrow: str = "slate", foot: str = "") -> Path:
    from playwright.sync_api import sync_playwright

    out.parent.mkdir(parents=True, exist_ok=True)
    doc = PAGE.format(
        eyebrow=html.escape(eyebrow), title=html.escape(title), sub=html.escape(sub), foot=html.escape(foot)
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.set_content(doc)
        page.evaluate("() => document.fonts.ready")
        page.screenshot(path=str(out))
        browser.close()
    return out
