"""What `scripts/build_assets.py --check` calls stale, which is the one judgement in the generator.

The generator draws the figures from constants and from one measurement: a word's advance width in
Chromium with the repository's own fonts. Chromium shapes that word through CoreText on macOS and
through FreeType on Linux, so the author's machine and the Linux runner disagree about it by a
fraction of a pixel per word. A byte comparison turns that disagreement into seventeen stale files
on every run, and the author regenerates the figures on his own machine, so the comparison is what
has to give rather than the figures.

Nothing here draws a figure or starts a browser, because the judgement is a pure function of two
strings. The fixtures below are the shape of a real figure rather than a real one: an element, a
number, an identifier that ends in a digit, a colour and an embedded font.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from support.paths import REPO


def _generator() -> ModuleType:
    """`scripts/build_assets.py` as a module, which is the only way to reach a file outside the package."""
    spec = importlib.util.spec_from_file_location("build_assets", REPO / "scripts" / "build_assets.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_assets = _generator()

FONT = "data:font/woff2;base64,d09GMgABAAAAAAr4ABAAAAAAFjQAAAqfAAEAAAAAAAAAAAAA1234567890+/=="
FIGURE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="248" viewBox="0 0 1000 248">\n'
    f"  <defs><style>@font-face{{font-family:'DT Sans';src:url({FONT})}}\n"
    "  .w3{fill:#7a5000;animation-name:w3}\n"
    "  .head{transform:translateX(343.9px);animation:head 9.0s cubic-bezier(.2,0,0,1) infinite}</style></defs>\n"
    '  <text class="w3" x="270.5" y="96">three.</text>\n'
    '  <rect x="-12.5" y="19.8" width="420" height="152" rx="12"/>\n'
    "</svg>\n"
)


HERO = (REPO / "assets" / "hero-light.svg").read_text(encoding="utf-8")
"""One committed figure, so that the fixture above is held to a file the generator really wrote."""


def nudge(figure: str, old: str, new: str) -> str:
    """The same figure with one number written differently, which is what a second machine produces."""
    assert old in figure
    return figure.replace(old, new, 1)


def test_a_figure_matches_itself() -> None:
    """A file the generator just wrote is what is committed, which is the ordinary run."""
    assert build_assets.stale_reason(FIGURE, FIGURE) is None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("270.5", "272.9"),  # the hero's last word, the largest drift measured between the platforms
        ("19.8", "19.4"),  # a panel in how-it-works, the smallest
        ("343.9", "340.1"),  # a playhead stop, just inside the tolerance
        ("-12.5", "-9.0"),  # a negative coordinate drifts the same way a positive one does
    ],
)
def test_shaping_drift_is_not_stale(old: str, new: str) -> None:
    """A number that moved by less than the tolerance is the same figure measured on another machine."""
    assert build_assets.stale_reason(nudge(FIGURE, old, new), FIGURE) is None


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("270.5", "277.0"),
        ("19.8", "14.0"),
        ("152", "120"),
        ("-12.5", "-20.0"),
    ],
)
def test_a_real_move_is_stale(old: str, new: str) -> None:
    """A number that moved by more than the tolerance is a change to the figure, and it fails."""
    reason = build_assets.stale_reason(nudge(FIGURE, old, new), FIGURE)
    assert reason is not None
    assert old in reason and new in reason


def test_the_tolerance_is_the_edge() -> None:
    """The tolerance itself passes and the smallest step past it fails, so the edge is where it is written."""
    edge = build_assets.SHAPING_TOLERANCE_PX
    assert build_assets.stale_reason(nudge(FIGURE, "270.5", f"{270.5 + edge}"), FIGURE) is None
    assert build_assets.stale_reason(nudge(FIGURE, "270.5", f"{270.5 + edge + 0.1}"), FIGURE) is not None


def test_changed_words_are_stale() -> None:
    """Text is held exactly, because no measurement can rewrite what a figure says."""
    reason = build_assets.stale_reason(FIGURE.replace("three.", "four."), FIGURE)
    assert reason is not None
    assert "the text differs" in reason


def test_a_changed_colour_is_stale() -> None:
    """A colour is text rather than a number, so the digits in it are held exactly."""
    reason = build_assets.stale_reason(FIGURE.replace("#7a5000", "#7a5001"), FIGURE)
    assert reason is not None
    assert "the text differs" in reason


def test_a_renamed_class_is_stale() -> None:
    """An identifier that ends in a digit is text, so `w3` never passes for `w4` within the tolerance."""
    reason = build_assets.stale_reason(FIGURE.replace("w3", "w4"), FIGURE)
    assert reason is not None
    assert "the text differs" in reason


def test_a_new_element_is_stale() -> None:
    """Structure is held exactly, so an element that appeared or vanished fails whatever its numbers are."""
    reason = build_assets.stale_reason(FIGURE.replace("</svg>", '  <circle cx="8" cy="8" r="4"/>\n</svg>'), FIGURE)
    assert reason is not None
    assert "the text differs" in reason


def test_a_changed_font_is_stale() -> None:
    """An embedded font is compared whole, so a digit inside its base64 is never read as a number."""
    reason = build_assets.stale_reason(FIGURE.replace("1234567890", "1234567891"), FIGURE)
    assert reason == "the embedded fonts differ"


def test_only_the_measured_figures_carry_the_tolerance() -> None:
    """Every other generated file is held byte for byte, because constants say the same thing anywhere."""
    assert build_assets.measured(Path("assets/hero-light.svg"))
    assert build_assets.measured(Path("docs/images/how-it-works-dark-stacked.svg"))
    assert build_assets.measured(Path("assets/narration-zero-light.svg"))
    assert build_assets.measured(Path("assets/og.svg"))
    assert not build_assets.measured(Path("docs/logo/light.svg"))
    assert not build_assets.measured(Path("assets/mark/lockup-nav-24.svg"))
    assert not build_assets.measured(Path("site/tokens.css"))


def test_a_committed_figure_matches_itself() -> None:
    """The fixture stands for a real figure, and the real figure agrees with it."""
    assert build_assets.stale_reason(HERO, HERO) is None


def test_a_digit_inside_an_embedded_font_is_never_a_number() -> None:
    """A figure is mostly base64, and a byte of that changing is a new font rather than a drift."""
    payload = build_assets.FONT_PAYLOAD.search(HERO)
    assert payload is not None
    body = payload.group()
    swapped = body[:-8] + ("A" if body[-8] != "A" else "B") + body[-7:]
    assert build_assets.stale_reason(HERO.replace(body, swapped, 1), HERO) == "the embedded fonts differ"


def test_the_report_sends_each_file_to_the_comparison_it_belongs_to(capsys: pytest.CaptureFixture[str]) -> None:
    """A measured figure reaches the tolerance and every other generated file is held to the byte."""
    hero = REPO / "assets" / "hero-light.svg"
    tokens = REPO / "site" / "tokens.css"
    css = tokens.read_text(encoding="utf-8")
    assert build_assets.report_stale({hero: HERO, tokens: css}) == 0
    assert build_assets.report_stale({hero: nudge(HERO, "343.9", "346.9")}) == 0
    assert build_assets.report_stale({hero: nudge(HERO, "343.9", "353.9")}) == 1
    assert build_assets.report_stale({tokens: css.replace("--dt-size-xs: 12px", "--dt-size-xs: 13px", 1)}) == 1
    printed = capsys.readouterr().out
    assert "stale: assets/hero-light.svg" in printed
    assert "stale: site/tokens.css, because it differs from its source" in printed
