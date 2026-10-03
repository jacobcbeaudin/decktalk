"""The titled placeholder frame a section whose clip is missing is cut from."""

from __future__ import annotations

import html
from pathlib import Path

from decktalk.media.browser import chromium
from decktalk.media.encode import css_color
from decktalk.media.pages import await_painted

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


def render_slate(
    out: Path,
    *,
    title: str,
    sub: str = "",
    eyebrow: str,
    foot: str = "",
    width: int,
    height: int,
    background: str,
    browser_path: str,
    policy: str,
    spend: bool,
) -> Path:
    """A titled placeholder frame, for a section whose clip is missing.

    `background` is `[video] slate_color`, written as ffmpeg writes a colour, because the plain
    frame this stands in for is drawn by ffmpeg from the same setting.
    """
    doc = SLATE_HTML.format(
        w=width,
        h=height,
        bg=css_color(background),
        eyebrow=html.escape(eyebrow),
        title=html.escape(title),
        sub=html.escape(sub),
        foot=html.escape(foot),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with chromium(browser_path, policy=policy, spend=spend) as browser:
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(doc)
        await_painted(page)
        page.screenshot(path=str(out))
    return out
