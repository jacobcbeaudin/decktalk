"""Which command reports each code the page raises, read off the page as each command opens it.

A runtime code's page names the commands that report it, from the code's own `raised_in`. `check`
reads the report of the index page, which lays every slide out once and plays nothing, and `record`
reads the report of the scene played on the stepped clock with its cues and words. So a code is
`check`'s exactly when the index page raises it, and `record`'s exactly when the played scene does,
and `verify` repeats every code the recording log carries.

Each page below carries one condition, opened twice through decktalk-probe.js the way a command opens
it, and the codes each opening reports are compared with what the code says of itself.

    uv run pytest -m browser tests/contract/test_reporters.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from contract.test_probe import instrument
from decktalk.findings import CHECK, Code, RaisedBy
from decktalk.pipeline import Stage
from support.browser_pages import KATEX, Tab, chromium_tab, opened, write_page

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser

SLIDE = "1.1"
"""The one slide every page here declares, in scene 1."""

LINE = '<p data-in="a" data-describe="the line">a line</p>'
"""A moment that keeps the slide in the cue list, so the played scene has something to mount."""

STEPS = (0.1, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5)
"""The seconds the played scene is stepped through, past every cue and every arrival below."""

LONG = " ".join(f"word{index}" for index in range(12))
"""A line longer than one cue may show a word at a time."""


@dataclass(frozen=True)
class Condition:
    """One page that carries one condition, and how a recording plays it."""

    body: str
    cues: str = f"{SLIDE}:a@0.1"
    words: str = ""
    head: str = ""
    reduced: bool = False


def markup(inside: str, *, outside: str = "") -> str:
    """Scene 1 with slide 1.1 holding the shared line and `inside`, and `outside` beside the slide."""
    return f'<div data-scene="1">{outside}<template data-slide="{SLIDE}">{LINE}{inside}</template></div>'


def script(slides: str, after: str = "") -> str:
    """Scene 1 declared from script, with `after` run once the scene is declared."""
    return f"<script>DeckTalk.scene(1, {{ slides: [{slides}] }});{after}</script>"


RENDERED = '{ id: "1.1", owns: ["a"], render: () => "<p id=bar>x</p>"'
"""A scripted slide owning the cue `a`, left open for the callback a case adds."""

THREW = '() => { throw new Error("no"); }'

CONDITIONS: dict[Code, Condition] = {
    Code.PAGE_ATTR_UNKNOWN: Condition(markup('<p data-inn="show">x</p>')),
    Code.PAGE_BAD_VALUE: Condition(markup('<p data-in="b" data-in-style="zoom" data-describe="b">b</p>')),
    Code.PAGE_MOMENT_UNKNOWN: Condition(markup("", outside='<p data-in="loose" data-describe="l">loose</p>')),
    Code.PAGE_MOMENT_ORDER: Condition(markup('<p data-in="b" data-out="b" data-describe="b">b</p>')),
    Code.PAGE_CUE_UNKNOWN: Condition(markup(""), cues=f"{SLIDE}:a@0.1,{SLIDE}:nobody@0.2"),
    Code.PAGE_NO_OWNER: Condition(markup(""), cues=f"{SLIDE}:a@0.1,{SLIDE}:nobody@0.2"),
    Code.PAGE_SCENE_EMPTY: Condition(markup("") + '<div data-scene="2"></div>'),
    Code.PAGE_SLIDE_NO_ID: Condition(markup("", outside="<template></template>")),
    Code.PAGE_SLIDE_DOUBLED: Condition(markup("", outside=f'<template data-slide="{SLIDE}"><p>b</p></template>')),
    Code.PAGE_SLIDE_UNUSED: Condition(
        markup("", outside='<template data-slide="1.2"><p data-in="b" data-describe="b">b</p></template>')
    ),
    Code.PAGE_TEMPLATE_IGNORED: Condition(markup("<template><p>never mounted</p></template>")),
    Code.PAGE_WORDS_NOT_FOUND: Condition(
        markup('<p data-in="b" data-words data-describe="b">nothing like it</p>'),
        cues=f"{SLIDE}:a@0.1,{SLIDE}:b@0.2",
        words="alpha@0.3,beta@0.5",
    ),
    Code.PAGE_KATEX_MISSING: Condition(markup('<p data-in="b" data-tex="x^2" data-describe="b">x</p>')),
    Code.PAGE_KATEX_ERROR: Condition(
        markup('<p data-in="b" data-tex="\\frac{1}{" data-describe="b">one over</p>'),
        cues=f"{SLIDE}:a@0.1,{SLIDE}:b@0.2",
        head=KATEX,
    ),
    Code.PAGE_RENDER_THREW: Condition(script(f"{RENDERED.split(', render')[0]}, render: {THREW} }}")),
    Code.PAGE_ENTER_THREW: Condition(script(f"{RENDERED}, entered: {THREW} }}")),
    Code.PAGE_SLIDE_HANDLER_THREW: Condition(script(f"{RENDERED}, on: {{ a: {THREW} }} }}")),
    Code.PAGE_HANDLER_THREW: Condition(script(f"{RENDERED} }}", f'DeckTalk.on("{SLIDE}:a", {THREW});')),
    Code.PAGE_MOTION_UNDECLARED: Condition(
        "<style>@keyframes spin { to { transform: rotate(360deg) } }</style>"
        + markup('<div style="width:40px;height:40px;animation:spin 6s linear infinite"></div>')
    ),
    Code.PAGE_MOTION_CUE_UNKNOWN: Condition(
        script(
            f"{RENDERED} }}",
            f"DeckTalk.on('{SLIDE}:a', (slide) => DeckTalk.motion(slide.querySelector('#bar'),"
            f" {{ from: '{SLIDE}:a', to: '{SLIDE}:nowhere', draw: () => {{}} }}));",
        )
    ),
    Code.PAGE_REALTIME_MEDIA: Condition(markup('<video src="clip.mp4"></video>')),
    Code.PAGE_FRAME_THREW: Condition(script(f"{RENDERED} }}", f'DeckTalk.frame({THREW}, {{ region: "p" }});')),
    Code.PAGE_WAIT_REJECTED: Condition(
        "<script>DeckTalk.waitFor(Promise.reject(new Error('no')));</script>" + markup("")
    ),
    Code.PAGE_WAIT_UNSETTLED: Condition("<script>DeckTalk.waitFor(new Promise(() => {}));</script>" + markup("")),
    Code.PAGE_CLASS_UNDESCRIBED: Condition(markup('<p data-in="b" data-class="c:stale" data-describe="b">b</p>')),
    Code.PAGE_CLASS_NOT_REDUCED: Condition(
        markup('<p data-in="b" data-class="c:stale" data-describe-class="c:struck out" data-describe="b">b</p>'),
        cues=f"{SLIDE}:a@0.1,{SLIDE}:b@0.2,{SLIDE}:c@0.4",
        head="<style>.stale { animation: fade 0.3s linear both } @keyframes fade { to { opacity: .2 } }</style>",
        reduced=True,
    ),
    Code.PAGE_SWAP_AMBIGUOUS: Condition(markup('<p data-in="b" data-swaps data-describe="b">b</p>')),
    Code.PAGE_APPEAR_TOO_LONG: Condition(
        markup(f'<p data-in="b" data-words="appear" data-describe="b">{LONG}</p>'),
        cues=f"{SLIDE}:a@0.1,{SLIDE}:b@0.2",
        words=",".join(f"word{index}@{0.3 + index / 10:.1f}" for index in range(12)),
    ),
    Code.PAGE_STAGGER_EMPTY: Condition(markup('<p data-in="b" data-stagger="0.08" data-describe="b">x</p>')),
    Code.PAGE_SPOTLIGHT_EMPTY: Condition(markup('<ul data-spotlight><li data-describe="a point">one</li></ul>')),
    Code.PAGE_MOTION_UNFINISHED: Condition(
        script(
            '{ id: "1.1", owns: ["a", "land"], render: () => "<p id=bar>x</p>" }',
            "DeckTalk.on('1.1:a', (slide) => DeckTalk.motion(slide.querySelector('#bar'),"
            " { from: '1.1:a', to: '1.1:land', draw: (p, t) => { slide.querySelector('#bar').textContent = t; } }));",
        ),
        cues=f"{SLIDE}:a@0.1,{SLIDE}:land@1.0",
    ),
}
"""One page per runtime code a page can carry by itself."""

NO_PAGE_CARRIES = {
    Code.PAGE_FREEZE_CUE_UNKNOWN: "every freeze a command asks for stops at a cue the slide declares",
    Code.PAGE_PREVIEW_AMBIGUOUS: "only a preview reads the resolved cue times, and no command plays a preview",
}
"""Runtime codes no command's opening of a page can raise, with why."""


@pytest.fixture(scope="module")
def tab():
    """The page a command opens: decktalk-probe.js added as an init script."""
    yield from chromium_tab(instrument)


@pytest.fixture(scope="module")
def quiet(tab: Tab):
    """The same, asking for reduced motion, which is what a build with `motion.reduce` opens."""
    browser = tab.page.context.browser
    assert browser is not None
    other = browser.new_page(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    instrument(other)
    yield other
    other.close()


def codes_of(page: Page) -> set[str]:
    return {row["code"] for row in page.evaluate("() => window.__decktalkProbe.report().warnings")}


def as_check_opens_it(page: Page, url: str) -> set[str]:
    """The index page, ready, which is the one report `check` reads."""
    opened(page, url)
    page.wait_for_function("() => window.__decktalkProbe.ready()")
    return codes_of(page)


def as_record_plays_it(page: Page, url: str, found: Condition) -> set[str]:
    """The scene played on the stepped clock with its cues and words, which is the report `record` reads."""
    query = f"?scene=1&t0=signal&cues={found.cues}" + (f"&words={found.words}" if found.words else "")
    opened(page, url + query)
    page.wait_for_function("() => window.__decktalkProbe.ready()")
    for seconds in STEPS:
        page.evaluate(f"() => window.DeckTalk.stepTo({seconds})")
    return codes_of(page)


def test_every_runtime_code_has_a_page_here_or_a_reason_none_can() -> None:
    runtime = {code for code in Code if code.raised_by is RaisedBy.RUNTIME}
    assert set(CONDITIONS) | set(NO_PAGE_CARRIES) == runtime
    assert not set(CONDITIONS) & set(NO_PAGE_CARRIES)


@pytest.mark.parametrize("code", list(CONDITIONS), ids=str)
def test_the_commands_a_runtime_code_names_are_the_ones_whose_opening_raises_it(
    tab: Tab, quiet: Page, tmp_path: Path, code: Code
) -> None:
    found = CONDITIONS[code]
    page = quiet if found.reduced else tab.page
    url = write_page(tmp_path, f"{code.name}.html", found.body, head=found.head)
    checked = code.value in as_check_opens_it(page, url)
    recorded = code.value in as_record_plays_it(page, url, found)
    assert (checked, recorded) == (CHECK in code.raised_in, Stage.RECORD.value in code.raised_in)


@pytest.mark.parametrize("code", list(NO_PAGE_CARRIES), ids=str)
def test_a_code_no_opening_raises_names_no_command_that_opens_a_page(code: Code) -> None:
    assert not {CHECK, Stage.RECORD.value} & set(code.raised_in)
