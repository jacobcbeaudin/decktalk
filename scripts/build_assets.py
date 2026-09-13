# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.50", "fonttools[woff]>=4.50"]
# ///
"""Generate every graphic from one source. The graphics are the hero, how-it-works (wide and
stacked), alignment, the verify strip, the rebuild lanes, the mark, the wordmark, and the favicon.
The README reads assets/, and the docs site reads docs/images/ and docs/logo/.

    uv run scripts/build_assets.py            # writes assets/*.svg, docs/images/*.svg, docs/logo/*.svg, docs/favicon.svg
    uv run scripts/build_assets.py --check    # exit 1 if the committed files would change

Every variant (light/dark, wide/stacked) comes from the same builders and one palette map, so
they cannot drift. The copies in assets/ have a transparent background so they sit on whatever
ground GitHub and PyPI paint. The copies in docs/images/ carry their own background rect. Inter
Tight and JetBrains Mono subsets (OFL, assets/fonts/) are embedded as base64 in the diagrams so
GitHub and PyPI render the intended faces. Word positions in the hero are measured in Chromium
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
CAP_HEIGHT = 0.73  # Inter Tight's cap height as a fraction of the font size.
MEASURE_PX = 32  # The size the words are measured at. Every diagram scales the positions from it.
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
            sys.exit(f"{path} is missing. See assets/fonts/LICENSE.txt for how it was made.")
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
    # The card spans the label row to the caption baseline. The narration column is laid out to
    # the same extent, with the words and ticks centred between the label and the caption.
    card_x, card_y = HERO_W - HERO_PAD - CARD_W, HERO_PAD
    caption_y = card_y + CARD_H
    words_y = 110
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{HERO_W}" height="{HERO_H}" viewBox="0 0 {HERO_W} {HERO_H}" role="img" aria-labelledby="t d">
  <title id="t">DeckTalk</title>
  <desc id="d">A playhead moves along a spoken sentence, one tick per word. When it reaches "curve", a curve draws on the slide. When it reaches "number", a figure appears. On the last word, the slide changes.</desc>
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
    # Each stage's label lights as its stage begins and stays lit. Nothing else marks the stage.
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
    # 04: frames wait faintly, brighten in turn, then merge into one bar that carries the output's name to the
    # loop's end. They rest at a low opacity rather than zero, so the panel never stands empty.
    css.append(f".fr{{transform-box:fill-box;animation:{total}s cubic-bezier(.2,0,0,1) infinite}}")
    for i, (a, b, dx) in enumerate(((78, 81, 0), (81, 84, -58), (84, 87, -116), (87, 90, -174)), start=1):
        css.append(
            f"@keyframes f{i}{{0%,{a}%{{opacity:.25;transform:translateX(0)}}{b}%,90%{{opacity:1;transform:translateX(0)}}92%,95%{{opacity:1;transform:translateX({dx}px)}}99%,100%{{opacity:.25;transform:translateX(0)}}}}.f{i}{{animation-name:f{i}}}"
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
      <rect class="bar fr f1" x="0" y="0" width="50" height="36" rx="8"/>
      <rect class="bar fr f2" x="58" y="0" width="50" height="36" rx="8"/>
      <rect class="bar fr f3" x="116" y="0" width="50" height="36" rx="8"/>
      <rect class="bar fr f4" x="174" y="0" width="50" height="36" rx="8"/>
      <g class="out"><rect class="accent" x="0" y="0" width="224" height="36" rx="8"/><text class="lab on-accent" x="14" y="22">OUT.MP4</text></g></g>"""
    return f"""  <g transform="translate({x} {y})">
    <text class="lab n{i}" x="0" y="80">{lab}</text>
    {art}
    <text class="h" x="0" y="212">{h}</text>
    <text class="s" x="0" y="236">{s}</text>
  </g>"""


def how_it_works(pal: dict[str, str], stacked: bool, background: bool) -> str:
    css = hiw_css(pal)
    title = "How DeckTalk works. You write a script. Your voice reads it, and every word gets a timestamp. Slides reveal on the words in a browser. ffmpeg cuts one mp4."
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
STRIP_CUES = {1, 5}  # The strip shows the curve and the number. The slide change belongs to the hero.


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
    # Each lead runs from just above the cue word's capitals up to the bottom centre of the frame that
    # shows its reveal, so it never crosses the letters. The word's accent tick sits directly below.
    lead_y = words_y - 26 * CAP_HEIGHT - 6
    leads = "".join(
        f'<line class="lead" x1="{cx:.1f}" y1="{lead_y:.1f}" x2="{left + frame_at(cx) * pitch + FRAME_W / 2:.1f}" y2="{strip_y + FRAME_H + 3}"/>'
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


# ---- verify strip -----------------------------------------------------------------------------

# Cue 3:3.1eq of the silent v10 build, read from `decktalk verify 3:3.1eq --json` and from the
# per-frame series that the onset scan reads. The lead and the probe delays are the VerifyConfig
# defaults, and the offset limit is max_offset_frames (2) at 25 fps.
VS_FROM, VS_TO = -3.3, 1.6  # seconds from the cue that the time axis spans
VS_LEAD = 0.1
VS_PROBES = ((0.7, 0.26), (1.5, 0.41))  # delay after the cue in seconds, and the changed share in percent
VS_CONTROLS = (0.0, 0.56)  # the two control shares for the reported 1.5 s probe, nearest the reference first
# The reference is the first frame at or after the lead, so on this cue it is the frame at -70 ms.
VS_FRAMES = ((-70, 0.0), (-30, 0.2292), (10, 0.2616), (50, 0.2631), (90, 0.2855), (130, 0.2870), (170, 0.2870))
VS_ONSET_MS = -30
VS_LIMIT_MS = 80
MINUS = "&#8722;"


def _signed(value: float, digits: int, unit: str) -> str:
    """A number with a true minus sign or a plus sign, so negative times read cleanly in the figure."""
    sign = MINUS if value < 0 else ("+" if value > 0 else "")
    return f"{sign}{abs(value):.{digits}f}{unit}"


def verify_strip(pal: dict[str, str], background: bool) -> str:
    """How `decktalk verify` measures one cue: the reference, the probes, the control spans, and the onset."""
    w, h = 1200, 300
    x0, x1 = 60, 770

    def tx(t: float) -> float:
        return x0 + (t - VS_FROM) / (VS_TO - VS_FROM) * (x1 - x0)

    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".tl{{font:500 12px {MONO};fill:{pal['mute']}}}.tl.acc{{fill:{pal['accent']}}}")
    css.append(f".val{{font:500 11px {MONO};fill:{pal['ink']}}}.val.on{{fill:{pal['on_accent']}}}")
    css.append(f".note{{font:600 13px {SANS};fill:{pal['ink']}}}.note.acc{{fill:{pal['accent']}}}")
    css.append(f".cap{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}.bar{{fill:{pal['bar']}}}.accent{{fill:{pal['accent']}}}")
    css.append(f".span{{fill:{pal['block']};stroke:{pal['bar']};stroke-width:1}}")
    css.append(f".end{{fill:{pal['ink']}}}.end.on{{fill:{pal['on_accent']}}}")
    css.append(f".axis{{stroke:{pal['bar']};stroke-width:1.5}}.tk{{stroke:{pal['mute']};stroke-width:1.5}}")
    css.append(f".cue{{stroke:{pal['accent']};stroke-width:2}}")
    css.append(f".ref{{stroke:{pal['ink']};stroke-width:1.5;stroke-dasharray:3 3}}")
    css.append(f".guide{{stroke:{pal['hair']};stroke-width:1}}")
    css.append(f".cell{{fill:{pal['bg']};stroke:{pal['bar']};stroke-width:1}}")
    css.append(f".cell.on{{stroke:{pal['accent']};stroke-width:2.5}}")
    css.append(f".edge{{stroke:{pal['mute']};stroke-width:1;stroke-dasharray:3 3}}")

    cue_x, ref_x = tx(0.0), tx(-VS_LEAD)
    axis_y = 196
    parts: list[str] = []
    # Faint guides drop from each measured time to the axis.
    for t in (VS_FROM, -1.7, VS_PROBES[0][0], VS_PROBES[1][0]):
        parts.append(f'<line class="guide" x1="{tx(t):.1f}" y1="92" x2="{tx(t):.1f}" y2="{axis_y}"/>')
    parts.append(f'<line class="ref" x1="{ref_x:.1f}" y1="84" x2="{ref_x:.1f}" y2="{axis_y + 6}"/>')

    # The control spans for the reported probe. Each is as long as the probe's span and ends where the next begins.
    best = max(range(len(VS_PROBES)), key=lambda i: VS_PROBES[i][1])
    delay, _ = VS_PROBES[best]
    span = delay + VS_LEAD
    ctl_y = 88
    spans = []
    for n, share in enumerate(VS_CONTROLS):
        b = -VS_LEAD - n * span
        a = b - span
        spans.append((a, b, share))
    quiet_a, quiet_b, _ = min(spans, key=lambda item: item[2])
    parts.append(
        f'<text class="note" x="{(tx(quiet_a) + tx(quiet_b)) / 2:.1f}" y="{ctl_y - 10}" text-anchor="middle">ctl % is the smaller</text>'
    )
    for a, b, share in spans:
        xa, xb = tx(a) + 1, tx(b) - 1
        parts.append(f'<rect class="span" x="{xa:.1f}" y="{ctl_y}" width="{xb - xa:.1f}" height="20" rx="4"/>')
        parts.append(
            f'<text class="val" x="{(xa + xb) / 2:.1f}" y="{ctl_y + 14}" text-anchor="middle">{share:.2f}%</text>'
        )
        for ex in (xa + 6, xb - 6):
            parts.append(f'<circle class="end" cx="{ex:.1f}" cy="{ctl_y + 10}" r="2.5"/>')

    # The probes, each measured from the reference. The accent marks the probe that the row reports.
    for i, (delay, share) in enumerate(VS_PROBES):
        y = 124 + i * 30
        xb = tx(delay)
        on = i == best
        parts.append(
            f'<rect class="{"accent" if on else "bar"}" x="{ref_x:.1f}" y="{y}" width="{xb - ref_x:.1f}" height="20" rx="4"/>'
        )
        label = f"{share:.2f}%, the best probe" if on else f"{share:.2f}%"
        parts.append(
            f'<text class="val{" on" if on else ""}" x="{xb - 12:.1f}" y="{y + 14}" text-anchor="end">{label}</text>'
        )
        for ex in (ref_x + 6, xb - 6):
            parts.append(f'<circle class="end{" on" if on else ""}" cx="{ex:.1f}" cy="{y + 10}" r="2.5"/>')

    parts.append(f'<line class="axis" x1="{x0}" y1="{axis_y}" x2="{x1}" y2="{axis_y}"/>')
    parts.append(f'<line class="cue" x1="{cue_x:.1f}" y1="50" x2="{cue_x:.1f}" y2="{axis_y + 6}"/>')
    parts.append(f'<text class="note acc" x="{cue_x + 8:.1f}" y="62">word start + offset</text>')
    marks = [
        (VS_FROM, _signed(VS_FROM, 1, " s"), "start", ""),
        (-1.7, _signed(-1.7, 1, " s"), "middle", ""),
        (-VS_LEAD, f"reference {_signed(-VS_LEAD, 1, ' s')}", "end", ""),
        (0.0, "cue", "start", " acc"),
        (VS_PROBES[0][0], f"probe {_signed(VS_PROBES[0][0], 1, ' s')}", "middle", ""),
        (VS_PROBES[1][0], f"probe {_signed(VS_PROBES[1][0], 1, ' s')}", "middle", ""),
    ]
    for t, text, anchor, cls in marks:
        x = tx(t)
        parts.append(f'<line class="tk" x1="{x:.1f}" y1="{axis_y - 5}" x2="{x:.1f}" y2="{axis_y + 5}"/>')
        lx = x - 6 if anchor == "end" else (x + 6 if t == 0.0 else x)
        parts.append(f'<text class="tl{cls}" x="{lx:.1f}" y="{axis_y + 22}" text-anchor="{anchor}">{text}</text>')

    # The inset: seven 40 ms frames around the cue, each with its changed share against the reference.
    ix0, ix1 = 830, 1140
    ms_from, ms_to = -95, 195

    def mx(ms: float) -> float:
        return ix0 + (ms - ms_from) / (ms_to - ms_from) * (ix1 - ix0)

    band_a, band_b = mx(-VS_LIMIT_MS), mx(VS_LIMIT_MS)
    parts.append(f'<rect class="block" x="{band_a:.1f}" y="48" width="{band_b - band_a:.1f}" height="154"/>')
    for bx in (band_a, band_b):
        parts.append(f'<line class="edge" x1="{bx:.1f}" y1="48" x2="{bx:.1f}" y2="202"/>')
    parts.append(f'<text class="tl" x="{band_a + 8:.1f}" y="64">&#177;{VS_LIMIT_MS} ms</text>')
    cell_w = (mx(40) - mx(0)) - 8
    top, base = 76, 190
    peak = 0.4
    for ms, share in VS_FRAMES:
        cx = mx(ms)
        on = ms == VS_ONSET_MS
        parts.append(
            f'<rect class="cell{" on" if on else ""}" x="{cx - cell_w / 2:.1f}" y="{top}" width="{cell_w:.1f}" height="{base - top + 6}" rx="4"/>'
        )
        if share > 0:
            bh = share / peak * (base - top - 24)
            parts.append(
                f'<rect class="{"accent" if on else "bar"}" x="{cx - 8:.1f}" y="{base - bh:.1f}" width="16" height="{bh:.1f}" rx="2"/>'
            )
            parts.append(
                f'<text class="val" x="{cx:.1f}" y="{base - bh - 5:.1f}" text-anchor="middle" style="font-size:10px">{share:.2f}</text>'
            )
        else:
            parts.append(f'<text class="tl" x="{cx:.1f}" y="{base - 4}" text-anchor="middle">ref</text>')
        parts.append(
            f'<text class="tl{" acc" if on else ""}" x="{cx:.1f}" y="{axis_y + 22}" text-anchor="middle">{_signed(ms, 0, "")}</text>'
        )
    # The cue falls inside the -30 ms frame's span, so it is marked above the cells rather than drawn through them.
    parts.append(f'<line class="cue" x1="{mx(0):.1f}" y1="50" x2="{mx(0):.1f}" y2="{top - 4}"/>')
    parts.append(f'<text class="tl acc" x="{mx(0) + 6:.1f}" y="64">cue</text>')

    probe_share = VS_PROBES[best][1]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">How verify measures one cue</title>
  <desc id="d">A time axis runs from 3.3 seconds before cue 3.1eq to 1.6 seconds after it. The reference is the first frame at or after 0.1 seconds before the cue, which is the frame 70 milliseconds before it, and probes sit 0.7 and 1.5 seconds after the cue. The 1.5 second probe is the one reported. Its two 1.6 second control spans end at the reference one after the other, and the smaller of their shares, 0.00 percent, is the control. An inset shows seven 40 millisecond frames around the cue with the share of pixels each one changed, and the frame 30 milliseconds before the cue is outlined as the onset, inside the 80 millisecond limit.</desc>
  <defs><style>{chr(10).join(css)}</style></defs>
  {bg_rect(pal, w, h, background)}
  <text class="lab" x="{x0}" y="36">CUE 3:3.1EQ, SILENT BUILD</text>
  <text class="lab" x="{ix0}" y="36">FRAMES AROUND THE CUE, MS</text>
  {"".join(parts)}
  <text class="cap" x="{x0}" y="{h - 42}">The best probe changed {probe_share:.2f}% of the picture, and its smaller control changed {min(VS_CONTROLS):.2f}%.</text>
  <text class="cap" x="{x0}" y="{h - 20}">A control span is as long as its probe's span, and only its first and last frames are compared.</text>
  <text class="cap" x="{ix0}" y="{h - 42}">The outlined frame is the onset, at {abs(VS_ONSET_MS)} ms</text>
  <text class="cap" x="{ix0}" y="{h - 20}">before the cue and inside the limit.</text>
</svg>
"""


# ---- rebuild lanes ----------------------------------------------------------------------------

LANES = (
    ("narrate (cached)", {3}, "voiced", "cached"),
    ("record, plain build", {1, 2, 3, 4, 5}, "recorded", ""),
    ("record, --only 3", {3}, "recorded", "kept"),
)


def rebuild_lanes(pal: dict[str, str], background: bool) -> str:
    """What runs again after section 3 is edited, in narration and in the two kinds of build."""
    w, h = 1200, 320
    left, col0, col_w, gap = 60, 330, 162, 12
    cell_w = col_w - gap
    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".ln{{font:500 14px {MONO};fill:{pal['ink']}}}")
    css.append(f".ct{{font:500 13px {MONO};fill:{pal['mute']}}}.ct.on{{fill:{pal['on_accent']}}}")
    css.append(f".cell{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}.accent{{fill:{pal['accent']}}}")
    rows = [f'<text class="lab" x="{left}" y="44">LANE</text>']
    for n in range(5):
        cx = col0 + n * col_w + cell_w / 2
        rows.append(f'<text class="lab" x="{cx:.1f}" y="44" text-anchor="middle">SECTION {n + 1}</text>')
    for i, (name, lit, on_text, off_text) in enumerate(LANES):
        y = 64 + i * 58
        rows.append(f'<text class="ln" x="{left}" y="{y + 26}">{name}</text>')
        for n in range(5):
            x = col0 + n * col_w
            on = (n + 1) in lit
            rows.append(
                f'<rect class="{"accent" if on else "cell"}" x="{x}" y="{y}" width="{cell_w}" height="40" rx="8"/>'
            )
            text = on_text if on else off_text
            if text:
                rows.append(
                    f'<text class="ct{" on" if on else ""}" x="{x + cell_w / 2:.1f}" y="{y + 25}" text-anchor="middle">{text}</text>'
                )
    y = 64 + 3 * 58 + 10
    rows.append(f'<text class="ln" x="{left}" y="{y + 26}">assemble</text>')
    rows.append(f'<rect class="accent" x="{col0}" y="{y}" width="{4 * col_w + cell_w}" height="40" rx="8"/>')
    rows.append(
        f'<text class="ct on" x="{col0 + 16}" y="{y + 25}">Every section is cut and joined into one mp4.</text>'
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">What runs again after an edit to section 3</title>
  <desc id="d">Five section columns and three lanes. In the narrate lane only section 3 is voiced, and the other sections come from the cache. A plain build records all five sections. A build with --only 3 records only section 3 and keeps the other recordings. Both builds assemble every section into one mp4.</desc>
  <defs><style>{chr(10).join(css)}</style></defs>
  {bg_rect(pal, w, h, background)}
  {"".join(rows)}
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
        out[docs / "images" / f"how-it-works-{variant}-stacked.svg"] = how_it_works(pal, stacked=True, background=True)
        out[docs / "images" / f"verify-strip-{variant}.svg"] = verify_strip(pal, background=True)
        out[docs / "images" / f"rebuild-lanes-{variant}.svg"] = rebuild_lanes(pal, background=True)
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
