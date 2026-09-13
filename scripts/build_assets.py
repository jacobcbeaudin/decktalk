# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.50", "fonttools[woff]>=4.50"]
# ///
"""Generate every graphic from one source: hero, how-it-works (wide and stacked), alignment,
mark, wordmark, favicon. The README reads assets/, the docs site reads docs/images/ and docs/logo/.

    uv run scripts/build_assets.py            # writes assets/*.svg, docs/images/*.svg, docs/logo/*.svg, docs/favicon.svg
    uv run scripts/build_assets.py --check    # exit 1 if the committed files would change

Every variant (light/dark, wide/stacked) comes from the same builders and one palette map, so
they cannot drift. The copies in assets/ have a transparent background so they sit on whatever
ground GitHub and PyPI paint; the copies in docs/images/ carry their own background rect. Inter
Tight and JetBrains Mono subsets (OFL, assets/fonts/) are embedded as base64 in the diagrams so
GitHub and PyPI render the intended faces; word positions in the hero are measured in Chromium
with that exact font, so the tick under each word is under the word. The wordmark instead carries
the letters as outline paths traced with fontTools, so each logo is a few kilobytes.

Motion rules (from the design review): base styles are the END state, keyframes carry the start
values, so `prefers-reduced-motion: reduce` shows the finished frame. Loops dissolve back to the
start over the last few percent instead of snapping.
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
    "accent": "#2c1fea",
    "on_accent": "#ffffff",
    "mark_bar": "#d4d4d8",  # the unlit ticks of the mark
    "cover": "#fad3f3",  # the alignment diagram's cover frames
    "cover_edge": "#fd62f9",
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
    "accent": "#7c8cff",  # electric hue kept, 6.5:1 on the dark ground
    "on_accent": "#0e0e0f",
    "mark_bar": "#4f4f57",
    "cover": "#33083a",
    "cover_edge": "#a20da8",
}

SANS = "'DT Sans', 'Inter Tight', 'Inter', -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'DT Mono', 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
SENTENCE = ["The", "curve", "rises,", "then", "the", "number", "lands."]
CUE_WORDS = {1, 5, 6}  # curve, number, lands.
MEASURE_PX = 32  # the size the words are measured at; every diagram scales the positions from it
HERO_W, HERO_H, HERO_PAD = 1000, 248, 48
HERO_PX = 30  # the sentence's size in the hero, so it clears the card at this width
HEAD_START, HEAD_END = 5, 64  # % of the loop the playhead travels
CARD_W, CARD_H, CARD_R = 324, 152, 12  # the hero's slide card
SLIDE_W, SLIDE_H = 348, 164  # the slide artwork's own coordinate space
FACES = (
    # family, file, weight range: variable fonts subset to the glyphs the diagrams use
    ("DT Sans", "InterTight.woff2", "100 900"),
    ("DT Mono", "JetBrainsMono.woff2", "100 800"),
)


def font_face() -> str:
    rules = []
    for family, name, weights in FACES:
        path = FONTS / name
        if not path.exists():
            sys.exit(f"missing {path}; see assets/fonts/LICENSE.txt for how it was made")
        b64 = base64.b64encode(path.read_bytes()).decode()
        rules.append(
            f"@font-face{{font-family:'{family}';font-weight:{weights};font-style:normal;"
            f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}"
        )
    return "".join(rules)


def reduced_motion() -> str:
    return "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


def bg_rect(pal: dict[str, str], w: int, h: int, background: bool) -> str:
    """The ground behind a diagram, or nothing when the host page paints its own."""
    return f'<rect width="{w}" height="{h}" fill="{pal["bg"]}"/>' if background else ""


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


def glyph_outlines(text: str, size: float, weight: int, tracking: float) -> tuple[str, float]:
    """The text as one SVG path in a y-down px space with the baseline at y=0, plus its width.

    The variable font is instanced at the requested weight and each glyph is placed by its own
    advance width, with `tracking` (in em) added between letters.
    """
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = instancer.instantiateVariableFont(TTFont(FONTS / "InterTight.woff2"), {"wght": weight})
    scale = size / font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    x = 0.0
    parts = []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, 0)))
        parts.append(pen.getCommands())
        x += font["hmtx"][name][0] * scale + tracking * size
    return "".join(parts), x - tracking * size


# ---- hero --------------------------------------------------------------------------------


def hero(pal: dict[str, str], xs: list[float], background: bool) -> str:
    last_tick = xs[-1] + 1
    total = 8.0

    def pct(x: float) -> int:
        return round(HEAD_START + x / last_tick * (HEAD_END - HEAD_START))

    word_pct = [pct(x) for x in xs]
    curve_on = word_pct[1]
    number_on = word_pct[5]
    trans = word_pct[6]  # the slide changes on the last word, like every other reveal
    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(
        f".w{{font:600 {HERO_PX}px {SANS};letter-spacing:-.01em;fill:{pal['ink']};animation:{total}s linear infinite}}"
    )
    css.append(f".cap{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}")
    css.append(f".axis{{stroke:{pal['hair']};stroke-width:2}}")
    css.append(
        f".tick{{stroke:{pal['accent']};stroke-width:2.5;stroke-linecap:round;animation:{total}s linear infinite}}"
    )
    css.append(f".dot{{fill:{pal['accent']};animation:{total}s linear infinite}}")
    css.append(f".num{{font:700 60px {SANS};letter-spacing:-.03em;fill:{pal['accent']}}}")
    # The loop dissolves: everything fades out over the last 7 % and back in over the first 4 %.
    css.append(f".loop{{animation:loop {total}s linear infinite}}")
    css.append("@keyframes loop{0%{opacity:0}4%,93%{opacity:1}100%{opacity:0}}")
    # The playhead is parked on the last tick at rest and travels from 0 during the loop.
    css.append(f".head{{transform:translateX({last_tick:.1f}px);animation:head {total}s linear infinite}}")
    css.append(
        f"@keyframes head{{0%,{HEAD_START}%{{transform:translateX(0)}}{HEAD_END}%,100%{{transform:translateX({last_tick:.1f}px)}}}}"
    )
    for i, p in enumerate(word_pct):
        on = pal["accent"] if i in CUE_WORDS else pal["ink"]
        css.append(f"@keyframes w{i}{{0%,{p - 1}%{{fill:{pal['dim']}}}{p}%,100%{{fill:{on}}}}}")
        css.append(f"@keyframes t{i}{{0%,{p - 1}%{{stroke:{pal['tick']}}}{p}%,100%{{stroke:{pal['accent']}}}}}")
        css.append(f".w{i}{{animation-name:w{i}}}.t{i}{{animation-name:t{i}}}")
    for n, at in ((1, curve_on), (2, number_on), (3, trans)):
        css.append(f"@keyframes d{n}{{0%,{at - 1}%{{opacity:0}}{at}%,100%{{opacity:1}}}}.d{n}{{animation-name:d{n}}}")
    # A dash of 1 with a gap of 2 (on a pathLength of 1) keeps the next dash's round cap off the
    # end of the path before the draw starts, so no dot appears ahead of the curve.
    css.append(
        f".curve{{stroke-dasharray:1 2;stroke-dashoffset:0;animation:draw {total}s cubic-bezier(.3,0,.1,1) infinite}}"
    )
    css.append(f"@keyframes draw{{0%,{curve_on}%{{stroke-dashoffset:1}}{curve_on + 16}%,100%{{stroke-dashoffset:0}}}}")
    css.append(
        f".pop{{transform-box:fill-box;transform-origin:center;animation:pop {total}s cubic-bezier(.2,.9,.2,1) infinite}}"
    )
    css.append(
        f"@keyframes pop{{0%,{number_on}%{{opacity:0;transform:scale(.7)}}{number_on + 4}%,100%{{opacity:1;transform:scale(1)}}}}"
    )
    # The slide transition: A out, then B in, sequenced so nothing bleeds through.
    css.append(f".sa{{animation:sa {total}s cubic-bezier(.2,0,0,1) infinite}}")
    css.append(
        f"@keyframes sa{{0%,{trans}%{{opacity:1;transform:translateX(0)}}{trans + 3}%,100%{{opacity:0;transform:translateX(-24px)}}}}"
    )
    css.append(f".sb{{opacity:0;transform:translateX(24px);animation:sb {total}s cubic-bezier(.2,0,0,1) infinite}}")
    css.append(
        f"@keyframes sb{{0%,{trans + 3}%{{opacity:0;transform:translateX(24px)}}{trans + 7}%,100%{{opacity:1;transform:translateX(0)}}}}"
    )
    css.append(reduced_motion())

    words_svg = "".join(
        f'<tspan class="w w{i}" x="{x:.1f}">{w}</tspan>' for i, (w, x) in enumerate(zip(SENTENCE, xs, strict=True))
    )
    ticks_svg = "".join(
        f'<line class="tick t{i}" x1="{x + 1:.1f}" y1="0" x2="{x + 1:.1f}" y2="16"/>' for i, x in enumerate(xs)
    )
    dots_svg = "".join(
        f'<circle class="dot d{n}" cx="{xs[i] + 1:.1f}" cy="-6" r="4"/>'
        for n, i in enumerate(sorted(CUE_WORDS), start=1)
    )
    # The card spans the label row to the caption baseline; the narration column is laid out to
    # the same extent, with the words and ticks centred between the label and the caption.
    card_x, card_y = HERO_W - HERO_PAD - CARD_W, HERO_PAD
    caption_y = card_y + CARD_H
    words_y = 110
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{HERO_W}" height="{HERO_H}" viewBox="0 0 {HERO_W} {HERO_H}" role="img" aria-labelledby="t d">
  <title id="t">DeckTalk</title>
  <desc id="d">A playhead moves along a spoken sentence, one tick per word. When it reaches "curve" a curve draws on the slide; when it reaches "number" a figure appears; on the last word the slide changes.</desc>
  <defs><style>{chr(10).join(css)}</style><clipPath id="card"><rect width="{CARD_W}" height="{CARD_H}" rx="{CARD_R}"/></clipPath></defs>
  {bg_rect(pal, HERO_W, HERO_H, background)}
  <g class="loop">
  <text class="lab" x="{HERO_PAD}" y="{card_y}">NARRATION</text>
  <g transform="translate({HERO_PAD} {words_y})">
    <text y="0">{words_svg}</text>
    <g transform="translate(0 30)">{ticks_svg}{dots_svg}<g class="head"><rect x="0" y="-14" width="2" height="38" fill="{pal["ink"]}"/></g></g>
    <text class="cap" x="0" y="{caption_y - words_y}">Every word has a timestamp, and every reveal has a cue.</text>
  </g>
  <g transform="translate({card_x} {card_y})">
    <rect class="block" width="{CARD_W}" height="{CARD_H}" rx="{CARD_R}"/>
    <g clip-path="url(#card)"><g transform="scale({CARD_W / SLIDE_W:.4f})">
      <g class="sa">
        <line class="axis" x1="30" y1="126" x2="200" y2="126"/><line class="axis" x1="30" y1="38" x2="30" y2="126"/>
        <path class="curve" pathLength="1" d="M30,120 C70,118 110,98 150,64 S190,36 200,32" fill="none" stroke="{pal["accent"]}" stroke-width="4" stroke-linecap="round"/>
        <text class="num pop" x="228" y="106">3×</text>
      </g>
      <g class="sb">
        <rect x="30" y="40" width="150" height="12" rx="6" fill="{pal["accent"]}"/>
        <rect x="30" y="72" width="260" height="9" rx="4.5" fill="{pal["bar"]}"/>
        <rect x="30" y="94" width="220" height="9" rx="4.5" fill="{pal["bar"]}"/>
        <rect x="30" y="116" width="240" height="9" rx="4.5" fill="{pal["bar"]}"/>
      </g>
    </g></g>
  </g>
  </g>
</svg>
"""


# ---- how it works ----------------------------------------------------------------------------

STAGES = [
    ("01 WRITE", "A script in markdown", "One heading starts one section."),
    ("02 NARRATE", "Your voice reads it", "ElevenLabs returns a time for every word."),
    ("03 RECORD", "Slides reveal on the words", "Plain HTML, recorded in Chromium."),
    ("04 ASSEMBLE", "Cut to the frame", "ffmpeg cuts, mixes, verifies."),
]
TICK_XS = [2, 30, 74, 96, 142, 178, 222]


def hiw_css(pal: dict[str, str], total: float = 10.0) -> str:
    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".h{{font:600 20px {SANS};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".s{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}.bar{{fill:{pal['bar']}}}.accent{{fill:{pal['accent']}}}")
    # Each stage's label lights as its stage begins and stays lit; nothing else marks the stage.
    for i, at in enumerate((0, 26, 52, 78)):
        css.append(
            f"@keyframes n{i}{{0%,{max(at - 1, 0)}%{{fill:{pal['mute']}}}{at}%,95%{{fill:{pal['accent']}}}99%,100%{{fill:{pal['mute']}}}}}.n{i}{{animation:n{i} {total}s linear infinite}}"
        )
    # 01: lines type in.
    css.append(f".type{{transform-box:fill-box;transform-origin:left;animation:{total}s linear infinite}}")
    for i, (a, b) in enumerate(((0, 3), (4, 8), (9, 13), (14, 18)), start=1):
        css.append(
            f"@keyframes ty{i}{{0%,{a}%{{transform:scaleX(0)}}{b}%,95%{{transform:scaleX(1)}}99%,100%{{transform:scaleX(0)}}}}.ty{i}{{animation-name:ty{i}}}"
        )
    # 02: uniform ticks light under a sweeping head, two cue dots, and the first cue's timestamp.
    css.append(
        f".tk{{stroke:{pal['accent']};stroke-width:2.5;stroke-linecap:round;animation:{total}s linear infinite}}"
    )
    css.append(
        f".head2{{transform:translateX({TICK_XS[-1] - 1}px);opacity:0;animation:head2 {total}s linear infinite}}"
    )
    css.append(
        f"@keyframes head2{{0%,26%{{transform:translateX(0);opacity:1}}48%{{transform:translateX({TICK_XS[-1] - 1}px);opacity:1}}50%,100%{{transform:translateX({TICK_XS[-1] - 1}px);opacity:0}}}}"
    )
    for i, x in enumerate(TICK_XS):
        at = round(26 + x / (TICK_XS[-1]) * 22)
        css.append(
            f"@keyframes k{i}{{0%,{at - 1}%{{stroke:{pal['tick']}}}{at}%,95%{{stroke:{pal['accent']}}}99%,100%{{stroke:{pal['tick']}}}}}.k{i}{{animation-name:k{i}}}"
        )
    css.append(f".cd{{fill:{pal['accent']};animation:{total}s linear infinite}}")
    css.append(f".ts{{fill:{pal['accent']};animation:{total}s linear infinite}}")
    css.append(
        "@keyframes cd1{0%,32%{opacity:0}33%,95%{opacity:1}99%,100%{opacity:0}}@keyframes cd2{0%,43%{opacity:0}44%,95%{opacity:1}99%,100%{opacity:0}}.cd1{animation-name:cd1}.cd2{animation-name:cd2}"
    )
    # 03: the mini curve draws and the figure pops. The 1 2 dasharray keeps the next dash's round
    # cap off the end of the path before the draw starts.
    css.append(
        f".mc{{stroke-dasharray:1 2;stroke-dashoffset:0;animation:mc {total}s cubic-bezier(.3,0,.1,1) infinite}}"
    )
    css.append("@keyframes mc{0%,54%{stroke-dashoffset:1}62%,95%{stroke-dashoffset:0}99%,100%{stroke-dashoffset:1}}")
    css.append(
        f".mp{{transform-box:fill-box;transform-origin:center;animation:mp {total}s cubic-bezier(.2,.9,.2,1) infinite}}"
    )
    css.append(
        "@keyframes mp{0%,64%{opacity:0;transform:scale(.7)}67%,95%{opacity:1;transform:scale(1)}99%,100%{opacity:0;transform:scale(.7)}}"
    )
    css.append(f".mnum{{font:700 26px {SANS};letter-spacing:-.03em;fill:{pal['accent']}}}")
    # 04: frames slide in, then merge into one bar that carries the output's name to the loop's end.
    css.append(f".fr{{transform-box:fill-box;animation:{total}s cubic-bezier(.2,0,0,1) infinite}}")
    for i, (a, b, dx) in enumerate(((78, 81, 0), (81, 84, -58), (84, 87, -116), (87, 90, -174)), start=1):
        css.append(
            f"@keyframes f{i}{{0%,{a}%{{opacity:0;transform:translateX(-10px)}}{b}%,90%{{opacity:1;transform:translateX(0)}}92%,95%{{opacity:1;transform:translateX({dx}px)}}99%,100%{{opacity:0;transform:translateX(-10px)}}}}.f{i}{{animation-name:f{i}}}"
        )
    css.append(f".out{{animation:out {total}s linear infinite}}")
    css.append("@keyframes out{0%,90%{opacity:0}92%,100%{opacity:1}}")
    css.append(f".on-accent{{fill:{pal['on_accent']}}}")
    css.append(reduced_motion())
    return "\n".join(css)


def stage_svg(i: int, pal: dict[str, str], x: int, y: int) -> str:
    lab, h, s = STAGES[i]
    if i == 0:
        art = """<g transform="translate(0 100)">
      <rect class="accent type ty1" x="0" y="0" width="64" height="8" rx="4"/>
      <rect class="bar type ty2" x="0" y="20" width="196" height="8" rx="4"/>
      <rect class="bar type ty3" x="0" y="40" width="168" height="8" rx="4"/>
      <rect class="bar type ty4" x="0" y="60" width="184" height="8" rx="4"/></g>"""
    elif i == 1:
        ticks = "".join(f'<line class="tk k{j}" x1="{tx}" y1="46" x2="{tx}" y2="62"/>' for j, tx in enumerate(TICK_XS))
        art = f"""<g transform="translate(0 100)">{ticks}
      <circle class="cd cd1" cx="{TICK_XS[2]}" cy="38" r="4"/><circle class="cd cd2" cx="{TICK_XS[5]}" cy="38" r="4"/>
      <text class="lab ts cd1" x="{TICK_XS[2] + 8}" y="38">0.82</text>
      <g class="head2"><rect x="1" y="30" width="2" height="38" class="accent"/></g></g>"""
    elif i == 2:
        art = f"""<g transform="translate(0 98)">
      <rect class="block" width="228" height="78" rx="8"/>
      <path class="mc" pathLength="1" d="M18,60 C48,58 74,44 100,28 S128,16 138,14" fill="none" stroke="{pal["accent"]}" stroke-width="3" stroke-linecap="round"/>
      <text class="mnum mp" x="160" y="50">3×</text></g>"""
    else:
        art = """<g transform="translate(0 100)">
      <rect class="block fr f1" x="0" y="0" width="50" height="36" rx="8"/>
      <rect class="block fr f2" x="58" y="0" width="50" height="36" rx="8"/>
      <rect class="block fr f3" x="116" y="0" width="50" height="36" rx="8"/>
      <rect class="block fr f4" x="174" y="0" width="50" height="36" rx="8"/>
      <g class="out"><rect class="accent" x="0" y="0" width="224" height="36" rx="8"/><text class="lab on-accent" x="14" y="22">OUT.MP4</text></g></g>"""
    return f"""  <g transform="translate({x} {y})">
    <text class="lab n{i}" x="0" y="80">{lab}</text>
    {art}
    <text class="h" x="0" y="212">{h}</text>
    <text class="s" x="0" y="236">{s}</text>
  </g>"""


def how_it_works(pal: dict[str, str], stacked: bool, background: bool) -> str:
    css = hiw_css(pal)
    title = "How DeckTalk works: you write a script; your voice reads it and every word gets a timestamp; slides reveal on the words in a browser; ffmpeg cuts one mp4."
    if not stacked:
        w, h = 1200, 240
        stages = "\n".join(stage_svg(i, pal, 60 + 280 * i, -40) for i in range(4))
    else:
        w, h = 600, 560
        stages = "\n".join(stage_svg(i, pal, 40 + 280 * (i % 2), 280 * (i // 2)) for i in range(4))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t">
  <title id="t">{title}</title>
  <defs><style>{css}</style></defs>
  {bg_rect(pal, w, h, background)}
{stages}
</svg>
"""


# ---- mark -------------------------------------------------------------------------------------


def mark_glyph(pal: dict[str, str], dot_cy: float = 3.75) -> str:
    """Four ticks with the second one lit under a cue dot, in a 24-unit square."""
    return f"""<g stroke-width="2.25" stroke-linecap="round">
    <line x1="4" y1="12" x2="4" y2="20" stroke="{pal["mark_bar"]}"/>
    <line x1="9.5" y1="8" x2="9.5" y2="20" stroke="{pal["accent"]}"/>
    <line x1="15" y1="12" x2="15" y2="20" stroke="{pal["mark_bar"]}"/>
    <line x1="20.5" y1="12" x2="20.5" y2="20" stroke="{pal["mark_bar"]}"/>
  </g><circle cx="9.5" cy="{dot_cy}" r="2.25" fill="{pal["accent"]}"/>"""


def mark(pal: dict[str, str], size: int = 24, background: bool = False) -> str:
    """The cue glyph from the hero: a word's tick lit under its dot, among its neighbours."""
    s = size / 24
    bg = f'<rect width="{size}" height="{size}" rx="{5 * s:.1f}" fill="{pal["bg"]}"/>' if background else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" role="img" aria-label="DeckTalk">
  {bg}{mark_glyph(pal)}
</svg>
"""


# ---- alignment ---------------------------------------------------------------------------------

FRAME_W, FRAME_H, FRAME_GAP = 88, 50, 8
COVER_FRAMES = 3  # frames that are still covered before the clock starts
STRIP_CUES = {1, 5}  # the strip shows the curve and the number; the slide change is the hero's


def alignment(pal: dict[str, str], xs: list[float], background: bool) -> str:
    """Why the cuts are exact: the recording opens on a tinted cover, the first clean frame is
    narration t=0, and each cue is a spoken word measured from that same origin."""
    w, h = 1200, 250
    left = 72
    n_frames = 11
    strip_y = 58
    pitch = FRAME_W + FRAME_GAP
    t0_x = left + COVER_FRAMES * pitch
    words_y = 168
    tick_y = 178
    curve_x = t0_x + xs[1] + 1  # the tick under "curve"
    number_x = t0_x + xs[5] + 1  # the tick under "number"
    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".w{{font:600 26px {SANS};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".cue{{fill:{pal['accent']}}}.dim{{fill:{pal['dim']}}}")
    css.append(f".cap{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".frame{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}")
    css.append(f".frame.on{{stroke:{pal['accent']};stroke-width:1.5}}")
    css.append(f".cover{{fill:{pal['cover']};stroke:{pal['cover_edge']};stroke-width:1}}")
    css.append(f".tick{{stroke:{pal['tick']};stroke-width:2.5;stroke-linecap:round}}.tick.on{{stroke:{pal['accent']}}}")
    css.append(f".t0{{stroke:{pal['ink']};stroke-width:2}}")
    css.append(f".lead{{stroke:{pal['accent']};stroke-width:1.5;stroke-dasharray:3 4}}")
    css.append(f".axis{{stroke:{pal['hair']};stroke-width:1.5}}")
    css.append(f".num{{font:700 16px {SANS};letter-spacing:-.03em;fill:{pal['accent']}}}")
    css.append(f".brace{{stroke:{pal['mute']};stroke-width:1.5;fill:none}}")

    def frame_at(cue_x: float) -> int:
        """The index of the first frame whose midpoint lies past a cue, where its reveal shows."""
        return next(i for i in range(n_frames) if left + i * pitch + FRAME_W / 2 > cue_x)

    lit = {frame_at(curve_x), frame_at(number_x)}
    frames = []
    for i in range(n_frames):
        x = left + i * pitch
        if i < COVER_FRAMES:
            frames.append(f'<rect class="cover" x="{x}" y="{strip_y}" width="{FRAME_W}" height="{FRAME_H}" rx="6"/>')
            continue
        # A miniature of the hero slide: a frame shows what had happened by its midpoint. The curve
        # draws over three frames after the word "curve", and the number appears after "number".
        mid = x + FRAME_W / 2
        progress = min(1.0, max(0.0, (mid - curve_x) / (3 * pitch)))
        cls = "frame on" if i in lit else "frame"
        art = [f'<rect class="{cls}" x="{x}" y="{strip_y}" width="{FRAME_W}" height="{FRAME_H}" rx="6"/>']
        art.append(f'<line class="axis" x1="{x + 10}" y1="{strip_y + 40}" x2="{x + 52}" y2="{strip_y + 40}"/>')
        art.append(f'<line class="axis" x1="{x + 10}" y1="{strip_y + 12}" x2="{x + 10}" y2="{strip_y + 40}"/>')
        if progress > 0:
            art.append(
                f'<path pathLength="1" stroke-dasharray="1 2" stroke-dashoffset="{1 - progress:.2f}" '
                f'd="M{x + 10},{strip_y + 38} C{x + 22},{strip_y + 37} {x + 34},{strip_y + 28} {x + 44},{strip_y + 18} '
                f'S{x + 50},{strip_y + 12} {x + 52},{strip_y + 11}" fill="none" stroke="{pal["accent"]}" '
                'stroke-width="2.5" stroke-linecap="round"/>'
            )
        if mid > number_x:
            art.append(f'<text class="num" x="{x + 58}" y="{strip_y + 32}">3×</text>')
        frames.append("".join(art))
    cover_mid = left + (COVER_FRAMES * pitch - FRAME_GAP) / 2
    words_svg = "".join(
        f'<tspan class="w {"cue" if i in STRIP_CUES else ""}" x="{t0_x + x:.1f}">{w}</tspan>'
        for i, (w, x) in enumerate(zip(SENTENCE, xs, strict=True))
    )
    ticks_svg = "".join(
        f'<line class="tick {"on" if i in STRIP_CUES else ""}" x1="{t0_x + x + 1:.1f}" y1="{tick_y}" x2="{t0_x + x + 1:.1f}" y2="{tick_y + 14}"/>'
        for i, x in enumerate(xs)
    )
    # Each lead runs from the cue's tick up to the bottom centre of the frame that shows its reveal.
    leads = "".join(
        f'<line class="lead" x1="{cx:.1f}" y1="{tick_y - 4}" x2="{left + frame_at(cx) * pitch + FRAME_W / 2:.1f}" y2="{strip_y + FRAME_H + 3}"/>'
        for cx in (curve_x, number_x)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">Why the cuts are exact</title>
  <desc id="d">A strip of recorded frames opens on a tinted cover while the page is hidden. The first clean frame is narration t=0. The spoken words start at the same point, and the frames in which the curve draws and the number appears line up with the words "curve" and "number".</desc>
  <defs><style>{chr(10).join(css)}</style></defs>
  {bg_rect(pal, w, h, background)}
  <text class="lab" x="{left}" y="40">RECORDING</text>
  {"".join(frames)}
  <path class="brace" d="M{left},{strip_y + FRAME_H + 8} v5 H{left + COVER_FRAMES * pitch - FRAME_GAP} v-5"/>
  <text class="cap" x="{cover_mid:.1f}" y="{strip_y + FRAME_H + 30}" text-anchor="middle">covered until the clock starts</text>
  <line class="t0" x1="{t0_x - FRAME_GAP / 2}" y1="{strip_y - 12}" x2="{t0_x - FRAME_GAP / 2}" y2="{tick_y + 18}"/>
  <text class="lab" x="{t0_x + 2}" y="{strip_y - 16}" style="fill:{pal["accent"]}">T = 0</text>
  {leads}
  <text class="lab" x="{left}" y="{words_y}">NARRATION</text>
  <text y="{words_y}">{words_svg}</text>
  {ticks_svg}
  <text class="cap" x="{left}" y="{h - 18}">The first clean frame is t=0, found in the frames rather than on a timer. Every cue is a word, measured from the same origin.</text>
</svg>
"""


# ---- wordmark ---------------------------------------------------------------------------------


def wordmark(pal: dict[str, str], name: tuple[str, float]) -> str:
    """The mark and the name, for the docs navbar. The name is outline paths, so no font ships."""
    d, width = name
    text_x = 34
    w = round(text_x + width)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="32" viewBox="0 0 {w} 32" role="img" aria-label="DeckTalk">
  <g transform="translate(0 5)">{mark_glyph(pal)}</g>
  <path transform="translate({text_x} 24)" fill="{pal["ink"]}" d="{d}"/>
</svg>
"""


# ---- social card -------------------------------------------------------------------------------


def og(pal: dict[str, str], xs: list[float], widths: list[float]) -> str:
    """The 1200 by 630 card that link previews show. It is the hero at rest with the wordmark."""
    w, h = 1200, 630
    css = [font_face()]
    css.append(f".bg{{fill:{pal['bg']}}}")
    css.append(f".lab{{font:500 13px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".w{{font:600 44px {SANS};letter-spacing:-.01em;fill:{pal['ink']}}}.cue{{fill:{pal['accent']}}}")
    css.append(f".tick{{stroke:{pal['tick']};stroke-width:3;stroke-linecap:round}}.tick.on{{stroke:{pal['accent']}}}")
    css.append(f".dot{{fill:{pal['accent']}}}.head{{fill:{pal['ink']}}}")
    css.append(f".title{{font:600 30px {SANS};letter-spacing:-.02em;fill:{pal['ink']}}}")
    css.append(f".tag{{font:400 26px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}.axis{{stroke:{pal['hair']};stroke-width:2}}")
    css.append(f".num{{font:700 64px {SANS};letter-spacing:-.03em;fill:{pal['accent']}}}")
    scale = 44 / MEASURE_PX
    words = "".join(
        f'<tspan class="w {"cue" if i in CUE_WORDS else ""}" x="{x * scale:.1f}">{t}</tspan>'
        for i, (t, x) in enumerate(zip(SENTENCE, xs, strict=True))
    )
    ticks = "".join(
        f'<line class="tick {"on" if i in CUE_WORDS else ""}" x1="{x * scale + 1:.1f}" y1="0" x2="{x * scale + 1:.1f}" y2="22"/>'
        for i, x in enumerate(xs)
    )
    dots = "".join(f'<circle class="dot" cx="{xs[i] * scale + 1:.1f}" cy="-8" r="5"/>' for i in CUE_WORDS)
    head_x = xs[-1] * scale + widths[-1] * scale + 2
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="DeckTalk">
  <defs><style>{chr(10).join(css)}</style></defs>
  <rect class="bg" width="{w}" height="{h}"/>
  <g transform="translate(80 84) scale(1.4)">{mark_glyph(pal)}</g>
  <text class="title" x="130" y="112">DeckTalk</text>
  <text class="lab" x="80" y="230">NARRATION</text>
  <g transform="translate(80 300)">
    <text y="0">{words}</text>
    <g transform="translate(0 34)">{ticks}{dots}<rect class="head" x="{head_x:.1f}" y="-16" width="3" height="52"/></g>
  </g>
  <g transform="translate(80 420)">
    <rect class="block" width="300" height="130" rx="14"/>
    <line class="axis" x1="26" y1="100" x2="160" y2="100"/><line class="axis" x1="26" y1="30" x2="26" y2="100"/>
    <path d="M26,96 C60,94 96,76 130,46 S158,26 160,24" fill="none" stroke="{pal["accent"]}" stroke-width="4" stroke-linecap="round"/>
    <text class="num" x="190" y="86">3×</text>
  </g>
  <text class="tag" x="420" y="470">Narrated presentations, cut to the word.</text>
  <text class="tag" x="420" y="510">A markdown script and HTML slides in.</text>
  <text class="tag" x="420" y="550">One mp4 out, every reveal on its word.</text>
</svg>
"""


def render_png(svg: str, target: Path, width: int, height: int) -> None:
    """Rasterize an SVG with Chromium, for the places that cannot show SVG such as link previews."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        p.set_content(f"<style>html,body{{margin:0}}</style>{svg}")
        p.wait_for_timeout(300)
        target.parent.mkdir(parents=True, exist_ok=True)
        p.screenshot(path=str(target))
        b.close()


# ---- entry ------------------------------------------------------------------------------------


def _clean(svg: str) -> str:
    """Strip trailing whitespace on every line, so the pre-commit hooks never rewrite a generated file."""
    return "\n".join(line.rstrip() for line in svg.splitlines()).rstrip("\n") + "\n"


def build() -> dict[Path, str]:
    widths, space = measure_words(SENTENCE, f"600 {MEASURE_PX}px {SANS}", "-.01em")
    xs: list[float] = []
    x = 0.0
    for i, w in enumerate(widths):
        xs.append(round(x, 1))
        gap = space * (1.6 if SENTENCE[i].endswith((",", ".")) else 1.0)
        x += w + gap
    name = glyph_outlines("DeckTalk", size=22, weight=600, tracking=-0.02)
    out: dict[Path, str] = {}
    docs = ROOT / "docs"
    for variant, pal in (("light", LIGHT), ("dark", DARK)):
        for background, folder in ((False, ASSETS), (True, docs / "images")):
            out[folder / f"hero-{variant}.svg"] = hero(pal, [x * HERO_PX / MEASURE_PX for x in xs], background)
            out[folder / f"how-it-works-{variant}.svg"] = how_it_works(pal, stacked=False, background=background)
            out[folder / f"alignment-{variant}.svg"] = alignment(pal, [x * 26 / MEASURE_PX for x in xs], background)
        out[ASSETS / f"how-it-works-{variant}-stacked.svg"] = how_it_works(pal, stacked=True, background=False)
        out[ASSETS / f"mark-{variant}.svg"] = mark(pal)
        out[docs / "logo" / f"{variant}.svg"] = wordmark(pal, name)
    out[docs / "favicon.svg"] = mark(LIGHT, size=32, background=True)
    out[ASSETS / "og.svg"] = og(LIGHT, xs, widths)
    return {k: _clean(v) for k, v in out.items()}


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
    # The social card is also needed as a PNG. It is not part of --check because raster bytes
    # vary between Chromium builds, so it is only refreshed when the SVG source was rewritten.
    if ASSETS / "og.svg" in changed or not (ROOT / "site" / "og.png").exists():
        for target in (ROOT / "site" / "og.png", ROOT / "docs" / "images" / "og.png"):
            render_png(files[ASSETS / "og.svg"], target, 1200, 630)
            print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
