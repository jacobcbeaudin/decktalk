# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright>=1.50", "fonttools[woff]>=4.50"]
# ///
"""Generate every graphic from one source. The graphics are the hero, how-it-works (wide and
stacked), the pipeline, narration zero, the verify probes and onset, the rebuild lanes, the narration
split, the duck lane, the cue offset, the mark and its lockups, the favicon set, the social card
and the brand's CSS tokens. Every number a figure prints comes from scripts/figure-data/*.json, and
each of those files names in its own `source` block the release, the command and the project it was
measured from. The README reads assets/, the docs site reads docs/images/ and docs/logo/, and the
homepage reads site/tokens.css and site/favicon.svg.

    uv run scripts/build_assets.py            # writes assets/*.svg, docs/images/*.svg, docs/logo/*.svg, the favicons, site/tokens.css
    uv run scripts/build_assets.py --check    # exit 1 if the committed files would change

Every variant (light/dark, wide/stacked) comes from the same builders and one palette map, so
they cannot drift. The palette is the brand's: a warm near-black or warm paper as the ground, one
tungsten gold for the voice that marks only the word being spoken, a cue and a live tick, and paper for
every picture. The copies in assets/ have a transparent background so they sit on whatever ground
GitHub and PyPI paint. The copies in docs/images/ carry their own background rect. Instrument Sans
and IBM Plex Mono subsets (OFL, assets/fonts/) are embedded as base64 in the diagrams so GitHub
and PyPI render the intended faces, and the two diagrams that carry a headline add Instrument Serif,
the face the headings are set in.
Word positions in the hero are measured in Chromium with that exact font, so the tick under each
word is under the word. The wordmark instead carries the letters as outline paths traced with
fontTools, so each logo is a few kilobytes.

Motion rules (from the design review): base styles are the END state, keyframes carry the start
values, so `prefers-reduced-motion: reduce` shows the finished frame. Loops dissolve back to the
start over the last few percent instead of snapping.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import re
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"

# The brand palette, direction B "the voice". Ratios are WCAG 2.x contrast against `bg`.
# `accent` is the voice, tungsten gold: it marks a spoken word, a cue, a live tick, and nothing that is a picture.
LIGHT = {
    "bg": "#fbf7f1",  # warm paper
    "ink": "#1b1511",  # 16.9:1
    "dim": "#a99d8f",  # unspoken words, 2.6:1, decorative by design
    "mute": "#6f655b",  # labels, captions, 5.3:1
    "block": "#f2ece3",  # a second shelf
    "hair": "#e1d8cc",  # hairlines
    "bar": "#d3c9bc",
    "tick": "#c4b9ab",
    "accent": "#7a5000",  # the voice, deepened for paper, 6.6:1 (the gold itself is 1.7:1 on paper)
    "on_accent": "#ffffff",  # 7.1:1 on the accent
    "paper": "#ffffff",  # a picture's ground
    "text2": "#5b524a",  # 7.2:1
    "focus": "#8f8374",  # focus ring, 3.5:1
    "voice_ink": "#ffffff",
    "voice_soft": "#fbeac4",  # ink 15.2:1 on it
    "ok": "#1f7a44",
    "warn": "#8a5a00",
    "cover": "#fad3f3",  # the narration-zero diagram's cover frames, DeckTalk's magenta cover
    "cover_edge": "#fd62f9",
}
DARK = {
    "bg": "#15110e",  # warm near-black, a little brown in it, never blue
    "ink": "#f5eee4",  # 16.3:1
    "dim": "#6a5f54",  # unspoken words, 3.1:1, decorative by design
    "mute": "#9a8e80",  # 5.9:1
    "block": "#1f1915",
    "hair": "#3b322c",
    "bar": "#4a3f37",
    "tick": "#4a3f37",
    "accent": "#f2b441",  # the voice, tungsten gold, 10.2:1
    "on_accent": "#1a0e07",  # 10.3:1 on the accent
    "paper": "#fbf6ee",
    "text2": "#c3b7a8",  # 9.5:1
    "focus": "#8a7c6e",  # 4.6:1
    "voice_ink": "#1a0e07",
    "voice_soft": "#3d2e12",  # text 11.4:1 on it
    "ok": "#8fd4a0",
    "warn": "#f2c66d",
    "cover": "#33083a",
    "cover_edge": "#a20da8",
}

SANS = "'DT Sans', 'Instrument Sans', system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "'DT Mono', 'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace"
TITLE = "'DT Title', 'Instrument Serif', Georgia, 'Times New Roman', serif"
DISPLAY = "'DT Display', 'Bricolage Grotesque', 'Avenir Next', 'Helvetica Neue', system-ui, sans-serif"
CAP_HEIGHT = 0.72  # Instrument Sans's cap height as a fraction of the font size.
MEASURE_PX = 32  # The size the words are measured at. Every diagram scales the positions from it.
HERO_W, HERO_H, HERO_PAD = 1000, 248, 48
HERO_PX = 30  # the line's size in the hero, so it clears the card at this width
CARD_W, CARD_H, CARD_R = 420, 152, 12  # the hero's slide card
# The hero is the opening of the lesson film: "A bowl. [beat] A ball. [beat] Watch it step down on my
# count. [beat] One. [beat] Two, three." The line leaves out "Watch it step down on my count.", and
# every time the hero prints or animates on is a word's real start from scripts/figure-data/hero.json.
HERO_WORDS = ["A", "bowl.", "A", "ball.", "One.", "Two,", "three."]
HERO_CUES = {1, 3, 4, 5, 6}  # bowl, ball, and the three count words
HERO_LOOP, HERO_LEAD = 9.0, 0.5  # the loop plays the words in real time, starting HERO_LEAD s in
# The film's scene 1 draws the bowl y = 720 - 320 u^2. The ball starts at u = -0.96, and each count
# is a step of gradient descent on u^2 at step size 0.25, so each step halves u.
HERO_STEPS = [-0.96, -0.48, -0.24, -0.12]


class Bowl:
    """The film's bowl, drawn y = floor - depth u^2 for u in [-1, 1], in whatever pixels a figure uses."""

    def __init__(self, cx: float, floor: float, hw: float, depth: float) -> None:
        self.cx, self.floor, self.hw, self.depth = cx, floor, hw, depth

    def path(self) -> str:
        return "".join(
            f"{'L' if n else 'M'}{self.cx + self.hw * u:.1f},{self.floor - self.depth * u * u:.1f}"
            for n, u in enumerate(i / 20 - 1 for i in range(41))
        )

    def at(self, u: float, r: float) -> tuple[float, float]:
        """A point r px off the bowl along its inner normal, so a circle of radius r sits on the line."""
        dx, dy = self.hw, -2 * self.depth * u
        n = (dx * dx + dy * dy) ** 0.5
        return self.cx + self.hw * u + r * dy / n, self.floor - self.depth * u * u - r * dx / n

    def art(self, ball_r: float, ball_off: float, mark_r: float, mark_off: float, pop: bool = False) -> str:
        """The ball's end state: a mark where each count stepped from, and the ball where it came to rest.
        With `pop`, each mark and then the ball carries its own pop class, p0 to p3, in step order."""

        def cls(k: int) -> str:
            return f" mp p{k}" if pop else ""

        marks = "".join(
            f'<circle class="mark{cls(k)}" cx="{self.at(u, mark_off)[0]:.1f}" cy="{self.at(u, mark_off)[1]:.1f}" r="{mark_r}"/>'
            for k, u in enumerate(HERO_STEPS[:-1])
        )
        bx, by = self.at(HERO_STEPS[-1], ball_off)
        return f'{marks}<circle class="ball{cls(len(HERO_STEPS) - 1)}" cx="{bx:.1f}" cy="{by:.1f}" r="{ball_r}"/>'


FACES = (
    # family, file, weight range: fonts subset to the glyphs the diagrams use
    ("DT Sans", "InstrumentSans.woff2", "400 700"),
    ("DT Mono", "PlexMono.woff2", "400 500"),
)
TITLE_FACE = ("DT Title", "InstrumentSerif.woff2", "400")
# The logo lockups trace their letters from this one instead of embedding it. The social card sets the
# wordmark as live text, so that one card embeds it.
DISPLAY_FACE = ("DT Display", "BricolageGrotesque.woff2", "500")


def font_face(title: bool = False, display: bool = False) -> str:
    extra = [f for f, want in ((TITLE_FACE, title), (DISPLAY_FACE, display)) if want]
    rules = []
    for family, name, weights in (*FACES, *extra):
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


def glyph_outlines(text: str, size: float, tracking: float) -> tuple[str, float]:
    """The text as one SVG path in a y-down px space with the baseline at y=0, plus its width.

    The display face (a static instance at the wordmark's weight) places each glyph by its own
    advance width, with `tracking` (in em) added between letters.
    """
    font = TTFont(FONTS / DISPLAY_FACE[1])
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


def hero_times() -> tuple[list[float], float]:
    """The measured start of each word of the hero line, and the start of the word "count".

    The words file holds every word of the section, so the line's own words are found in order and a
    repeated word takes the next occurrence rather than the first.
    """
    words = figure_data("hero")["words"]
    times, j = [], 0
    for word in HERO_WORDS:
        key = word.strip(".,")
        while words[j]["word"] != key:
            j += 1
        times.append(words[j]["start"])
        j += 1
    return times, next(w["start"] for w in words if w["word"] == "count")


def hero_css(pal: dict[str, str], ticks_x: list[float], times: list[float], count_t: float) -> str:
    """Every rule the hero loop needs: the palette, the playhead, one pair of keyframes per word, and
    the reveals of the bowl, the ball and the three count boxes."""
    total = HERO_LOOP
    bowl_t, ball_t, counts = times[1], times[3], times[4:]

    def pct(t: float) -> float:
        return round((HERO_LEAD + t) / total * 100, 1)

    def show(name: str, on: float, off: float | None = None) -> str:
        """Hidden until pct `on`, and hidden again from pct `off` when given. The base style is the end state."""
        a, b = pct(on), None if off is None else pct(off)
        if b is None:
            frames = f"0%,{a - 0.1:.1f}%{{opacity:0}}{a:.1f}%,100%{{opacity:1}}"
            base = ""
        else:
            frames = f"0%,{a - 0.1:.1f}%{{opacity:0}}{a:.1f}%,{b - 0.1:.1f}%{{opacity:1}}{b:.1f}%,100%{{opacity:0}}"
            base = "opacity:0;"
        return f"@keyframes {name}{{{frames}}}.{name}{{{base}animation:{name} {total}s linear infinite}}"

    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(
        f".w{{font:600 {HERO_PX}px {SANS};letter-spacing:-.01em;fill:{pal['ink']};animation:{total}s linear infinite}}"
    )
    css.append(f".cap{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}")
    css.append(
        f".tick{{stroke:{pal['accent']};stroke-width:2.5;stroke-linecap:round;animation:{total}s linear infinite}}"
    )
    css.append(f".dot{{fill:{pal['accent']}}}")
    css.append(f".bowl{{stroke:{pal['ink']};stroke-width:2.5;fill:none;stroke-linecap:round;stroke-linejoin:round}}")
    css.append(f".ball{{fill:{pal['ink']};stroke:{pal['block']};stroke-width:2.5}}.mark{{fill:{pal['mute']}}}")
    css.append(
        f".box{{fill:{pal['bg']};stroke:{pal['ink']};stroke-width:1.5}}.box.full{{fill:{pal['accent']};stroke:{pal['accent']}}}"
    )
    css.append(f".bl{{font:600 14px {SANS};fill:{pal['ink']};text-anchor:middle}}.bl.full{{fill:{pal['on_accent']}}}")
    css.append(f".time{{font:500 12px {SANS};fill:{pal['mute']};text-anchor:middle;font-variant-numeric:tabular-nums}}")
    # The loop dissolves: everything fades out over the last 7 % and back in over the first 4 %.
    css.append(f".loop{{animation:loop {total}s linear infinite}}")
    css.append("@keyframes loop{0%{opacity:0}4%,93%{opacity:1}100%{opacity:0}}")
    # The playhead rests on each word's tick from that word's start, with a short glide between ticks.
    head = [f"0%,{pct(times[0]):.1f}%{{transform:translateX({ticks_x[0]:.1f}px)}}"]
    for i in range(1, len(times)):
        head.append(
            f"{pct(times[i]) - 0.8:.1f}%{{transform:translateX({ticks_x[i - 1]:.1f}px)}}"
            f"{pct(times[i]):.1f}%{{transform:translateX({ticks_x[i]:.1f}px)}}"
        )
    head.append(f"100%{{transform:translateX({ticks_x[-1]:.1f}px)}}")
    css.append(
        f".head{{transform:translateX({ticks_x[-1]:.1f}px);animation:head {total}s cubic-bezier(.2,0,0,1) infinite}}"
    )
    css.append(f"@keyframes head{{{''.join(head)}}}")
    for i, t in enumerate(times):
        p = pct(t)
        on = pal["accent"] if i in HERO_CUES else pal["ink"]
        css.append(f"@keyframes w{i}{{0%,{p - 0.1:.1f}%{{fill:{pal['dim']}}}{p:.1f}%,100%{{fill:{on}}}}}")
        css.append(
            f"@keyframes t{i}{{0%,{p - 0.1:.1f}%{{stroke:{pal['tick']}}}{p:.1f}%,100%{{stroke:{pal['accent']}}}}}"
        )
        css.append(f".w{i}{{fill:{on};animation-name:w{i}}}.t{i}{{animation-name:t{i}}}")
        if i in HERO_CUES:
            css.append(show(f"d{i}", t))
    css.append(show("bw", bowl_t))
    css.append(show("bx", count_t))
    steps_on = [ball_t, *counts]
    for k, on in enumerate(steps_on):
        css.append(show(f"b{k}", on, steps_on[k + 1] if k + 1 < len(steps_on) else None))
    for k, t in enumerate(counts):
        css.append(show(f"m{k}", t))
        css.append(show(f"f{k}", t))
    css.append(reduced_motion())
    return chr(10).join(css)


def hero(pal: dict[str, str], xs: list[float], background: bool) -> str:
    """The lesson film's opening. Each word lights at its real start, the bowl and the ball appear on
    their words, and on each count the ball steps down and that word's box fills with its start time."""
    times, count_t = hero_times()
    counts = times[4:]
    ticks_x = [x + 1 for x in xs]
    css = hero_css(pal, ticks_x, times, count_t)

    words_svg = "".join(
        f'<tspan class="w w{i}" x="{x:.1f}">{w}</tspan>' for i, (w, x) in enumerate(zip(HERO_WORDS, xs, strict=True))
    )
    ticks_svg = "".join(
        f'<line class="tick t{i}" x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="16"/>' for i, x in enumerate(ticks_x)
    )
    dots_svg = "".join(f'<circle class="dot d{i}" cx="{ticks_x[i]:.1f}" cy="-6" r="4"/>' for i in sorted(HERO_CUES))

    # The slide card, in card pixels: the bowl on the left, the three count boxes on the right.
    pad = 24
    bowl_hw, bowl_cx = 72, pad + 72
    bowl = Bowl(bowl_cx, 124, bowl_hw, 90)
    bowl_d = bowl.path()
    balls = "".join(
        f'<circle class="ball b{k}" cx="{bowl.at(u, 10)[0]:.1f}" cy="{bowl.at(u, 10)[1]:.1f}" r="9"/>'
        for k, u in enumerate(HERO_STEPS)
    )
    marks = "".join(
        f'<circle class="mark m{k}" cx="{bowl.at(u, 4)[0]:.1f}" cy="{bowl.at(u, 4)[1]:.1f}" r="3.5"/>'
        for k, u in enumerate(HERO_STEPS[:-1])
    )
    box_x0, gap, box_y, box_h = bowl_cx + bowl_hw + pad, 8, 48, 36
    box_w = (CARD_W - pad - box_x0 - 2 * gap) / 3
    boxes, fills = [], []
    for k, (label, t) in enumerate(zip(("one", "two", "three"), counts, strict=True)):
        bx = box_x0 + k * (box_w + gap)
        mid = bx + box_w / 2
        boxes.append(
            f'<rect class="box" x="{bx:.1f}" y="{box_y}" width="{box_w:.1f}" height="{box_h}" rx="8"/>'
            f'<text class="bl" x="{mid:.1f}" y="{box_y + 23}">{label}</text>'
        )
        fills.append(
            f'<g class="f{k}"><rect class="box full" x="{bx:.1f}" y="{box_y}" width="{box_w:.1f}" height="{box_h}" rx="8"/>'
            f'<text class="bl full" x="{mid:.1f}" y="{box_y + 23}">{label}</text>'
            f'<text class="time" x="{mid:.1f}" y="{box_y + box_h + 22}">{t:.2f} s</text></g>'
        )
    card_x, card_y = HERO_W - HERO_PAD - CARD_W, HERO_PAD
    caption_y = card_y + CARD_H
    words_y = 110
    spoken = " ".join(HERO_WORDS)
    time_list = ", ".join(f"{t:.2f} s" for t in counts[:-1]) + f", and {counts[-1]:.2f} s"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{HERO_W}" height="{HERO_H}" viewBox="0 0 {HERO_W} {HERO_H}" role="img" aria-labelledby="t d">
  <title id="t">DeckTalk</title>
  <desc id="d">A playhead moves along the narration "{spoken}", one tick per word. A bowl appears on the slide on "bowl", and a ball appears on its rim on "ball". On each count word, the ball steps down the bowl and a box fills with that word's start time: {time_list}.</desc>
  <defs><style>{css}</style></defs>
  {bg_rect(pal, HERO_W, HERO_H, background)}
  <g class="loop">
  <text class="lab" x="{HERO_PAD}" y="{card_y}">NARRATION</text>
  <g transform="translate({HERO_PAD} {words_y})">
    <text y="0">{words_svg}</text>
    <g transform="translate(0 30)">{ticks_svg}{dots_svg}<g class="head"><rect x="-1" y="-14" width="2" height="38" fill="{pal["ink"]}"/></g></g>
    <text class="cap" x="0" y="{caption_y - words_y}">Every word has a start time, and each reveal waits for its word.</text>
  </g>
  <g transform="translate({card_x} {card_y})">
    <rect class="block" width="{CARD_W}" height="{CARD_H}" rx="{CARD_R}"/>
    <path class="bowl bw" d="{bowl_d}"/>
    {marks}{balls}
    <g class="bx">{"".join(boxes)}</g>
    {"".join(fills)}
  </g>
  </g>
</svg>
"""


# ---- how it works ----------------------------------------------------------------------------

STAGES = [
    ("01 WRITE", "A script in markdown", "One heading starts one section."),
    ("02 NARRATE", "Your voice reads it", "The speech provider times every word."),
    ("03 RECORD", "Slides appear on their words", "Plain HTML, recorded in Chromium."),
    ("04 ASSEMBLE", "Cut to the frame", "ffmpeg cuts, mixes and measures."),
]
NARRATE_W = 222  # the width the narrate panel's ticks span
NARRATE_CUES = (1, 3)  # the cue dots sit on "bowl" and "ball", and the timestamp sits on "bowl"


def narrate_ticks(xs: list[float]) -> list[float]:
    """The hero's measured word starts, scaled so the ticks span the narrate panel, one per word."""
    return [round(2 + x * (NARRATE_W - 2) / xs[-1], 1) for x in xs]


def hiw_css(pal: dict[str, str], ticks: list[float], total: float = 10.0) -> str:
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
    # 02: a tick per word of the opening lights under a sweeping head, cue dots on "bowl" and "ball",
    # and the timestamp of "bowl".
    css.append(
        f".tk{{stroke:{pal['accent']};stroke-width:2.5;stroke-linecap:round;animation:{total}s linear infinite}}"
    )
    css.append(f".head2{{transform:translateX({ticks[-1] - 1}px);opacity:0;animation:head2 {total}s linear infinite}}")
    css.append(
        f"@keyframes head2{{0%,26%{{transform:translateX(0);opacity:1}}48%{{transform:translateX({ticks[-1] - 1}px);opacity:1}}50%,100%{{transform:translateX({ticks[-1] - 1}px);opacity:0}}}}"
    )
    lit_at = []
    for i, x in enumerate(ticks):
        at = round(26 + x / (ticks[-1]) * 22)
        lit_at.append(at)
        css.append(
            f"@keyframes k{i}{{0%,{at - 1}%{{stroke:{pal['tick']}}}{at}%,95%{{stroke:{pal['accent']}}}99%,100%{{stroke:{pal['tick']}}}}}.k{i}{{animation-name:k{i}}}"
        )
    css.append(f".cd{{fill:{pal['accent']};animation:{total}s linear infinite}}")
    css.append(f".ts{{fill:{pal['accent']};animation:{total}s linear infinite}}")
    for n, i in enumerate(NARRATE_CUES, start=1):
        at = lit_at[i]
        css.append(
            f"@keyframes cd{n}{{0%,{at - 1}%{{opacity:0}}{at}%,95%{{opacity:1}}99%,100%{{opacity:0}}}}.cd{n}{{animation-name:cd{n}}}"
        )
    # 03: the mini bowl draws, then each step mark and last the ball pop in turn. The 1 2 dasharray
    # keeps the next dash's round cap off the end of the path before the draw starts.
    css.append(
        f".mc{{stroke-dasharray:1 2;stroke-dashoffset:0;animation:mc {total}s cubic-bezier(.3,0,.1,1) infinite}}"
    )
    css.append("@keyframes mc{0%,54%{stroke-dashoffset:1}62%,95%{stroke-dashoffset:0}99%,100%{stroke-dashoffset:1}}")
    css.append(
        f".mp{{transform-box:fill-box;transform-origin:center;animation:mp {total}s cubic-bezier(.2,.9,.2,1) infinite}}"
    )
    for k in range(len(HERO_STEPS)):
        a = 63 + 2 * k
        css.append(
            f"@keyframes p{k}{{0%,{a}%{{opacity:0;transform:scale(.4)}}{a + 3}%,95%{{opacity:1;transform:scale(1)}}99%,100%{{opacity:0;transform:scale(.4)}}}}.p{k}{{animation-name:p{k}}}"
        )
    css.append(
        f".mbowl{{stroke:{pal['ink']};stroke-width:3;fill:none;stroke-linecap:round;stroke-linejoin:round}}"
        f".ball{{fill:{pal['ink']};stroke:{pal['block']};stroke-width:2}}.mark{{fill:{pal['mute']}}}"
    )
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


def stage_svg(i: int, x: int, y: int, ticks: list[float]) -> str:
    lab, h, s = STAGES[i]
    if i == 0:
        art = """<g transform="translate(0 100)">
      <rect class="accent type ty1" x="0" y="0" width="64" height="8" rx="4"/>
      <rect class="bar type ty2" x="0" y="20" width="196" height="8" rx="4"/>
      <rect class="bar type ty3" x="0" y="40" width="168" height="8" rx="4"/>
      <rect class="bar type ty4" x="0" y="60" width="184" height="8" rx="4"/></g>"""
    elif i == 1:
        lines = "".join(f'<line class="tk k{j}" x1="{tx}" y1="46" x2="{tx}" y2="62"/>' for j, tx in enumerate(ticks))
        bowl_x, ball_x = (ticks[j] for j in NARRATE_CUES)
        art = f"""<g transform="translate(0 100)">{lines}
      <circle class="cd cd1" cx="{bowl_x}" cy="38" r="4"/><circle class="cd cd2" cx="{ball_x}" cy="38" r="4"/>
      <text class="lab ts cd1" x="{bowl_x + 8:.1f}" y="38">{figure_data("how-it-works")["start"]:.2f}</text>
      <g class="head2"><rect x="1" y="30" width="2" height="38" class="accent"/></g></g>"""
    elif i == 2:
        bowl = Bowl(114, 66, 88, 50)
        art = f"""<g transform="translate(0 98)">
      <rect class="block" width="228" height="78" rx="8"/>
      <path class="mbowl mc" pathLength="1" d="{bowl.path()}"/>
      {bowl.art(ball_r=7, ball_off=8, mark_r=3, mark_off=3.5, pop=True)}</g>"""
    else:
        art = """<g transform="translate(0 100)">
      <rect class="bar fr f1" x="0" y="0" width="50" height="36" rx="8"/>
      <rect class="bar fr f2" x="58" y="0" width="50" height="36" rx="8"/>
      <rect class="bar fr f3" x="116" y="0" width="50" height="36" rx="8"/>
      <rect class="bar fr f4" x="174" y="0" width="50" height="36" rx="8"/>
      <g class="out"><rect class="accent" x="0" y="0" width="224" height="36" rx="8"/><text class="lab on-accent" x="14" y="22">OUT.MP4</text></g></g>"""
    cmds = STAGE_COMMANDS[i]
    cmd = f'<text class="cmd" x="0" y="102">{" · ".join(cmds)}</text>' if cmds else ""
    return f"""  <g transform="translate({x} {y})">
    <text class="lab n{i}" x="0" y="80">{lab}</text>
    {cmd}
    <g transform="translate(0 {CMD_ROW})">
    {art}
    <text class="h" x="0" y="212">{h}</text>
    <text class="s" x="0" y="236">{s}</text>
    </g>
  </g>"""


# The pipeline stages each panel runs, in their fixed order. Writing the script runs none.
STAGE_COMMANDS = ((), ("narrate", "cue"), ("record", "soundscape"), ("assemble", "verify"))
CMD_ROW = 22  # the height the command row adds under each panel label


def how_it_works(pal: dict[str, str], stacked: bool, background: bool, ticks: list[float]) -> str:
    css = hiw_css(pal, ticks) + f"\n.cmd{{font:500 13px {MONO};fill:{pal['ink']}}}"
    title = "How DeckTalk works. You write a script. Your voice reads it, and every word gets a timestamp. Slides appear on their words in a browser. ffmpeg cuts one mp4. The six stages are narrate, cue, record, soundscape, assemble and verify."
    if not stacked:
        w, h = 1200, 240 + CMD_ROW
        stages = "\n".join(stage_svg(i, 60 + 280 * i, -40, ticks) for i in range(4))
    else:
        w, h = 600, 560 + 2 * CMD_ROW
        stages = "\n".join(stage_svg(i, 40 + 280 * (i % 2), 280 * (i // 2), ticks) for i in range(4))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t">
  <title id="t">{title}</title>
  <defs><style>{css}</style></defs>
  {bg_rect(pal, w, h, background)}
{stages}
</svg>
"""


# ---- pipeline ---------------------------------------------------------------------------------

# The five stations from a script to one film, each with the file it is, and what the docs call it.
# The cues station is the join, where a named phrase meets its picture, and it is the only one in colour.
STATIONS = (
    ("Script", "script.md", ("Markdown, one heading", "a section. It comes first.")),
    ("Voice", "<hash>.mp3 · words.json", ("Your voice reads it, and", "every word gets a time.")),
    ("Cues", "cues.json", ("You name the phrase, and", "the picture starts on it.")),
    ("Slides", "deck/index.html", ("Plain HTML, one scene", "a section, in any theme.")),
    ("Video", "build/final/<name>.mp4", ("Recorded in real time, and", "every reveal is measured.")),
)
CUE_STATION = 2
# The cue on the cues station: the starter's first cue, from src/decktalk/template/starter/cues.json.
STATION_CUE = ("This is DeckTalk", "1.1:title")
PIPELINE_HEADLINE = "A script becomes one film, and every picture lands on its word."
PIPELINE_NOTE = "Only that section is voiced and recorded again. The others keep their takes and their recordings."


def station_icon(i: int) -> str:
    """The glyph on station i, in a 48 by 40 box: a page, a waveform, the join, a stack of slides, a frame."""
    if i == 0:
        return (
            '<path class="ink" d="M 0 0 h 22 l 8 8 v 30 h -30 z M 22 0 v 8 h 8"/>'
            '<g class="dim"><line x1="6" y1="16" x2="22" y2="16"/><line x1="6" y1="22" x2="24" y2="22"/><line x1="6" y1="28" x2="18" y2="28"/></g>'
        )
    if i == 1:
        return (
            '<g class="ink">'
            + "".join(
                f'<line x1="{x}" y1="{19 - h / 2}" x2="{x}" y2="{19 + h / 2}"/>'
                for x, h in ((2, 6), (8, 18), (14, 30), (20, 14), (26, 24), (32, 10), (38, 4))
            )
            + "</g>"
        )
    if i == 2:
        # The picture on top, the word beneath it, and the cue line that joins them.
        return (
            '<rect class="ink" x="0" y="0" width="40" height="22" rx="2"/>'
            '<rect class="lamp" x="0" y="30" width="26" height="6" rx="3"/>'
            '<line class="lampline" x1="0.75" y1="22" x2="0.75" y2="30"/>'
        )
    if i == 3:
        return (
            '<rect class="film" x="8" y="8" width="40" height="22" rx="2" opacity="0.35"/>'
            '<rect class="film" x="4" y="4" width="40" height="22" rx="2" opacity="0.6"/>'
            '<rect class="film" x="0" y="0" width="40" height="22" rx="2"/>'
            '<g class="filmink"><line x1="6" y1="8" x2="22" y2="8"/><line x1="6" y1="14" x2="30" y2="14"/></g>'
        )
    holes = "".join(
        f'<rect class="fill" x="{x}" y="{y}" width="3" height="3"/>' for x in (4, 37) for y in (4, 13.5, 23)
    )
    return f'<rect class="ink" x="0" y="0" width="44" height="30" rx="3"/>{holes}<path class="fill" d="M 18 9 l 12 6 l -12 6 z"/>'


def pipeline(pal: dict[str, str], background: bool) -> str:
    """How DeckTalk makes a film: five stations from the script to the video, the file each one is, and
    the note that a changed sentence voices and records only its own section again."""
    w, h = 1200, 470
    left, card_w, card_h, gap, top = 60, 196, 116, 25, 148
    pitch = card_w + gap
    css = [font_face(title=True)]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}.lab.acc{{fill:{pal['accent']}}}")
    css.append(f".h{{font:400 34px {TITLE};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".name{{font:600 17px {SANS};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".file{{font:500 12px {MONO};fill:{pal['mute']}}}")
    css.append(f".desc{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(
        f".cue{{font:500 13px {MONO};fill:{pal['mute']}}}.cue.acc{{fill:{pal['accent']}}}.cue.ink{{fill:{pal['ink']}}}"
    )
    css.append(
        f".panel{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}.panel.on{{stroke:{pal['accent']};stroke-width:1.5}}"
    )
    css.append(f".hair{{stroke:{pal['hair']};stroke-width:1.5;fill:none}}.arrow{{fill:{pal['bar']}}}")
    css.append(f".ink{{stroke:{pal['ink']};fill:none;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}}")
    css.append(
        f".dim{{stroke:{pal['mute']};fill:none;stroke-width:1.5;stroke-linecap:round}}.fill{{fill:{pal['ink']}}}"
    )
    css.append(f".lamp{{fill:{pal['accent']}}}.lampline{{stroke:{pal['accent']};stroke-width:1.5}}")
    css.append(
        f".film{{fill:{pal['paper']};stroke:{pal['bar']};stroke-width:1}}.filmink{{stroke:{pal['on_accent']};stroke-width:1.5;stroke-linecap:round}}"
    )
    parts: list[str] = [
        f'<text class="lab" x="{left}" y="52">THE PIPELINE</text>',
        f'<text class="h" x="{left}" y="102">{PIPELINE_HEADLINE}</text>',
    ]
    mid = top + card_h / 2
    for i in range(len(STATIONS) - 1):
        xa, xb = left + i * pitch + card_w, left + (i + 1) * pitch
        parts.append(f'<line class="hair" x1="{xa}" y1="{mid}" x2="{xb - 6}" y2="{mid}"/>')
        parts.append(f'<path class="arrow" d="M {xb - 7} {mid - 4} l 7 4 l -7 4 z"/>')
    # The slides feed the cues too: the picture side of the join.
    sx, cx = left + 3 * pitch + card_w / 2, left + CUE_STATION * pitch + card_w / 2 + 24
    parts.append(f'<path class="hair" d="M {sx} {top} C {sx} {top - 40}, {cx} {top - 40}, {cx} {top - 8}"/>')
    parts.append(f'<path class="arrow" d="M {cx - 4} {top - 9} l 4 7 l 4 -7 z"/>')
    for i, (name, file, desc) in enumerate(STATIONS):
        x = left + i * pitch
        on = i == CUE_STATION
        parts.append(f'<g transform="translate({x} {top})">')
        parts.append(f'<rect class="panel{" on" if on else ""}" width="{card_w}" height="{card_h}" rx="8"/>')
        parts.append(f'<g transform="translate(22 20)">{station_icon(i)}</g>')
        parts.append(f'<text class="name" x="22" y="86">{name}</text>')
        parts.append(f'<text class="file" x="22" y="104">{file.replace("<", "&lt;").replace(">", "&gt;")}</text>')
        parts.append(f'<text class="lab{" acc" if on else ""}" x="0" y="{card_h + 34}">0{i + 1}</text>')
        parts.append(f'<text class="desc" x="0" y="{card_h + 60}">{desc[0]}</text>')
        parts.append(f'<text class="desc" x="0" y="{card_h + 80}">{desc[1]}</text>')
        if on:
            phrase, cue = STATION_CUE
            parts.append(
                f'<text class="cue" x="0" y="{card_h + 110}"><tspan class="ink">"{phrase}"</tspan> → <tspan class="acc">{cue}</tspan></text>'
            )
        parts.append("</g>")
    note_y = h - 46
    parts.append(f'<line class="hair" x1="{left}" y1="{note_y - 28}" x2="{w - left}" y2="{note_y - 28}"/>')
    parts.append(f'<text class="lab" x="{left}" y="{note_y}">CHANGE ONE SENTENCE</text>')
    parts.append(f'<text class="desc" x="{left + 210}" y="{note_y}">{PIPELINE_NOTE}</text>')
    desc = (
        "Five stations from left to right: the script, the voice, the cues, the slides and the video, each with its file. "
        f"The script is script.md, one heading a section. The voice reads it and every word gets a time, in a take and its words file. "
        f"The cues are cues.json, where you name the phrase and the picture starts on it, such as the phrase {STATION_CUE[0]} for the cue {STATION_CUE[1]}. "
        "The slides are plain HTML in deck/index.html, one scene a section. The video is recorded in real time, and every reveal is measured. "
        f"A line from the slides joins the cues. Under the stations: change one sentence, and {PIPELINE_NOTE[0].lower()}{PIPELINE_NOTE[1:]}"
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">How DeckTalk makes a film</title>
  <desc id="d">{desc}</desc>
  <defs><style>{chr(10).join(css)}</style></defs>
  {bg_rect(pal, w, h, background)}
  {"".join(parts)}
</svg>
"""


# ---- mark -------------------------------------------------------------------------------------


# The mark: a lit screen with the spoken word beneath it, left edges aligned, in a 32-unit square. That is the
# product's claim in two shapes: the picture sits on its word. The screen is paper on the dark stage and ink on
# the light one, the word is the voice, and the favicon adds the stage tile because a browser tab's ground is unknown.
MARK_UNIT = 32
MARK_SCREEN = (6, 7.5, 20, 11.25, 1.25)  # x, y, width, height, corner radius
MARK_WORD = (6, 21.5, 9, 3, 1.5)
MARK_TILE_R = 7


def mark_glyph(pal: dict[str, str], voice: str | None = None, picture: str | None = None) -> str:
    """The screen and the word beneath it. Colours default to the palette's picture and voice."""
    voice = voice or pal["accent"]
    picture = picture or pal["paper" if pal is DARK else "ink"]
    sx, sy, sw, sh, sr = MARK_SCREEN
    wx, wy, ww, wh, wr = MARK_WORD
    return (
        f'<rect x="{sx}" y="{sy}" width="{sw}" height="{sh}" rx="{sr}" fill="{picture}"/>'
        f'<rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" rx="{wr}" fill="{voice}"/>'
    )


def mark(pal: dict[str, str], size: int = 32, background: bool = False, mono: bool = False) -> str:
    """The mark alone. With `background`, on a rounded square of the palette's ground, for a favicon.
    With `mono`, in one colour inherited from the page, for print and single-ink uses."""
    glyph = mark_glyph(pal, "currentColor", "currentColor") if mono else mark_glyph(pal)
    u = MARK_UNIT
    # The tile is drawn in the mark's own space, so it scales with the mark at any size, and the mark keeps its
    # six-unit margin inside it, the way a home-screen icon holds its glyph.
    tile = f'\n  <rect width="{u}" height="{u}" rx="{MARK_TILE_R}" fill="{pal["bg"]}"/>' if background else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {u} {u}" role="img" aria-label="DeckTalk">{tile}
  {glyph}
</svg>
"""


# ---- narration zero ----------------------------------------------------------------------------

FRAME_W, FRAME_H, FRAME_GAP = 88, 50, 8
COVER_FRAMES = 3  # frames that are still covered before the clock starts
# The starter's open, from template/script.md: "A bowl. [beat] A ball. [beat] Watch it step down on my count."
ZERO_WORDS = ["A", "bowl.", "A", "ball.", "Watch", "it", "step", "down"]
STRIP_CUES = {1, 3}  # the two cues the bowl and the ball arrive on, which are the words this strip marks


def narration_zero(pal: dict[str, str], xs: list[float], background: bool) -> str:
    """Why the cuts are exact: the recording opens on the magenta cover, the first clean frame is
    narration t=0, and each cue is a spoken word measured from that same origin. The starter's
    reveals use data-reveal="instant", so the bowl and the ball each appear whole in one frame."""
    w, h = 1200, 250
    left = 72
    n_frames = 11
    strip_y = 58
    pitch = FRAME_W + FRAME_GAP
    t0_x = left + COVER_FRAMES * pitch
    words_y = 168
    tick_y = 178
    bowl_x = t0_x + xs[1] + 1  # the tick under "bowl."
    ball_x = t0_x + xs[3] + 1  # the tick under "ball."
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
    css.append(f".bowl{{stroke:{pal['ink']};stroke-width:2;fill:none;stroke-linejoin:round}}")
    css.append(f".ball{{fill:{pal['ink']}}}")
    css.append(f".brace{{stroke:{pal['mute']};stroke-width:1.5;fill:none}}")

    def frame_at(cue_x: float) -> int:
        """The index of the first frame whose midpoint lies past a cue, where its reveal shows."""
        return next(i for i in range(n_frames) if left + i * pitch + FRAME_W / 2 > cue_x)

    bowl_frame, ball_frame = frame_at(bowl_x), frame_at(ball_x)
    lit = {bowl_frame, ball_frame}
    # A miniature of scene 1 of the starter's deck/index.html: the bowl y = 720 - 320 u^2 and the
    # ball at its first position, u = -0.96, scaled into a frame.
    bowl_pts = [(u, 44 + 30 * u, 42 - 26 * u * u) for u in (i / 10 - 1.05 for i in range(22))]
    frames = []
    for i in range(n_frames):
        x = left + i * pitch
        if i < COVER_FRAMES:
            frames.append(f'<rect class="cover" x="{x}" y="{strip_y}" width="{FRAME_W}" height="{FRAME_H}" rx="6"/>')
            continue
        cls = "frame on" if i in lit else "frame"
        art = [f'<rect class="{cls}" x="{x}" y="{strip_y}" width="{FRAME_W}" height="{FRAME_H}" rx="6"/>']
        if i >= bowl_frame:
            d = "".join(
                f"{'L' if n else 'M'}{x + px:.1f},{strip_y + py:.1f}" for n, (_u, px, py) in enumerate(bowl_pts)
            )
            art.append(f'<path class="bowl" d="{d}"/>')
        if i >= ball_frame:
            art.append(
                f'<circle class="ball" cx="{x + 44 - 30 * 0.96:.1f}" cy="{strip_y + 42 - 26 * 0.96**2 - 6:.1f}" r="5"/>'
            )
        frames.append("".join(art))
    cover_mid = left + (COVER_FRAMES * pitch - FRAME_GAP) / 2
    words_svg = "".join(
        f'<tspan class="w {"cue" if i in STRIP_CUES else ""}" x="{t0_x + x:.1f}">{w}</tspan>'
        for i, (w, x) in enumerate(zip(ZERO_WORDS, xs, strict=True))
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
        for cx in (bowl_x, ball_x)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">Why the cuts are exact</title>
  <desc id="d">A strip of recorded frames opens on three magenta cover frames. The first clean frame is narration t=0, where the narration "A bowl. A ball. Watch it step down" starts. The bowl appears whole in the first frame after "bowl", and the ball in the first frame after "ball".</desc>
  <defs><style>{chr(10).join(css)}</style></defs>
  {bg_rect(pal, w, h, background)}
  <text class="lab" x="{left}" y="40">RECORDING</text>
  {"".join(frames)}
  <path class="brace" d="M{left},{strip_y + FRAME_H + 8} v5 H{left + COVER_FRAMES * pitch - FRAME_GAP} v-5"/>
  <text class="cap" x="{cover_mid:.1f}" y="{strip_y + FRAME_H + 30}" text-anchor="middle">magenta cover</text>
  <line class="t0" x1="{t0_x - FRAME_GAP / 2}" y1="{strip_y - 12}" x2="{t0_x - FRAME_GAP / 2}" y2="{tick_y + 18}"/>
  <text class="lab" x="{t0_x + 2}" y="{strip_y - 16}" style="fill:{pal["accent"]}">T = 0</text>
  {leads}
  <text class="lab" x="{left}" y="{words_y}">NARRATION</text>
  <text y="{words_y}">{words_svg}</text>
  {ticks_svg}
  <text class="cap" x="{left}" y="{h - 18}">The first clean frame is t=0.</text>
</svg>
"""


# ---- figure data --------------------------------------------------------------------------------

MINUS = "&#8722;"
CUE_OFFSET_KEY = 0.2  # the offset key of the worked example in docs/guides/writing-for-the-ear.mdx


def _signed(value: float, digits: int, unit: str) -> str:
    """A number with a true minus sign or a plus sign, so negative times read cleanly in the figure."""
    sign = MINUS if value < 0 else ("+" if value > 0 else "")
    return f"{sign}{abs(value):.{digits}f}{unit}"


def figure_data(name: str) -> dict:
    """The measured values for one figure, from scripts/figure-data/NAME.json.

    Each file carries a `source` block naming the release, the command and the project it was
    measured from, so a figure that prints a number says where the number came from.
    """
    path = ROOT / "scripts" / "figure-data" / f"{name}.json"
    if not path.exists():
        sys.exit(f"{path} is missing, and every figure that prints a measured number reads it.")
    return json.loads(path.read_text(encoding="utf-8"))


def fig_css(pal: dict[str, str]) -> list[str]:
    """The classes the data figures share. Faces and colors come from the palette maps only."""
    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".ln{{font:500 14px {MONO};fill:{pal['ink']}}}")
    css.append(
        f".tl{{font:500 12px {MONO};fill:{pal['mute']}}}.tl.acc{{fill:{pal['accent']}}}.tl.ink{{fill:{pal['ink']}}}"
    )
    css.append(f".val{{font:500 12px {MONO};fill:{pal['ink']}}}.val.on{{fill:{pal['on_accent']}}}")
    css.append(f".note{{font:600 13px {SANS};fill:{pal['ink']}}}.note.acc{{fill:{pal['accent']}}}")
    css.append(f".cap{{font:400 14px {SANS};fill:{pal['mute']}}}")
    css.append(f".block{{fill:{pal['block']}}}.bar{{fill:{pal['bar']}}}.accent{{fill:{pal['accent']}}}")
    css.append(f".span{{fill:{pal['block']};stroke:{pal['bar']};stroke-width:1}}")
    css.append(f".end{{fill:{pal['ink']}}}.end.on{{fill:{pal['on_accent']}}}")
    css.append(f".axis{{stroke:{pal['bar']};stroke-width:1.5}}.tk{{stroke:{pal['mute']};stroke-width:1.5}}")
    css.append(f".cue{{stroke:{pal['accent']};stroke-width:2}}.cue.ink{{stroke:{pal['ink']}}}")
    css.append(f".ref{{stroke:{pal['ink']};stroke-width:1.5;stroke-dasharray:3 3}}")
    css.append(f".guide{{stroke:{pal['hair']};stroke-width:1}}")
    css.append(f".edge{{stroke:{pal['mute']};stroke-width:1;stroke-dasharray:3 3}}")
    css.append(
        f".cell{{fill:{pal['bg']};stroke:{pal['bar']};stroke-width:1}}.cell.on{{stroke:{pal['accent']};stroke-width:2.5}}"
    )
    css.append(f".clip{{fill:url(#hatch);stroke:{pal['hair']};stroke-width:1}}")
    css.append(f".lead{{stroke:{pal['accent']};stroke-width:1.5;stroke-dasharray:4 4;fill:none}}")
    css.append(f".drop{{stroke:{pal['bar']};stroke-width:1.5;stroke-dasharray:4 4}}")
    return css


def _svg(
    w: int, h: int, title: str, desc: str, css: list[str], pal: dict[str, str], background: bool, body: str
) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">{title}</title>
  <desc id="d">{desc}</desc>
  <defs><style>{chr(10).join(css)}</style>{hatch(pal)}</defs>
  {bg_rect(pal, w, h, background)}
  {body}
</svg>
"""


# ---- verify: probes (figure A) and onset (figure B) ---------------------------------------------


def verify_probes(pal: dict[str, str], background: bool) -> str:
    """Where `decktalk verify` measures one cue: the reference frame, the probes, and the control spans."""
    d = figure_data("verify-strip")
    probes, rep = d["probes"], d["reported_probe"]
    controls = probes[rep]["controls"]
    w, h = 1200, 280
    x0, x1 = 60, 1140
    t_from = min(c["from"] for c in controls) - 0.08
    t_to = max(p["delay"] for p in probes) + 0.5

    def tx(t: float) -> float:
        return x0 + (t - t_from) / (t_to - t_from) * (x1 - x0)

    ref_t = d["reference_seconds"]
    cue_x, ref_x = tx(0.0), tx(ref_t)
    axis_y = 212
    parts: list[str] = []
    for t in [c["from"] for c in controls] + [p["delay"] for p in probes]:
        parts.append(f'<line class="guide" x1="{tx(t):.1f}" y1="78" x2="{tx(t):.1f}" y2="{axis_y}"/>')
    parts.append(f'<line class="ref" x1="{ref_x:.1f}" y1="70" x2="{ref_x:.1f}" y2="{axis_y + 6}"/>')

    ctl_y = 82
    mid = (tx(min(c["from"] for c in controls)) + tx(max(c["to"] for c in controls))) / 2
    parts.append(
        f'<text class="note" x="{mid:.1f}" y="{ctl_y - 12}" text-anchor="middle">control spans of the reported probe</text>'
    )
    for c in controls:
        xa, xb = tx(c["from"]) + 1, tx(c["to"]) - 1
        parts.append(f'<rect class="span" x="{xa:.1f}" y="{ctl_y}" width="{xb - xa:.1f}" height="22" rx="4"/>')
        parts.append(
            f'<text class="val" x="{(xa + xb) / 2:.1f}" y="{ctl_y + 15}" text-anchor="middle">{c["percent"]:.2f}%</text>'
        )
        for ex in (xa + 6, xb - 6):
            parts.append(f'<circle class="end" cx="{ex:.1f}" cy="{ctl_y + 11}" r="2.5"/>')

    for i, p in enumerate(probes):
        y = 122 + i * 34
        xb = tx(p["delay"])
        on = i == rep
        parts.append(
            f'<rect class="{"accent" if on else "bar"}" x="{ref_x:.1f}" y="{y}" width="{xb - ref_x:.1f}" height="22" rx="4"/>'
        )
        label = f"{p['changed_percent']:.2f}%, reported" if on else f"{p['changed_percent']:.2f}%"
        parts.append(
            f'<text class="val{" on" if on else ""}" x="{xb - 12:.1f}" y="{y + 15}" text-anchor="end">{label}</text>'
        )
        for ex in (ref_x + 6, xb - 6):
            parts.append(f'<circle class="end{" on" if on else ""}" cx="{ex:.1f}" cy="{y + 11}" r="2.5"/>')

    parts.append(f'<line class="axis" x1="{x0}" y1="{axis_y}" x2="{x1}" y2="{axis_y}"/>')
    parts.append(f'<line class="cue" x1="{cue_x:.1f}" y1="52" x2="{cue_x:.1f}" y2="{axis_y + 6}"/>')
    parts.append(f'<text class="note acc" x="{cue_x + 8:.1f}" y="64">cue time</text>')
    marks = [(c["from"], _signed(c["from"], 2, " s"), "middle", "") for c in controls]
    marks += [(ref_t, f"reference {_signed(ref_t, 2, ' s')}", "end", ""), (0.0, "cue", "start", " acc")]
    marks += [(p["delay"], f"probe {_signed(p['delay'], 1, ' s')}", "middle", "") for p in probes]
    for t, text, anchor, cls in marks:
        x = tx(t)
        parts.append(f'<line class="tk" x1="{x:.1f}" y1="{axis_y - 5}" x2="{x:.1f}" y2="{axis_y + 5}"/>')
        lx = x - 6 if anchor == "end" else (x + 6 if anchor == "start" else x)
        parts.append(f'<text class="tl{cls}" x="{lx:.1f}" y="{axis_y + 22}" text-anchor="{anchor}">{text}</text>')

    margins = [p["margin"] for p in probes]
    tie = len(set(margins)) < len(margins)
    rule = (
        "Both probes have the same margin, so verify reports the earlier one."
        if tie
        else "Verify reports the probe with the largest margin."
    )
    parts.append(f'<text class="cap" x="{x0}" y="{h - 18}">{rule}</text>')
    r = probes[rep]
    desc = (
        f"A time axis around cue {d['check']}. The reference frame sits {abs(ref_t):.2f} seconds before the cue. "
        + " ".join(
            f"The probe {p['delay']} seconds after the cue changed {p['changed_percent']:.2f} percent." for p in probes
        )
        + f" The reported {r['delay']} second probe has two control spans that end at the reference, and both changed "
        + f"{r['control_percent']:.2f} percent."
    )
    body = f'<text class="lab" x="{x0}" y="36">CUE {d["check"].upper()}, BUILD WITHOUT VOICE</text>' + "".join(parts)
    return _svg(w, h, "Where verify measures one cue", desc, fig_css(pal), pal, background, body)


def verify_onset(pal: dict[str, str], background: bool) -> str:
    """The onset: the changed share of each frame from the reference, with the offset limit around the cue."""
    d = figure_data("verify-strip")
    frames = d["series"][:7]
    onset, limit = d["row"]["offset_ms"], d["max_offset_ms"]
    w, h = 900, 280
    ix0, ix1 = 60, 840
    ms_from, ms_to = frames[0]["ms"] - 26, frames[-1]["ms"] + 26

    def mx(ms: float) -> float:
        return ix0 + (ms - ms_from) / (ms_to - ms_from) * (ix1 - ix0)

    parts: list[str] = []
    band_a, band_b = mx(-limit), mx(limit)
    parts.append(f'<rect class="block" x="{band_a:.1f}" y="56" width="{band_b - band_a:.1f}" height="164"/>')
    for bx in (band_a, band_b):
        parts.append(f'<line class="edge" x1="{bx:.1f}" y1="56" x2="{bx:.1f}" y2="220"/>')
    parts.append(f'<text class="tl" x="{band_a + 8:.1f}" y="72">&#177;{limit} ms offset limit</text>')
    step = frames[1]["ms"] - frames[0]["ms"]
    cell_w = mx(step) - mx(0) - 10
    top, base = 84, 200
    peak = max(f["percent"] for f in frames) or 1.0
    axis_y = 238
    for i, f in enumerate(frames):
        cx = mx(f["ms"])
        on = f["ms"] == onset
        parts.append(
            f'<rect class="cell{" on" if on else ""}" x="{cx - cell_w / 2:.1f}" y="{top}" width="{cell_w:.1f}" height="{base - top + 6}" rx="4"/>'
        )
        if i == 0:
            parts.append(f'<text class="tl" x="{cx:.1f}" y="{base - 6}" text-anchor="middle">ref</text>')
        elif f["percent"] > 0:
            bh = f["percent"] / peak * (base - top - 30)
            parts.append(
                f'<rect class="{"accent" if on else "bar"}" x="{cx - 9:.1f}" y="{base - bh:.1f}" width="18" height="{bh:.1f}" rx="2"/>'
            )
            parts.append(
                f'<text class="val" x="{cx:.1f}" y="{base - bh - 6:.1f}" text-anchor="middle">{f["percent"]:.2f}</text>'
            )
        else:
            parts.append(
                f'<text class="val" x="{cx:.1f}" y="{base - 6}" text-anchor="middle">{f["percent"]:.2f}</text>'
            )
        parts.append(
            f'<text class="tl{" acc" if on else ""}" x="{cx:.1f}" y="{axis_y}" text-anchor="middle">{_signed(f["ms"], 0, "")}</text>'
        )
        if on:
            parts.append(f'<text class="note acc" x="{cx:.1f}" y="{axis_y + 22}" text-anchor="middle">onset</text>')
    parts.append(f'<line class="cue" x1="{mx(0):.1f}" y1="56" x2="{mx(0):.1f}" y2="{top - 4}"/>')
    parts.append(f'<text class="tl acc" x="{mx(0) + 6:.1f}" y="{top - 12}">cue</text>')
    parts.append(f'<text class="tl" x="{ix1}" y="{axis_y}" text-anchor="end" dx="44">ms</text>')
    shares = ", ".join(f"{f['percent']:.2f}" for f in frames[1:])
    desc = (
        f"Seven frames, {step} milliseconds apart, from the reference frame {abs(frames[0]['ms'])} milliseconds before cue "
        f"{d['check']}. Their changed shares against the reference are {shares} percent. The frame {onset} milliseconds "
        f"after the cue is outlined as the onset, inside a band of {limit} milliseconds on each side of the cue."
    )
    body = (
        f'<text class="lab" x="{ix0}" y="36">CUE {d["check"].upper()}, CHANGED SHARE PER FRAME AT LEVEL {d["onset_diff_level"]}</text>'
        + "".join(parts)
    )
    return _svg(w, h, "How verify finds the onset", desc, fig_css(pal), pal, background, body)


# ---- narration split ----------------------------------------------------------------------------


def listed(numbers: list[str]) -> str:
    """A list of section numbers as a reader says it, with "and" before the last one."""
    return ", ".join(numbers[:-1]) + (" and " if len(numbers) > 1 else "") + numbers[-1]


def split_footer(d: dict, tx: Callable[[float], float], cap_y: int, chap_y: int, span: tuple[int, int]) -> list[str]:
    """The caption bars, the chapter marks and the time axis under the two tracks."""
    x0, x1 = span
    secs = d["sections"]
    parts: list[str] = []
    for a, b in d["captions"]:
        parts.append(
            f'<rect class="cp" x="{tx(a) + 0.6:.1f}" y="{cap_y}" width="{max(tx(b) - tx(a) - 1.2, 0.8):.1f}" height="12" rx="2"/>'
        )
    for n, t in enumerate(d["chapters"]):
        x = tx(t)
        parts.append(f'<line class="ch" x1="{x:.1f}" y1="{chap_y}" x2="{x:.1f}" y2="{chap_y + 16}"/>')
        # A chapter starts with a section, and consecutive sections with the same title share one chapter.
        num = next((s["number"] for s in secs if abs(s["video_start"] - t) < 0.01), n + 1)
        parts.append(f'<text class="tl ink" x="{x + 4:.1f}" y="{chap_y + 13}">{num}</text>')
    axis_y = chap_y + 36
    parts.append(f'<line class="axis" x1="{x0}" y1="{axis_y}" x2="{x1}" y2="{axis_y}"/>')
    t = 0
    while t <= d["video_seconds"]:
        x = tx(t)
        parts.append(f'<line class="tk" x1="{x:.1f}" y1="{axis_y - 4}" x2="{x:.1f}" y2="{axis_y + 4}"/>')
        parts.append(
            f'<text class="tl" x="{x:.1f}" y="{axis_y + 20}" text-anchor="middle">{t // 60}:{t % 60:02d}</text>'
        )
        t += 30
    return parts


def split_desc(secs: list[dict], moved: list[dict]) -> str:
    """What a reader who cannot see the figure is told, which is every claim the picture makes."""
    clips = [s for s in secs if s["clip"]]
    if not (clips and moved):
        return "The narration track and the video, section by section."
    plays = " ".join(
        f"The video plays section {c['number']}, a clip of {c['video_end'] - c['video_start']:.2f} seconds, after section {c['number'] - 1}."
        for c in clips
    )
    return (
        f"The narration track holds sections {listed([str(s['number']) for s in secs if not s['clip']])} back to back. "
        f"{plays}"
        f" The narration splits before each page section that follows a clip, and sections {listed([str(s['number']) for s in moved])} start later in the video. "
        "Captions sit under page sections only. Each chapter starts with a section, and consecutive sections with the same title share one."
    )


def narration_split(pal: dict[str, str], background: bool) -> str:
    """A clip between page sections pauses the narration: the track splits, and the later part moves right."""
    d = figure_data("narration-split")
    secs = d["sections"]
    w, h = 1200, 340
    x0, x1 = 190, 1140
    scale = (x1 - x0) / d["video_seconds"]

    def tx(t: float) -> float:
        return x0 + t * scale

    css = fig_css(pal)
    css.append(
        f".sec{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}.num{{font:600 14px {SANS};fill:{pal['ink']}}}.num.on{{fill:{pal['on_accent']}}}"
    )
    css.append(f".cp{{fill:{pal['bar']}}}.ch{{stroke:{pal['ink']};stroke-width:2}}")
    narr_y, vid_y, cap_y, chap_y, bh = 64, 164, 236, 268, 40
    parts: list[str] = [
        '<text class="lab" x="60" y="30">A STARTER WITH A CLIP SECTION, BUILD WITHOUT VOICE, TO SCALE</text>'
    ]
    for label, y in (
        ("narration.mp3", narr_y + 25),
        ("video", vid_y + 25),
        ("captions", cap_y + 9),
        ("chapters", chap_y + 11),
    ):
        parts.append(f'<text class="ln" x="60" y="{y}">{label}</text>')

    def block(a: float, b: float, y: int, n: int, moved: bool) -> None:
        xa, xb = tx(a) + 1, tx(b) - 1
        parts.append(
            f'<rect class="{"accent" if moved else "sec"}" x="{xa:.1f}" y="{y}" width="{xb - xa:.1f}" height="{bh}" rx="6"/>'
        )
        parts.append(
            f'<text class="num{" on" if moved else ""}" x="{(xa + xb) / 2:.1f}" y="{y + 25}" text-anchor="middle">{n}</text>'
        )

    # The narration splits wherever a page section starts later in the video than the one before it, which is
    # after each clip between page sections. Each run of sections with one offset gets a lead at both ends.
    runs: list[list[dict]] = []
    for s in secs:
        if s["clip"]:
            xa, xb = tx(s["video_start"]) + 1, tx(s["video_end"]) - 1
            parts.append(f'<rect class="clip" x="{xa:.1f}" y="{vid_y}" width="{xb - xa:.1f}" height="{bh}" rx="4"/>')
            parts.append(
                f'<text class="note" x="{(xa + xb) / 2:.1f}" y="{vid_y + bh + 18}" text-anchor="middle">{s["number"]} clip</text>'
            )
            continue
        moved = s["offset"] > 0
        if runs and abs(runs[-1][0]["offset"] - s["offset"]) < 1e-6:
            runs[-1].append(s)
        else:
            runs.append([s])
        block(s["narration_start"], s["narration_end"], narr_y, s["number"], moved)
        block(s["video_start"], s["video_end"], vid_y, s["number"], moved)
    moved = [s for s in secs if not s["clip"] and s["offset"] > 0]
    for run in runs:
        if run[0]["offset"] <= 0:
            continue
        first, last = run[0], run[-1]
        for na, va in ((first["narration_start"], first["video_start"]), (last["narration_end"], last["video_end"])):
            parts.append(
                f'<line class="lead" x1="{tx(na):.1f}" y1="{narr_y + bh + 2}" x2="{tx(va):.1f}" y2="{vid_y - 2}"/>'
            )
        sx = tx(first["narration_start"])
        parts.append(f'<line class="cue" x1="{sx:.1f}" y1="{narr_y - 12}" x2="{sx:.1f}" y2="{narr_y + bh + 6}"/>')
        parts.append(f'<text class="note acc" x="{sx:.1f}" y="{narr_y - 18}" text-anchor="middle">split</text>')
    parts += split_footer(d, tx, cap_y, chap_y, (x0, x1))
    desc = split_desc(secs, moved)
    return _svg(w, h, "The narration pauses for a clip", desc, css, pal, background, "".join(parts))


# ---- duck lane ----------------------------------------------------------------------------------


def duck_spans(d: dict, tx: Callable[[float], float], span_y: int, window: tuple[float, float]) -> list[str]:
    """One bar per span the music ducks under, each saying whether it is a spoken span or a whole clip."""
    a, b = window
    clip_keys = {(s["video_start"], s["video_end"]) for s in d["sections"] if s["clip"]}
    parts: list[str] = []
    for sa, sb in d["spans"]:
        xa, xb = tx(max(sa, a)) + 1, tx(min(sb, b)) - 1
        is_clip = any(abs(sa - p) < 1e-6 and abs(sb - q) < 1e-6 for p, q in clip_keys)
        parts.append(f'<rect class="bar" x="{xa:.1f}" y="{span_y}" width="{xb - xa:.1f}" height="22" rx="4"/>')
        parts.append(
            f'<text class="val" x="{(xa + xb) / 2:.1f}" y="{span_y + 15}" text-anchor="middle">{"the whole clip" if is_clip else "spoken span"}</text>'
        )
    return parts


def duck_desc(d: dict, window: tuple[float, float], base: float, duck: float) -> str:
    """What a reader who cannot see the figure is told, which is every claim the curve makes."""
    a, b = window
    s3, clip, s5 = d["sections"]
    return (
        f"The music level from {a // 60:.0f}:{a % 60:02.0f} to {b // 60:.0f}:{b % 60:02.0f} of the video, across the end of section {s3['number']}, "
        f"the clip in section {clip['number']}, and the start of section {s5['number']}. The level sits at {base:.0f} dB, drops to "
        f"{base + duck:.0f} dB under each spoken span and under the whole clip, and comes back up in the short silence after the last word "
        f"of section {s3['number']}."
    )


def duck_lane(pal: dict[str, str], background: bool) -> str:
    """The music level around a clip, from [mix] and the spans that plan_mix() ducks under."""
    d = figure_data("duck-lane")
    a, b = d["window"]
    base, duck, ramp = d["music_db"], d["music_duck_db"], d["duck_ramp_seconds"]
    w, h = 1200, 320
    x0, x1 = 260, 1140

    def tx(t: float) -> float:
        return x0 + (t - a) / (b - a) * (x1 - x0)

    def level(t: float) -> float:
        f = max((min(1, max(0, (t - s) / ramp)) * min(1, max(0, (e - t) / ramp)) for s, e in d["spans"]), default=0.0)
        return 20 * math.log10(10 ** (base / 20) * (1 - (1 - 10 ** (duck / 20)) * f))

    hi, lo = base + 2, base + duck - 2
    lvl_top, lvl_h = 150, 96

    def ly(db_: float) -> float:
        return lvl_top + (hi - db_) / (hi - lo) * lvl_h

    css = fig_css(pal)
    css.append(f".sec{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}")
    css.append(f".grid{{stroke:{pal['hair']};stroke-width:1.5;stroke-dasharray:4 4}}")
    css.append(f".curve{{stroke:{pal['accent']};stroke-width:3;fill:none;stroke-linejoin:round}}")
    sec_y, span_y = 56, 104
    parts = [f'<clipPath id="win"><rect x="{x0}" y="0" width="{x1 - x0}" height="{h}"/></clipPath>']
    parts.append('<text class="lab" x="60" y="36">MUSIC LEVEL AROUND A CLIP, A STARTER\u2019S [MIX] SETTINGS</text>')
    parts.append(f'<text class="ln" x="60" y="{sec_y + 23}">section</text>')
    parts.append(f'<text class="ln" x="60" y="{span_y + 17}">ducked</text>')
    parts.append(f'<text class="ln" x="60" y="{ly(base) + 5:.1f}">music</text>')
    clipped = []
    for s in d["sections"]:
        sa, sb = max(s["video_start"], a), min(s["video_end"], b)
        xa, xb = tx(sa) + 1, tx(sb) - 1
        cls = "clip" if s["clip"] else "sec"
        clipped.append(f'<rect class="{cls}" x="{xa:.1f}" y="{sec_y}" width="{xb - xa:.1f}" height="36" rx="6"/>')
        label = f"{s['number']} clip" if s["clip"] else f"{s['number']} {s['chapter']}"
        tx_anchor = (xa + xb) / 2
        clipped.append(
            f'<rect class="block" x="{tx_anchor - 7 * len(label) / 2 - 8:.1f}" y="{sec_y + 8}" width="{7 * len(label) + 16:.1f}" height="20" rx="4"/>'
            if s["clip"]
            else ""
        )
        clipped.append(f'<text class="note" x="{tx_anchor:.1f}" y="{sec_y + 23}" text-anchor="middle">{label}</text>')
    clipped += duck_spans(d, tx, span_y, (a, b))
    for db_ in (base, base + duck):
        y = ly(db_)
        clipped.append(f'<line class="grid" x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}"/>')
    n = 1100
    pts = " ".join(f"{tx(a + (b - a) * i / n):.1f},{ly(level(a + (b - a) * i / n)):.1f}" for i in range(n + 1))
    clipped.append(f'<polyline class="curve" points="{pts}"/>')
    parts.append(f'<g clip-path="url(#win)">{"".join(clipped)}</g>')
    for db_ in (base, base + duck):
        parts.append(
            f'<text class="tl" x="{x0 - 10}" y="{ly(db_) + 4:.1f}" text-anchor="end">{_signed(db_, 0, " dB")}</text>'
        )
    axis_y = lvl_top + lvl_h + 26
    parts.append(f'<line class="axis" x1="{x0}" y1="{axis_y}" x2="{x1}" y2="{axis_y}"/>')
    for t in range(math.ceil(a), math.floor(b) + 1):
        x = tx(t)
        parts.append(f'<line class="tk" x1="{x:.1f}" y1="{axis_y - 4}" x2="{x:.1f}" y2="{axis_y + 4}"/>')
        parts.append(
            f'<text class="tl" x="{x:.1f}" y="{axis_y + 20}" text-anchor="middle">{t // 60}:{t % 60:02d}</text>'
        )
    desc = duck_desc(d, (a, b), base, duck)
    return _svg(w, h, "How the music ducks", desc, css, pal, background, "".join(parts))


# ---- cue offset ---------------------------------------------------------------------------------


def cue_offset(pal: dict[str, str], background: bool) -> str:
    """A cue time is the start of its word plus the offset key."""
    d = figure_data("cue-offset")
    words = d["words"]
    cued = words[1]
    w, h = 1200, 250
    x0, x1 = 60, 1140
    a, b = words[0]["start"] - 0.45, words[-1]["end"] + 0.15

    def tx(t: float) -> float:
        return x0 + (t - a) / (b - a) * (x1 - x0)

    css = fig_css(pal)
    css.append(f".word{{font:600 22px {SANS};fill:{pal['ink']}}}.word.acc{{fill:{pal['accent']}}}")
    css.append(
        f".wb{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}.wb.on{{stroke:{pal['accent']};stroke-width:2}}"
    )
    css.append(f".arrow{{stroke:{pal['ink']};stroke-width:2;fill:none;stroke-linecap:round;stroke-linejoin:round}}")
    word_y = 150
    parts: list[str] = []
    for i, wd in enumerate(words):
        xa, xb = tx(wd["start"]) + 1, tx(wd["end"]) - 1
        on = i == 1
        parts.append(
            f'<rect class="wb{" on" if on else ""}" x="{xa:.1f}" y="{word_y}" width="{xb - xa:.1f}" height="48" rx="8"/>'
        )
        parts.append(
            f'<text class="word{" acc" if on else ""}" x="{(xa + xb) / 2:.1f}" y="{word_y + 32}" text-anchor="middle">{wd["word"]}</text>'
        )
    ws = tx(cued["start"])
    pos = tx(cued["start"] + CUE_OFFSET_KEY)
    parts.append(f'<line class="cue ink" x1="{ws:.1f}" y1="52" x2="{ws:.1f}" y2="{word_y + 60}"/>')
    parts.append(f'<text class="tl ink" x="{ws:.1f}" y="{word_y + 78}" text-anchor="middle">word start</text>')
    parts.append(f'<text class="note" x="{ws - 8:.1f}" y="64" text-anchor="end">offset key 0</text>')
    parts.append(f'<line class="cue" x1="{pos:.1f}" y1="52" x2="{pos:.1f}" y2="{word_y - 4}"/>')
    parts.append(f'<text class="note acc" x="{pos + 8:.1f}" y="64">offset key {CUE_OFFSET_KEY}</text>')
    parts.append(f'<text class="tl" x="{pos + 8:.1f}" y="82">after the word starts</text>')
    by = 120
    parts.append(f'<path class="edge" d="M{ws:.1f},{by} H{pos:.1f}"/>')
    parts.append(
        f'<text class="tl acc" x="{(ws + pos) / 2:.1f}" y="{by - 6}" text-anchor="middle">{_signed(CUE_OFFSET_KEY, 1, " s")}</text>'
    )
    ay = 104
    tip = ws - 150
    parts.append(
        f'<path class="arrow" d="M{ws - 6:.1f},{ay} H{tip:.1f} M{tip + 8:.1f},{ay - 6} L{tip:.1f},{ay} L{tip + 8:.1f},{ay + 6}"/>'
    )
    parts.append(f'<text class="note" x="{tip - 10:.1f}" y="{ay - 4}" text-anchor="end">negative offset key</text>')
    parts.append(f'<text class="tl" x="{tip - 10:.1f}" y="{ay + 14}" text-anchor="end">for text read aloud</text>')
    desc = (
        f'The spoken words "{words[0]["word"]} {cued["word"]} {words[2]["word"]}" from section {d["section"]}, each drawn as long as it is spoken. '
        f'With offset key 0, the cue time is the start of "{cued["word"]}". Offset key {CUE_OFFSET_KEY} moves the cue time '
        f"{CUE_OFFSET_KEY} seconds later, after the word starts. A negative offset key moves it earlier, for text the audience reads aloud."
    )
    body = f'<text class="lab" x="{x0}" y="36">CUE TIME = WORD START + OFFSET KEY</text>' + "".join(parts)
    return _svg(w, h, "The offset key moves a cue time", desc, css, pal, background, body)


# ---- rebuild lanes ----------------------------------------------------------------------------

EDITED_SECTION = 1  # the starter's own edit: one word changes in section 1
LANES = (
    # lane, the page sections that run in it, the text on those, the text on the other page sections
    ("narrate", {EDITED_SECTION}, "voiced", "cached"),
    ("record, plain build", None, "recorded", ""),  # None: every page section
    (f"record, --section {EDITED_SECTION}", {EDITED_SECTION}, "recorded", "kept"),
)


def starter_sections() -> list[tuple[int, str, bool]]:
    """(number, chapter, is a clip) for each [[section]] of the starter that `decktalk init` writes.

    A section that sets no `chapter` takes the script's own "## N." heading, as DeckTalk does.
    """
    starter = ROOT / "src" / "decktalk" / "template" / "starter"
    doc = tomllib.loads((starter / "decktalk.toml").read_text(encoding="utf-8"))
    headings = {
        int(m.group(1)): m.group(2).strip()
        for m in re.finditer(r"^##\s+(\d+)\.\s+(.+)$", (starter / "script.md").read_text(encoding="utf-8"), re.M)
    }
    return [(s["number"], s.get("chapter") or headings[s["number"]], "clip" in s) for s in doc["section"]]


def hatch(pal: dict[str, str], pid: str = "hatch") -> str:
    """A diagonal hatch for a clip, which DeckTalk neither voices nor records."""
    return (
        f'<pattern id="{pid}" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="8" height="8" fill="{pal["block"]}"/><line x1="0" y1="0" x2="0" y2="8" stroke="{pal["bar"]}" stroke-width="2"/></pattern>'
    )


def rebuild_lanes(pal: dict[str, str], background: bool) -> str:
    """What runs again after section 1 is edited, in narration and in the two kinds of build."""
    sections = starter_sections()
    w, h = 1200, 332
    left, col0, gap = 60, 244, 10
    col_w = (w - left - col0 + gap) / len(sections)
    cell_w = col_w - gap
    css = [font_face()]
    css.append(f".lab{{font:500 12px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".ti{{font:600 14px {SANS};fill:{pal['ink']}}}")
    css.append(f".ln{{font:500 14px {MONO};fill:{pal['ink']}}}")
    css.append(f".ct{{font:500 13px {MONO};fill:{pal['mute']}}}.ct.on{{fill:{pal['on_accent']}}}")
    css.append(f".cell{{fill:{pal['block']};stroke:{pal['hair']};stroke-width:1}}.accent{{fill:{pal['accent']}}}")
    css.append(f".clip{{fill:url(#hatch);stroke:{pal['hair']};stroke-width:1}}")
    css.append(f".ct.cl{{fill:{pal['ink']}}}.chip{{fill:{pal['bg']}}}")
    rows = [f'<text class="lab" x="{left}" y="40">LANE</text>']
    for n, (number, title, _clip) in enumerate(sections):
        x = col0 + n * col_w
        rows.append(f'<text class="lab" x="{x + 2:.1f}" y="40">SECTION {number}</text>')
        rows.append(f'<text class="ti" x="{x + 2:.1f}" y="62">{title}</text>')
    top, pitch = 82, 58
    for i, (name, lit, on_text, off_text) in enumerate(LANES):
        y = top + i * pitch
        rows.append(f'<text class="ln" x="{left}" y="{y + 26}">{name}</text>')
        for n, (number, _title, clip) in enumerate(sections):
            x = col0 + n * col_w
            if clip:
                rows.append(f'<rect class="clip" x="{x:.1f}" y="{y}" width="{cell_w:.1f}" height="40" rx="8"/>')
                cx = x + cell_w / 2
                rows.append(f'<rect class="chip" x="{cx - 22:.1f}" y="{y + 10}" width="44" height="20" rx="4"/>')
                rows.append(f'<text class="ct cl" x="{cx:.1f}" y="{y + 25}" text-anchor="middle">clip</text>')
                continue
            on = lit is None or number in lit
            rows.append(
                f'<rect class="{"accent" if on else "cell"}" x="{x:.1f}" y="{y}" width="{cell_w:.1f}" height="40" rx="8"/>'
            )
            text = on_text if on else off_text
            if text:
                rows.append(
                    f'<text class="ct{" on" if on else ""}" x="{x + cell_w / 2:.1f}" y="{y + 25}" text-anchor="middle">{text}</text>'
                )
    y = top + len(LANES) * pitch + 10
    rows.append(f'<text class="ln" x="{left}" y="{y + 26}">assemble</text>')
    rows.append(
        f'<rect class="accent" x="{col0}" y="{y}" width="{(len(sections) - 1) * col_w + cell_w:.1f}" height="40" rx="8"/>'
    )
    rows.append(
        f'<text class="ct on" x="{col0 + 16}" y="{y + 25}">Every section is cut and joined into one mp4.</text>'
    )
    pages = [n for n, _t, clip in sections if not clip]
    clips = [n for n, _t, clip in sections if clip]
    others = ", ".join(str(n) for n in pages if n != EDITED_SECTION)
    names = [str(n) for n in clips]
    if not names:
        clip_text = "Every section is a page section"
    elif len(names) == 1:
        clip_text = f"Section {names[0]} is a clip"
    else:
        clip_text = f"Sections {', '.join(names[:-1])} and {names[-1]} are clips"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">What runs again after an edit to section {EDITED_SECTION}</title>
  <desc id="d">{len(sections)} section columns and three lanes. {clip_text} in every lane. In the narrate lane, section {EDITED_SECTION} is voiced, and sections {others} are cached. A plain build records every page section. A build with --section {EDITED_SECTION} records section {EDITED_SECTION} and keeps the other recordings. The assemble bar spans every section.</desc>
  <defs><style>{chr(10).join(css)}</style>{hatch(pal)}</defs>
  {bg_rect(pal, w, h, background)}
  {"".join(rows)}
</svg>
"""


# ---- wordmark ---------------------------------------------------------------------------------


LOCKUP_H = 32  # the lockup is drawn 32 units tall, the mark 24 of them, and scaled to the height asked for


def wordmark(pal: dict[str, str], name: tuple[str, float], height: int = LOCKUP_H, mono: bool = False) -> str:
    """The mark and the name, for the docs navbar and the lockups. The name is outline paths, so no font ships.
    The mark stands 24 units tall with its screen at the left edge, and the name starts 11 units after it."""
    d, width = name
    scale = 24 / MARK_UNIT
    text_x = MARK_SCREEN[2] * scale + 11
    w, s = text_x + width, height / LOCKUP_H
    glyph = mark_glyph(pal, "currentColor", "currentColor") if mono else mark_glyph(pal)
    ink = "currentColor" if mono else pal["ink"]
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w * s:.2f}" height="{height}" viewBox="0 0 {w:.2f} {LOCKUP_H}" role="img" aria-label="DeckTalk">
  <g transform="translate({-MARK_SCREEN[0] * scale} 4) scale({scale})">{glyph}</g>
  <path transform="translate({text_x} 24)" fill="{ink}" d="{d}"/>
</svg>
"""


# ---- social card -------------------------------------------------------------------------------


OG_HEADLINE = ("Every picture", "lands on its word.")
OG_KICKER = "Narrated video from a script, in your own voice."
# The card's line is the one the homepage follows through how-it-works, from Halfway's section 2. Each
# cue phrase is set in the voice colour, with a dot on the word its picture lands on.
OG_WORDS = ["Twenty", "minutes", "for", "her.", "Twenty", "minutes", "for", "you."]
OG_CUES = {0, 1, 2, 3, 4, 5, 6, 7}  # both phrases are cue phrases, 2.1her and 2.1you
OG_DOTS = {0, 4}  # the pictures land on the first word of each phrase
# The frame in the card's right third: Halfway's scene 2 once both routes are drawn, the picture the
# card's line lands on. Rendered losslessly from the deck by `decktalk storyboard --section 2 --at 15`
# in the Halfway project, then scaled to 640 by 360 as lossless WebP. Never a frame of the mp4.
OG_FRAME = ASSETS / "halfway-frame.webp"
OG_FRAME_BOX = (780, 196, 340, 191)  # x, y, width, height: beside the headline, on the site's right margin


def og(pal: dict[str, str], xs: list[float]) -> str:
    """The 1200 by 630 card that link previews show: the wordmark, the site's name, the headline, a frame of Halfway
    beside it, and the Halfway line the homepage follows, with a tick under each word and the cue phrases in the
    voice colour."""
    w, h = 1200, 630
    if not OG_FRAME.exists():
        sys.exit(f"{OG_FRAME} is missing. See the comment above OG_FRAME for how it was made.")
    frame = base64.b64encode(OG_FRAME.read_bytes()).decode()
    fx, fy, fw, fh = OG_FRAME_BOX
    css = [font_face(title=True, display=True)]
    css.append(f".shot{{fill:none;stroke:{pal['tick']};stroke-width:2}}")
    css.append(f".bg{{fill:{pal['bg']}}}")
    css.append(f".lab{{font:500 13px {MONO};fill:{pal['mute']};letter-spacing:.14em}}")
    css.append(f".w{{font:600 40px {SANS};letter-spacing:-.01em;fill:{pal['text2']}}}.cue{{fill:{pal['accent']}}}")
    css.append(f".tick{{stroke:{pal['tick']};stroke-width:3;stroke-linecap:round}}.tick.on{{stroke:{pal['accent']}}}")
    css.append(f".dot{{fill:{pal['accent']}}}.head{{fill:{pal['ink']}}}")
    css.append(f".title{{font:500 30px {DISPLAY};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".h{{font:400 92px {TITLE};letter-spacing:-.01em;fill:{pal['ink']}}}")
    css.append(f".tag{{font:400 28px {SANS};fill:{pal['text2']}}}")
    css.append(f".site{{font:400 22px {MONO};fill:{pal['text2']}}}")
    scale = 40 / MEASURE_PX
    words = "".join(
        f'<tspan class="w {"cue" if i in OG_CUES else ""}" x="{x * scale:.1f}">{t}</tspan>'
        for i, (t, x) in enumerate(zip(OG_WORDS, xs, strict=True))
    )
    ticks = "".join(
        f'<line class="tick {"on" if i in OG_DOTS else ""}" x1="{x * scale + 1:.1f}" y1="0" x2="{x * scale + 1:.1f}" y2="22"/>'
        for i, x in enumerate(xs)
    )
    dots = "".join(f'<circle class="dot" cx="{xs[i] * scale + 1:.1f}" cy="-8" r="5"/>' for i in sorted(OG_DOTS))
    # The playhead sits on the last tick: the card is the frame where the last picture lands, and gold means now.
    head_x = xs[-1] * scale + 1 - 1.5
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="DeckTalk">
  <defs><style>{chr(10).join(css)}</style></defs>
  <rect class="bg" width="{w}" height="{h}"/>
  <g transform="translate(76 72) scale(1.125)">{mark_glyph(pal)}</g>
  <text class="title" x="130" y="107">DeckTalk</text>
  <text class="site" x="{w - 80}" y="107" text-anchor="end">decktalk.ai</text>
  <text class="h" x="76" y="270">{OG_HEADLINE[0]}</text>
  <text class="h" x="76" y="368">{OG_HEADLINE[1]}</text>
  <text class="tag" x="80" y="436">{OG_KICKER}</text>
  <clipPath id="shot"><rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" rx="10"/></clipPath>
  <image x="{fx}" y="{fy}" width="{fw}" height="{fh}" clip-path="url(#shot)" preserveAspectRatio="xMidYMid slice" href="data:image/webp;base64,{frame}"/>
  <rect class="shot" x="{fx + 1}" y="{fy + 1}" width="{fw - 2}" height="{fh - 2}" rx="9"/>
  <g transform="translate(80 540)">
    <text y="0">{words}</text>
    <g transform="translate(0 30)">{ticks}{dots}<rect class="head" x="{head_x:.1f}" y="-16" width="3" height="46"/></g>
  </g>
</svg>
"""


# ---- tokens ------------------------------------------------------------------------------------

TYPE_TOKENS = """\
  --dt-font-title: "Instrument Serif", Georgia, "Times New Roman", serif;
  --dt-font-display: "Bricolage Grotesque", "Avenir Next", "Helvetica Neue", system-ui, sans-serif;
  --dt-font-text: "Instrument Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --dt-font-mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  /* A 1.25 scale from 16, rounded to whole pixels. */
  --dt-size-xs: 12px;
  --dt-size-sm: 14px;
  --dt-size-md: 16px;
  --dt-size-lg: 18px;
  --dt-size-xl: 22px;
  --dt-size-2xl: 28px;
  --dt-size-3xl: 40px;
  --dt-size-4xl: 56px;
  --dt-size-5xl: 80px;
  --dt-leading-tight: 1;
  --dt-leading-snug: 1.15;
  --dt-leading-body: 1.5;
  --dt-tracking-title: -0.01em;
  --dt-tracking-display: -0.025em;
  --dt-tracking-eyebrow: 0.08em;
  /* Motion: the runtime's own reveal, a 10 px rise over 0.3 s, and a plain fade. */
  --dt-ease-out: cubic-bezier(0.2, 0.7, 0.2, 1);
  --dt-release: 300ms;
  --dt-rise: 10px;
"""


def colour_tokens(pal: dict[str, str]) -> str:
    """The palette as custom properties. Gold is the voice: it marks the word being spoken, a waveform
    while sound plays, a live cue and the section being rebuilt, and never a button or a picture."""
    rows = (
        ("bg", pal["bg"]),
        ("surface", pal["block"]),
        ("surface-2", pal["bar"] if pal is LIGHT else "#2a2320"),
        ("border", pal["hair"]),
        ("focus", pal["focus"]),
        ("text", pal["ink"]),
        ("text-2", pal["text2"]),
        ("text-3", pal["mute"]),
        ("text-dim", pal["dim"]),
        ("voice", pal["accent"]),
        ("voice-ink", pal["voice_ink"]),
        ("voice-soft", pal["voice_soft"]),
        ("paper", pal["paper"]),
        ("paper-ink", "#1b1511"),
        ("mark-picture", pal["paper"] if pal is DARK else pal["ink"]),
        ("ok", pal["ok"]),
        ("warn", pal["warn"]),
    )
    return "".join(f"  --dt-{name}: {value};\n" for name, value in rows)


def tokens_css() -> str:
    """site/tokens.css: the brand's colour, type and motion tokens.

    Dark is the default and, at launch, the only theme. The paper theme is written so that it can
    ship later under data-theme="light".
    """
    return f"""/* DeckTalk brand tokens. Generated by scripts/build_assets.py from its palette maps; edit there. */
:root {{
{colour_tokens(DARK)}{TYPE_TOKENS}  color-scheme: dark;
}}
:root[data-theme="light"] {{
{colour_tokens(LIGHT)}  color-scheme: light;
}}
"""


def render_png(svg: str, target: Path, width: int, height: int) -> None:
    """Rasterize an SVG with Chromium, for the places that cannot show SVG such as link previews."""
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
    h_widths, h_space = measure_words(HERO_WORDS, f"600 {MEASURE_PX}px {SANS}", "-.01em")
    hero_xs: list[float] = []
    x = 0.0
    for i, w in enumerate(h_widths):
        hero_xs.append(round(x, 1))
        x += w + h_space * (1.6 if HERO_WORDS[i].endswith((",", ".")) else 1.0)
    a_widths, a_space = measure_words(ZERO_WORDS, f"600 {MEASURE_PX}px {SANS}", "-.01em")
    zero_xs: list[float] = []
    x = 0.0
    for i, w in enumerate(a_widths):
        zero_xs.append(round(x, 1))
        x += w + a_space * (1.6 if ZERO_WORDS[i].endswith((",", ".")) else 1.0)
    o_widths, o_space = measure_words(OG_WORDS, f"600 {MEASURE_PX}px {SANS}", "-.01em")
    og_xs: list[float] = []
    x = 0.0
    for i, w in enumerate(o_widths):
        og_xs.append(round(x, 1))
        x += w + o_space * (1.6 if OG_WORDS[i].endswith((",", ".")) else 1.0)
    ticks = narrate_ticks(hero_xs)
    name = glyph_outlines("DeckTalk", size=22, tracking=-0.01)
    out: dict[Path, str] = {}
    docs = ROOT / "docs"
    site = ROOT / "site"
    for variant, pal in (("light", LIGHT), ("dark", DARK)):
        for background, folder in ((False, ASSETS), (True, docs / "images")):
            out[folder / f"hero-{variant}.svg"] = hero(pal, [x * HERO_PX / MEASURE_PX for x in hero_xs], background)
            out[folder / f"how-it-works-{variant}.svg"] = how_it_works(
                pal, stacked=False, background=background, ticks=ticks
            )
            out[folder / f"narration-zero-{variant}.svg"] = narration_zero(
                pal, [x * 26 / MEASURE_PX for x in zero_xs], background
            )
        out[ASSETS / f"how-it-works-{variant}-stacked.svg"] = how_it_works(
            pal, stacked=True, background=False, ticks=ticks
        )
        out[docs / "images" / f"how-it-works-{variant}-stacked.svg"] = how_it_works(
            pal, stacked=True, background=True, ticks=ticks
        )
        out[docs / "images" / f"verify-probes-{variant}.svg"] = verify_probes(pal, background=True)
        out[docs / "images" / f"verify-onset-{variant}.svg"] = verify_onset(pal, background=True)
        out[docs / "images" / f"narration-split-{variant}.svg"] = narration_split(pal, background=True)
        out[docs / "images" / f"duck-lane-{variant}.svg"] = duck_lane(pal, background=True)
        out[docs / "images" / f"cue-offset-{variant}.svg"] = cue_offset(pal, background=True)
        out[docs / "images" / f"rebuild-lanes-{variant}.svg"] = rebuild_lanes(pal, background=True)
        out[docs / "logo" / f"{variant}.svg"] = wordmark(pal, name)
        for background, folder in ((False, ASSETS), (True, docs / "images")):
            out[folder / f"pipeline-{variant}.svg"] = pipeline(pal, background)
    # The mark set: the mark alone at 32, the nav lockup at 24 and the hero lockup at 96, each for the dark
    # stage, for paper (-light) and in one inherited colour (-mono).
    marks = ASSETS / "mark"
    out[marks / "mark-32.svg"] = mark(DARK, size=32)
    out[marks / "mark-32-light.svg"] = mark(LIGHT, size=32)
    out[marks / "mark-32-mono.svg"] = mark(DARK, size=32, mono=True)
    for label, height in (("nav", 24), ("hero", 96)):
        out[marks / f"lockup-{label}-{height}.svg"] = wordmark(DARK, name, height)
        out[marks / f"lockup-{label}-{height}-light.svg"] = wordmark(LIGHT, name, height)
        out[marks / f"lockup-{label}-{height}-mono.svg"] = wordmark(DARK, name, height, mono=True)
    # The favicon sits on the dark ground in both themes, so the gold beats read on any tab.
    for target in (docs / "favicon.svg", site / "favicon.svg"):
        out[target] = mark(DARK, size=32, background=True)
    out[ASSETS / "og.svg"] = og(DARK, og_xs)
    out[site / "tokens.css"] = tokens_css()
    return {k: _clean(v) for k, v in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if any generated file would change")
    args = ap.parse_args()
    files = build()
    changed = [p for p, s in files.items() if not p.exists() or p.read_text(encoding="utf-8") != s]
    if args.check:
        for p in changed:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if changed else 0
    for p, s in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(s, encoding="utf-8")
        print(f"wrote {p.relative_to(ROOT)}  ({len(s) // 1024} KB)")
    # The social card and the favicons are also needed as PNGs. They are not part of --check because
    # raster bytes vary between Chromium builds, so they are only refreshed when their SVG source was rewritten.
    if ASSETS / "og.svg" in changed or not (ROOT / "site" / "og.png").exists():
        for target in (ROOT / "site" / "og.png", ROOT / "docs" / "images" / "og.png"):
            render_png(files[ASSETS / "og.svg"], target, 1200, 630)
            print(f"wrote {target.relative_to(ROOT)}")
    favicon = ROOT / "site" / "favicon.svg"
    if favicon in changed or not (ROOT / "site" / "apple-touch-icon.png").exists():
        for name, size in (("favicon-32.png", 32), ("favicon-192.png", 192), ("apple-touch-icon.png", 180)):
            render_png(mark(DARK, size=size, background=True), ROOT / "site" / name, size, size)
            print(f"wrote site/{name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
