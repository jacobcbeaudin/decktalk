# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.50"]
# ///
"""Generate every graphic from one source: hero, how-it-works (wide and stacked), alignment,
mark, wordmark, favicon. The README reads assets/, the docs site reads docs/images/ and docs/logo/.

    uv run scripts/build_assets.py            # writes assets/*.svg, docs/images/*.svg, docs/logo/*.svg, docs/favicon.svg
    uv run scripts/build_assets.py --check    # exit 1 if the committed files would change

Every variant (light/dark, wide/stacked) comes from the same builders and one palette map, so
they cannot drift. Inter Tight subsets (OFL, assets/fonts/) are embedded as base64 so GitHub
and PyPI render the intended face; word positions in the hero are measured in Chromium with
that exact font, so the tick under each word is under the word.

Motion rules (from the design review): base styles are the END state, keyframes carry the start
values, so `prefers-reduced-motion: reduce` shows the finished frame. Loops dissolve back to the
start over the last 4 % instead of snapping.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"

LIGHT = {
    "bg": "#ffffff",
    "ink": "#0a0a0a",
    "dim": "#a3a3ad",  # unspoken words, 2.5:1 on white
    "mute": "#71717a",  # labels, captions
    "block": "#f4f4f5",
    "hair": "#e4e4e7",
    "bar": "#d4d4d8",
    "tick": "#c4c4c8",
    "blue": "#2c1fea",
    "on_blue": "#ffffff",
}
DARK = {
    "bg": "#0e0e0f",
    "ink": "#f4f4f5",
    "dim": "#52525b",
    "mute": "#8b8b94",
    "block": "#19191c",
    "hair": "#27272a",
    "bar": "#3f3f46",
    "tick": "#3f3f46",
    "blue": "#7c8cff",  # electric hue kept, 6.5:1 on the dark ground
    "on_blue": "#0e0e0f",
}

SANS = "'DT Sans', 'Inter Tight', 'Inter', -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
SENTENCE = ["The", "curve", "rises,", "then", "the", "number", "lands."]
CUE_WORDS = {1, 5}  # curve, number
HERO_W, HERO_H = 1200, 300
HEAD_START, HEAD_END = 5, 64  # % of the loop the playhead travels


def font_face() -> str:
    path = FONTS / "InterTight.woff2"  # variable font, wght 100-900, subset to the glyphs used
    if not path.exists():
        sys.exit(f"missing {path}; see assets/fonts/LICENSE.txt for how it was made")
    b64 = base64.b64encode(path.read_bytes()).decode()
    return (
        "@font-face{font-family:'DT Sans';font-weight:100 900;font-style:normal;"
        f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}"
    )


def reduced_motion() -> str:
    return "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


# ---- measuring --------------------------------------------------------------------------


def measure_words(words: list[str], font: str, letter_spacing: str) -> tuple[list[float], float]:
    """Advance widths of each word and of a space, in Chromium with the embedded font."""
    from playwright.sync_api import sync_playwright

    html = f"""<style>{font_face()}</style><svg xmlns="http://www.w3.org/2000/svg" width="2000" height="100">
      <text id="t" x="0" y="50" style="font:{font};letter-spacing:{letter_spacing}">{"".join(f'<tspan id="w{i}">{w}</tspan>' for i, w in enumerate(words))}<tspan id="sp"> </tspan><tspan id="sp2">a a</tspan><tspan id="aa">aa</tspan></text></svg>"""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page()
        p.set_content(html)
        p.evaluate("() => document.fonts.ready")
        p.wait_for_timeout(200)
        widths = p.evaluate(
            "(n) => Array.from({length:n}, (_, i) => document.getElementById('w'+i).getComputedTextLength())",
            len(words),
        )
        space = p.evaluate(
            "() => document.getElementById('sp2').getComputedTextLength() - document.getElementById('aa').getComputedTextLength()"
        )
        b.close()
    return [float(w) for w in widths], float(space)


# ---- hero --------------------------------------------------------------------------------


def hero(pal: dict[str, str], xs: list[float], widths: list[float]) -> str:
    last_tick = xs[-1] + 1
    total = 8.0

    def pct(x: float) -> int:
        return round(HEAD_START + x / last_tick * (HEAD_END - HEAD_START))

    word_pct = [pct(x) for x in xs]
    curve_on = word_pct[1]
    number_on = word_pct[5]
    trans = 70  # slide transition once the playhead has cleared the sentence
    css = [font_face()]
    css.append(f".bg{{fill:{pal['bg']}}}")
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".w{{font:600 32px {SANS};letter-spacing:-.01em;fill:{pal['ink']};animation:{total}s linear infinite}}")
    css.append(f".cap{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}")
    css.append(f".axis{{stroke:{pal['hair']};stroke-width:2}}")
    css.append(
        f".tick{{stroke:{pal['blue']};stroke-width:2.5;stroke-linecap:round;animation:{total}s linear infinite}}"
    )
    css.append(f".dot{{fill:{pal['blue']};animation:{total}s linear infinite}}")
    css.append(f".num{{font:700 60px {SANS};letter-spacing:-.03em;fill:{pal['blue']}}}")
    # playhead: base = parked on the last tick; travels from 0 during the loop, fades out in the dissolve
    css.append(f".head{{transform:translateX({last_tick:.1f}px);animation:head {total}s linear infinite}}")
    css.append(
        f"@keyframes head{{0%,{HEAD_START}%{{transform:translateX(0);opacity:1}}{HEAD_END}%,95%{{transform:translateX({last_tick:.1f}px);opacity:1}}99%{{transform:translateX({last_tick:.1f}px);opacity:0}}100%{{transform:translateX(0);opacity:0}}}}"
    )
    for i, p in enumerate(word_pct):
        on = pal["blue"] if i in CUE_WORDS else pal["ink"]
        css.append(
            f"@keyframes w{i}{{0%,{p - 1}%{{fill:{pal['dim']}}}{p}%,95%{{fill:{on}}}99%,100%{{fill:{pal['dim']}}}}}"
        )
        css.append(
            f"@keyframes t{i}{{0%,{p - 1}%{{stroke:{pal['tick']}}}{p}%,95%{{stroke:{pal['blue']}}}99%,100%{{stroke:{pal['tick']}}}}}"
        )
        css.append(f".w{i}{{animation-name:w{i}}}.t{i}{{animation-name:t{i}}}")
    css.append(f"@keyframes d1{{0%,{curve_on - 1}%{{opacity:0}}{curve_on}%,95%{{opacity:1}}99%,100%{{opacity:0}}}}")
    css.append(f"@keyframes d2{{0%,{number_on - 1}%{{opacity:0}}{number_on}%,95%{{opacity:1}}99%,100%{{opacity:0}}}}")
    css.append(".d1{animation-name:d1}.d2{animation-name:d2}")
    css.append(
        f".curve{{stroke-dasharray:1;stroke-dashoffset:0;animation:draw {total}s cubic-bezier(.3,0,.1,1) infinite}}"
    )
    css.append(
        f"@keyframes draw{{0%,{curve_on}%{{stroke-dashoffset:1}}{curve_on + 16}%,95%{{stroke-dashoffset:0}}99%,100%{{stroke-dashoffset:1}}}}"
    )
    css.append(
        f".pop{{transform-box:fill-box;transform-origin:center;animation:pop {total}s cubic-bezier(.2,.9,.2,1) infinite}}"
    )
    css.append(
        f"@keyframes pop{{0%,{number_on}%{{opacity:0;transform:scale(.7)}}{number_on + 4}%,95%{{opacity:1;transform:scale(1)}}99%,100%{{opacity:0;transform:scale(.7)}}}}"
    )
    # slide transition: A out, then B in, sequenced so nothing bleeds through
    css.append(f".sa{{animation:sa {total}s cubic-bezier(.2,0,0,1) infinite}}")
    css.append(
        f"@keyframes sa{{0%,{trans}%{{opacity:1;transform:translateX(0)}}{trans + 3}%,95%{{opacity:0;transform:translateX(-24px)}}99%,100%{{opacity:1;transform:translateX(0)}}}}"
    )
    css.append(f".sb{{opacity:0;transform:translateX(24px);animation:sb {total}s cubic-bezier(.2,0,0,1) infinite}}")
    css.append(
        f"@keyframes sb{{0%,{trans + 3}%{{opacity:0;transform:translateX(24px)}}{trans + 7}%,95%{{opacity:1;transform:translateX(0)}}99%,100%{{opacity:0;transform:translateX(24px)}}}}"
    )
    css.append(reduced_motion())

    words_svg = "".join(
        f'<tspan class="w w{i}" x="{x:.1f}">{w}</tspan>' for i, (w, x) in enumerate(zip(SENTENCE, xs, strict=True))
    )
    ticks_svg = "".join(
        f'<line class="tick t{i}" x1="{x + 1:.1f}" y1="0" x2="{x + 1:.1f}" y2="16"/>' for i, x in enumerate(xs)
    )
    dots_svg = f'<circle class="dot d1" cx="{xs[1] + 1:.1f}" cy="-6" r="4"/><circle class="dot d2" cx="{xs[5] + 1:.1f}" cy="-6" r="4"/>'
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{HERO_W}" height="{HERO_H}" viewBox="0 0 {HERO_W} {HERO_H}" role="img" aria-labelledby="t d">
  <title id="t">DeckTalk</title>
  <desc id="d">A playhead moves along a spoken sentence, one tick per word. When it reaches "curve" a curve draws on the slide; when it reaches "number" a figure appears; after the last word the slide changes.</desc>
  <defs><style>{chr(10).join(css)}</style><clipPath id="card"><rect width="348" height="164" rx="12"/></clipPath></defs>
  <rect class="bg" width="{HERO_W}" height="{HERO_H}"/>
  <text class="lab" x="72" y="76">NARRATION</text>
  <g transform="translate(72 140)">
    <text y="0">{words_svg}</text>
    <g transform="translate(0 30)">{ticks_svg}{dots_svg}<g class="head"><rect x="0" y="-14" width="2" height="38" fill="{pal["blue"]}"/></g></g>
    <text class="cap" x="0" y="82">A timestamp for every word. A cue for every reveal.</text>
  </g>
  <text class="lab" x="780" y="76">SLIDE</text>
  <g transform="translate(780 92)">
    <rect class="block" width="348" height="164" rx="12"/>
    <g clip-path="url(#card)">
      <g class="sa">
        <line class="axis" x1="30" y1="126" x2="200" y2="126"/><line class="axis" x1="30" y1="38" x2="30" y2="126"/>
        <path class="curve" pathLength="1" d="M30,120 C70,118 110,98 150,64 S190,36 200,32" fill="none" stroke="{pal["blue"]}" stroke-width="4" stroke-linecap="round"/>
        <text class="num pop" x="228" y="106">3×</text>
      </g>
      <g class="sb">
        <rect x="30" y="40" width="150" height="12" rx="6" fill="{pal["blue"]}"/>
        <rect x="30" y="72" width="260" height="9" rx="4.5" fill="{pal["bar"]}"/>
        <rect x="30" y="94" width="220" height="9" rx="4.5" fill="{pal["bar"]}"/>
        <rect x="30" y="116" width="240" height="9" rx="4.5" fill="{pal["bar"]}"/>
      </g>
    </g>
  </g>
</svg>
"""


# ---- how it works ----------------------------------------------------------------------------

STAGES = [
    ("01 WRITE", "A script in markdown", "One heading per scene."),
    ("02 NARRATE", "Your voice reads it", "ElevenLabs returns a time for every word."),
    ("03 RECORD", "Slides reveal on the words", "Plain HTML, recorded in Chromium."),
    ("04 ASSEMBLE", "Cut to the frame", "ffmpeg cuts, mixes, verifies."),
]
TICK_XS = [2, 30, 74, 96, 142, 178, 222]


def hiw_css(pal: dict[str, str], total: float = 10.0) -> str:
    css = [font_face()]
    css.append(f".bg{{fill:{pal['bg']}}}")
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".h{{font:600 20px {SANS};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".s{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}.bar{{fill:{pal['bar']}}}.blue{{fill:{pal['blue']}}}")
    css.append(f".rail{{stroke:{pal['hair']};stroke-width:2}}")
    css.append(f".node{{fill:{pal['blue']};animation:{total}s linear infinite}}")
    # the pulse: base = parked at the last node
    css.append(f".pulse{{transform:translateX(840px);animation:pulse {total}s linear infinite}}")
    css.append(
        "@keyframes pulse{0%{transform:translateX(0);opacity:1}80%,95%{transform:translateX(840px);opacity:1}99%{transform:translateX(840px);opacity:0}100%{transform:translateX(0);opacity:0}}"
    )
    for i, at in enumerate((0, 26, 52, 78)):
        css.append(
            f"@keyframes n{i}{{0%,{max(at - 1, 0)}%{{fill:{pal['ink']}}}{at}%,95%{{fill:{pal['blue']}}}99%,100%{{fill:{pal['ink']}}}}}.n{i}{{animation-name:n{i}}}"
        )
    # 01: lines type in
    css.append(f".type{{transform-box:fill-box;transform-origin:left;animation:{total}s linear infinite}}")
    for i, (a, b) in enumerate(((0, 3), (4, 8), (9, 13), (14, 18)), start=1):
        css.append(
            f"@keyframes ty{i}{{0%,{a}%{{transform:scaleX(0)}}{b}%,95%{{transform:scaleX(1)}}99%,100%{{transform:scaleX(0)}}}}.ty{i}{{animation-name:ty{i}}}"
        )
    # 02: uniform ticks light under a sweeping head, two cue dots
    css.append(f".tk{{stroke:{pal['blue']};stroke-width:2.5;stroke-linecap:round;animation:{total}s linear infinite}}")
    css.append(
        f".head2{{transform:translateX({TICK_XS[-1] - 1}px);opacity:0;animation:head2 {total}s linear infinite}}"
    )
    css.append(
        f"@keyframes head2{{0%,26%{{transform:translateX(0);opacity:1}}48%{{transform:translateX({TICK_XS[-1] - 1}px);opacity:1}}50%,100%{{transform:translateX({TICK_XS[-1] - 1}px);opacity:0}}}}"
    )
    for i, x in enumerate(TICK_XS):
        at = round(26 + x / (TICK_XS[-1]) * 22)
        css.append(
            f"@keyframes k{i}{{0%,{at - 1}%{{stroke:{pal['tick']}}}{at}%,95%{{stroke:{pal['blue']}}}99%,100%{{stroke:{pal['tick']}}}}}.k{i}{{animation-name:k{i}}}"
        )
    css.append(f".cd{{fill:{pal['blue']};animation:{total}s linear infinite}}")
    css.append(
        "@keyframes cd1{0%,32%{opacity:0}33%,95%{opacity:1}99%,100%{opacity:0}}@keyframes cd2{0%,43%{opacity:0}44%,95%{opacity:1}99%,100%{opacity:0}}.cd1{animation-name:cd1}.cd2{animation-name:cd2}"
    )
    # 03: mini curve draws, figure pops
    css.append(f".mc{{stroke-dasharray:1;stroke-dashoffset:0;animation:mc {total}s cubic-bezier(.3,0,.1,1) infinite}}")
    css.append("@keyframes mc{0%,54%{stroke-dashoffset:1}62%,95%{stroke-dashoffset:0}99%,100%{stroke-dashoffset:1}}")
    css.append(
        f".mp{{transform-box:fill-box;transform-origin:center;animation:mp {total}s cubic-bezier(.2,.9,.2,1) infinite}}"
    )
    css.append(
        "@keyframes mp{0%,64%{opacity:0;transform:scale(.7)}67%,95%{opacity:1;transform:scale(1)}99%,100%{opacity:0;transform:scale(.7)}}"
    )
    css.append(f".mnum{{font:700 26px {SANS};letter-spacing:-.03em;fill:{pal['blue']}}}")
    # 04: frames slide in, then merge into one block; label appears
    css.append(f".fr{{transform-box:fill-box;animation:{total}s cubic-bezier(.2,0,0,1) infinite}}")
    for i, (a, b, dx) in enumerate(((78, 81, 0), (81, 84, -58), (84, 87, -116), (87, 90, -174)), start=1):
        css.append(
            f"@keyframes f{i}{{0%,{a}%{{opacity:0;transform:translateX(-10px)}}{b}%,90%{{opacity:1;transform:translateX(0)}}92%,95%{{opacity:1;transform:translateX({dx}px)}}99%,100%{{opacity:0;transform:translateX(-10px)}}}}.f{i}{{animation-name:f{i}}}"
        )
    css.append(f".out{{animation:out {total}s linear infinite}}")
    css.append("@keyframes out{0%,91%{opacity:0}92%,96%{opacity:1}99%,100%{opacity:0}}")
    css.append(reduced_motion())
    return "\n".join(css)


def stage_svg(i: int, pal: dict[str, str], x: int, y: int, node: bool) -> str:
    lab, h, s = STAGES[i]
    dot = f'<circle class="node n{i}" cx="0" cy="40" r="3"/>' if node else ""
    if i == 0:
        art = """<g transform="translate(0 100)">
      <rect class="blue type ty1" x="0" y="0" width="64" height="8" rx="4"/>
      <rect class="bar type ty2" x="0" y="20" width="196" height="8" rx="4"/>
      <rect class="bar type ty3" x="0" y="40" width="168" height="8" rx="4"/>
      <rect class="bar type ty4" x="0" y="60" width="184" height="8" rx="4"/></g>"""
    elif i == 1:
        ticks = "".join(f'<line class="tk k{j}" x1="{tx}" y1="46" x2="{tx}" y2="62"/>' for j, tx in enumerate(TICK_XS))
        art = f"""<g transform="translate(0 100)">{ticks}
      <circle class="cd cd1" cx="{TICK_XS[2]}" cy="38" r="4"/><circle class="cd cd2" cx="{TICK_XS[5]}" cy="38" r="4"/>
      <g class="head2"><rect x="1" y="30" width="2" height="38" class="blue"/></g></g>"""
    elif i == 2:
        art = f"""<g transform="translate(0 98)">
      <rect class="block" width="228" height="78" rx="8"/>
      <path class="mc" pathLength="1" d="M18,60 C48,58 74,44 100,28 S128,16 138,14" fill="none" stroke="{pal["blue"]}" stroke-width="3" stroke-linecap="round"/>
      <text class="mnum mp" x="160" y="50">3×</text></g>"""
    else:
        art = """<g transform="translate(0 100)">
      <rect class="block fr f1" x="0" y="0" width="50" height="36" rx="8"/>
      <rect class="block fr f2" x="58" y="0" width="50" height="36" rx="8"/>
      <rect class="block fr f3" x="116" y="0" width="50" height="36" rx="8"/>
      <rect class="block fr f4" x="174" y="0" width="50" height="36" rx="8"/>
      <g class="out"><rect class="blue" x="0" y="0" width="224" height="36" rx="8"/><text class="lab" x="0" y="66">OUT.MP4</text></g></g>"""
    return f"""  <g transform="translate({x} {y})">{dot}
    <text class="lab" x="0" y="80">{lab}</text>
    {art}
    <text class="h" x="0" y="212">{h}</text>
    <text class="s" x="0" y="236">{s}</text>
  </g>"""


def how_it_works(pal: dict[str, str], stacked: bool) -> str:
    css = hiw_css(pal)
    title = "How DeckTalk works: you write a script; your voice reads it and every word gets a timestamp; slides reveal on the words in a browser; ffmpeg cuts one mp4."
    if not stacked:
        w, h = 1200, 280
        rail = f'<line class="rail" x1="60" y1="40" x2="900" y2="40"/><g class="pulse"><circle cx="60" cy="40" r="5" fill="{pal["blue"]}"/></g>'
        stages = "\n".join(stage_svg(i, pal, 60 + 280 * i, 0, node=True) for i in range(4))
    else:
        w, h = 600, 560
        rail = ""
        stages = "\n".join(stage_svg(i, pal, 40 + 280 * (i % 2), 280 * (i // 2), node=False) for i in range(4))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t">
  <title id="t">{title}</title>
  <defs><style>{css}</style></defs>
  <rect class="bg" width="{w}" height="{h}"/>
{(f"  {rail}" + chr(10)) if rail else ""}{stages}
</svg>
"""


# ---- mark -------------------------------------------------------------------------------------


def mark(pal: dict[str, str], size: int = 24, background: bool = False) -> str:
    """Three ticks, the middle one lit with a cue dot: the hero's cue glyph."""
    s = size / 24
    bg = f'<rect width="{size}" height="{size}" rx="{5 * s:.1f}" fill="{pal["bg"]}"/>' if background else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" role="img" aria-label="DeckTalk">
  {bg}<g stroke-width="2.5" stroke-linecap="round">
    <line x1="4" y1="9" x2="4" y2="20" stroke="{pal["bar"]}"/>
    <line x1="12" y1="9" x2="12" y2="20" stroke="{pal["blue"]}"/>
    <line x1="20" y1="9" x2="20" y2="20" stroke="{pal["bar"]}"/>
  </g><circle cx="12" cy="4.5" r="2.5" fill="{pal["blue"]}"/>
</svg>
"""


# ---- alignment ---------------------------------------------------------------------------------

MAGENTA = "#ff00ff"
FRAME_W, FRAME_H, FRAME_GAP = 88, 50, 8
COVER_FRAMES = 4  # frames that are still magenta before the clock starts


def alignment(pal: dict[str, str], xs: list[float]) -> str:
    """Why the cuts are exact: the recording opens on a magenta cover, the first clean frame is
    narration t=0, and each cue is a spoken word measured from that same origin."""
    w, h = 1200, 250
    left = 72
    n_frames = 11
    strip_y = 58
    t0_x = left + COVER_FRAMES * (FRAME_W + FRAME_GAP)
    words_y = 168
    tick_y = 178
    curve_x = t0_x + xs[1] + 1  # the tick under "curve"
    number_x = t0_x + xs[5] + 1  # the tick under "number"
    css = [font_face()]
    css.append(f".bg{{fill:{pal['bg']}}}")
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".w{{font:600 26px {SANS};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".cue{{fill:{pal['blue']}}}.dim{{fill:{pal['dim']}}}")
    css.append(f".cap{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".frame{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}")
    css.append(f".cover{{fill:{MAGENTA};opacity:.9}}")
    css.append(f".tick{{stroke:{pal['tick']};stroke-width:2.5;stroke-linecap:round}}.tick.on{{stroke:{pal['blue']}}}")
    css.append(f".t0{{stroke:{pal['blue']};stroke-width:2}}")
    css.append(f".lead{{stroke:{pal['blue']};stroke-width:1.5;stroke-dasharray:3 4}}")
    css.append(f".axis{{stroke:{pal['hair']};stroke-width:1.5}}")
    css.append(f".num{{font:700 16px {SANS};letter-spacing:-.03em;fill:{pal['blue']}}}")
    css.append(f".brace{{stroke:{pal['mute']};stroke-width:1.5;fill:none}}")

    frames = []
    for i in range(n_frames):
        x = left + i * (FRAME_W + FRAME_GAP)
        if i < COVER_FRAMES:
            frames.append(f'<rect class="cover" x="{x}" y="{strip_y}" width="{FRAME_W}" height="{FRAME_H}" rx="6"/>')
            continue
        # a miniature of the hero slide: a frame shows what had happened by its midpoint. The curve
        # draws over three frames after the word "curve", and the number appears after "number".
        mid = x + FRAME_W / 2
        progress = min(1.0, max(0.0, (mid - curve_x) / (3 * (FRAME_W + FRAME_GAP))))
        art = [f'<rect class="frame" x="{x}" y="{strip_y}" width="{FRAME_W}" height="{FRAME_H}" rx="6"/>']
        art.append(f'<line class="axis" x1="{x + 10}" y1="{strip_y + 40}" x2="{x + 52}" y2="{strip_y + 40}"/>')
        art.append(f'<line class="axis" x1="{x + 10}" y1="{strip_y + 12}" x2="{x + 10}" y2="{strip_y + 40}"/>')
        if progress > 0:
            art.append(
                f'<path pathLength="1" stroke-dasharray="1" stroke-dashoffset="{1 - progress:.2f}" '
                f'd="M{x + 10},{strip_y + 38} C{x + 22},{strip_y + 37} {x + 34},{strip_y + 28} {x + 44},{strip_y + 18} '
                f'S{x + 50},{strip_y + 12} {x + 52},{strip_y + 11}" fill="none" stroke="{pal["blue"]}" '
                'stroke-width="2.5" stroke-linecap="round"/>'
            )
        if mid > number_x:
            art.append(f'<text class="num" x="{x + 58}" y="{strip_y + 32}">3×</text>')
        frames.append("".join(art))
    cover_mid = left + (COVER_FRAMES * (FRAME_W + FRAME_GAP) - FRAME_GAP) / 2
    words_svg = "".join(
        f'<tspan class="w {"cue" if i in CUE_WORDS else ""}" x="{t0_x + x:.1f}">{w}</tspan>'
        for i, (w, x) in enumerate(zip(SENTENCE, xs, strict=True))
    )
    ticks_svg = "".join(
        f'<line class="tick {"on" if i in CUE_WORDS else ""}" x1="{t0_x + x + 1:.1f}" y1="{tick_y}" x2="{t0_x + x + 1:.1f}" y2="{tick_y + 14}"/>'
        for i, x in enumerate(xs)
    )
    leads = "".join(
        f'<line class="lead" x1="{cx:.1f}" y1="{strip_y + FRAME_H + 4}" x2="{cx:.1f}" y2="{tick_y - 4}"/>'
        for cx in (curve_x, number_x)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">Why the cuts are exact</title>
  <desc id="d">A strip of recorded frames opens magenta while the page is covered. The first clean frame is narration t=0. The spoken words start at the same point, and the frames in which the curve draws and the number appears line up with the words "curve" and "number".</desc>
  <defs><style>{chr(10).join(css)}</style></defs>
  <rect class="bg" width="{w}" height="{h}"/>
  <text class="lab" x="{left}" y="40">RECORDING</text>
  {"".join(frames)}
  <path class="brace" d="M{left},{strip_y + FRAME_H + 8} v5 H{left + COVER_FRAMES * (FRAME_W + FRAME_GAP) - FRAME_GAP} v-5"/>
  <text class="cap" x="{cover_mid:.1f}" y="{strip_y + FRAME_H + 30}" text-anchor="middle">covered until the clock starts</text>
  <line class="t0" x1="{t0_x - FRAME_GAP / 2}" y1="{strip_y - 12}" x2="{t0_x - FRAME_GAP / 2}" y2="{tick_y + 18}"/>
  <text class="lab" x="{t0_x + 2}" y="{strip_y - 16}" fill="{pal["blue"]}" style="fill:{pal["blue"]}">T = 0</text>
  {leads}
  <text class="lab" x="{t0_x + 2}" y="{words_y - 30}">NARRATION</text>
  <text y="{words_y}">{words_svg}</text>
  {ticks_svg}
  <text class="cap" x="{left}" y="{h - 18}">The first clean frame is t=0, found in the frames rather than on a timer. Every cue is a word, measured from the same origin.</text>
</svg>
"""


# ---- wordmark ---------------------------------------------------------------------------------


def wordmark(pal: dict[str, str]) -> str:
    """The mark and the name, for the docs navbar."""
    css = font_face() + f".n{{font:600 22px {SANS};letter-spacing:-.02em;fill:{pal['ink']}}}"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="132" height="32" viewBox="0 0 132 32" role="img" aria-label="DeckTalk">
  <defs><style>{css}</style></defs>
  <g transform="translate(0 4)" stroke-width="2.5" stroke-linecap="round">
    <line x1="4" y1="9" x2="4" y2="20" stroke="{pal["bar"]}"/>
    <line x1="12" y1="9" x2="12" y2="20" stroke="{pal["blue"]}"/>
    <line x1="20" y1="9" x2="20" y2="20" stroke="{pal["bar"]}"/>
    <circle cx="12" cy="4.5" r="2.5" fill="{pal["blue"]}" stroke="none"/>
  </g>
  <text class="n" x="34" y="24">DeckTalk</text>
</svg>
"""


# ---- entry ------------------------------------------------------------------------------------


def build() -> dict[Path, str]:
    widths, space = measure_words(SENTENCE, f"600 32px {SANS}", "-.01em")
    xs: list[float] = []
    x = 0.0
    for i, w in enumerate(widths):
        xs.append(round(x, 1))
        gap = space * (1.6 if SENTENCE[i].endswith((",", ".")) else 1.0)
        x += w + gap
    out: dict[Path, str] = {}
    docs = ROOT / "docs"
    for name, pal in (("light", LIGHT), ("dark", DARK)):
        out[ASSETS / f"hero-{name}.svg"] = hero(pal, xs, widths)
        out[ASSETS / f"how-it-works-{name}.svg"] = how_it_works(pal, stacked=False)
        out[ASSETS / f"how-it-works-{name}-stacked.svg"] = how_it_works(pal, stacked=True)
        out[ASSETS / f"alignment-{name}.svg"] = alignment(pal, [x * 26 / 32 for x in xs])
        out[ASSETS / f"mark-{name}.svg"] = mark(pal)
        out[docs / "logo" / f"{name}.svg"] = wordmark(pal)
        for key in ("hero", "how-it-works", "alignment"):
            out[docs / "images" / f"{key}-{name}.svg"] = out[ASSETS / f"{key}-{name}.svg"]
    out[docs / "favicon.svg"] = mark(LIGHT, size=32, background=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if any generated file would change")
    args = ap.parse_args()
    files = build()
    changed = [p for p, s in files.items() if not p.exists() or p.read_text() != s]
    if args.check:
        for p in changed:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if changed else 0
    for p, s in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(s)
        print(f"wrote {p.relative_to(ROOT)}  ({len(s) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
