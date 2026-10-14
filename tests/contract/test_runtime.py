"""decktalk-runtime.js, the page contract as a deck loads it, opened the way a person opens it.

Every page here is written by the test, carries the shipped bundle and nothing else, and is judged
through `window.__decktalk`. What the recorder adds to a page lives in `tests/contract/test_probe.py`
and never here, which is the property the split between the two bundles is supposed to have.

The numbers every assertion is written against come from `decktalk.page`, which is generated from
`contract.ts`, so a length that changes in the registry changes here without an edit.

    uv run pytest -m browser tests/contract/test_runtime.py
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response
from werkzeug.utils import send_from_directory

from decktalk.findings import Code
from decktalk.page import (
    APPEAR_WORDS_MAX,
    ATTENTION,
    BACK_OPACITY,
    COUNTS,
    ENGINE_PATH,
    ENTRANCES,
    EXITS,
    FRAME_STEP_MS,
    MEASURABLE_SPAN_SECONDS,
    MOTION_EASES,
    ONSET_FIRST_FRAME_PERCENT,
    PREVIEW_CUE_TIMES,
    SLIDE_ENTRANCES,
    WORD_STYLES,
)
from decktalk.toolchain.assets import RUNTIME_FILE, engine_files
from support.browser_pages import KATEX, Tab, chromium_tab, opened, script_page, settled, write_page

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser


# The three-slide scene every markup test uses: attributes only, no JavaScript anywhere. Its moments
# write cue names, so the cue ids the recorder sees are "1.1:ball" and the rest.
MARKUP_SCENE = """
<div data-scene="1" data-name="Open">
  <template data-slide="1.1" data-preview-seconds="6">
    <h1 class="title">A bowl</h1>
    <p class="ball" data-in="ball" data-in-style="pop" data-describe="a ball rests in the bowl">A ball</p>
    <p class="step" data-in="step" data-back="ball" data-describe="the step down">Watch it step down</p>
  </template>
  <template data-slide="1.2" data-preview-seconds="4">
    <p class="sum" data-in="sum" data-tex-display data-tex="\\sum_{i=1}^{n} x_i"
       data-describe="the sum of the first n terms">the sum of x i from one to n</p>
  </template>
  <template data-slide="1.3" data-preview-seconds="4" data-owns="aside">
    <p class="late" data-in="late" data-describe="the closing line">nothing follows</p>
  </template>
</div>
"""


def deck(tmp_path: Path, name: str, body: str = MARKUP_SCENE) -> str:
    """A page carrying the shared scene and the typesetter its one equation asks for."""
    return write_page(tmp_path, name, body, head=KATEX)


@pytest.fixture(scope="module")
def tab():
    """The page a person opens: the runtime and nothing the recorder would add."""
    yield from chromium_tab()


@pytest.fixture
def page(tab: Tab) -> Page:
    tab.errors.clear()
    return tab.page


@pytest.fixture
def errors(tab: Tab) -> list[str]:
    """What the page threw during this test, which `page` cleared before it began."""
    return tab.errors


def warnings_of(page: Page) -> list[dict[str, Any]]:
    """Every warning the page reported, as the five fields the contract publishes."""
    return page.evaluate("() => window.__decktalk.warnings")


def codes_of(page: Page) -> list[str]:
    """The codes alone, which is what a test that cares about the condition and not the place reads."""
    return [row["code"] for row in warnings_of(page)]


def opacity_one_frame_in(page: Page, selector: str) -> float:
    """The opacity of an element one captured frame into whatever it is playing.

    The animation is paused and moved rather than watched, so the assertion is exact rather than a
    race against the compositor.
    """
    return page.evaluate(
        """([selector, frame]) => {
            const el = document.querySelector(selector);
            const playing = el.getAnimations();
            for (const one of playing) { one.pause(); one.currentTime = frame; }
            return Number(getComputedStyle(el).opacity);
        }""",
        [selector, FRAME_STEP_MS],
    )


STEP_UNTIL_JS = """async ([until, step]) => {
  const painted = () => new Promise((done) => requestAnimationFrame(() => done()));
  let due = window.DeckTalk.stepTo(0).due;
  await painted();
  for (let at = step; at < until; at += step) {
    while (due !== null && due < at) due = window.DeckTalk.stepTo(due).due;
    due = window.DeckTalk.stepTo(at).due;
    await painted();
  }
  while (due !== null && due < until) due = window.DeckTalk.stepTo(due).due;
  window.DeckTalk.stepTo(until);
  await painted();
}"""
"""Step the section clock frame by frame to `until`, to every due task on its own second first, and paint each frame."""


def stepped(page: Page, until: float) -> None:
    """Show the page at `until` the way the recorder reaches it, one painted frame at a time.

    An effect is born on the second its cue runs, and an entrance hands its element back when the
    frame that ends it is painted, so the page is painted after every step as a recorded frame is.
    What a test reads at `until` is then exact rather than a wait that hopes the effect has ended.
    """
    page.evaluate(STEP_UNTIL_JS, [until, FRAME_STEP_MS / 1000])


ALIAS = PREVIEW_CUE_TIMES
"""The one router alias a preview asks its origin for, which the served directory does not hold."""


@pytest.fixture
def origin(tmp_path: Path, httpserver: HTTPServer) -> Iterator[Any]:
    """A served project directory, so a preview can ask for the cue times the last run resolved."""
    published: list[dict[str, Any]] = []

    def serve(request: Request) -> Response:
        if request.path == ALIAS:
            if not published:
                return Response(status=404)
            return Response(json.dumps(published[-1]), mimetype="application/json")
        if request.path.startswith(ENGINE_PATH):
            # The engine's own files, answered from the package as DeckTalk's origin answers them.
            found = engine_files()[request.path.removeprefix(ENGINE_PATH)]
            return send_from_directory(found.parent, found.name, request.environ)
        return send_from_directory(tmp_path, request.path.lstrip("/"), request.environ)

    httpserver.expect_request(re.compile(".*")).respond_with_handler(serve)

    class Server:
        def publish(self, document: dict[str, Any] | None) -> None:
            published[:] = [] if document is None else [document]

        def write(self, name: str, body: str) -> str:
            """A page that loads the runtime and the typesetter from the engine's path, as a project is served."""
            (tmp_path / name).write_text(
                f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name}</title>'
                f'<link rel="stylesheet" href="{ENGINE_PATH}katex/katex.min.css">'
                f'<script src="{ENGINE_PATH}katex/katex.min.js"></script>'
                f'<script src="{ENGINE_PATH}{RUNTIME_FILE}"></script></head><body>{body}</body></html>',
                encoding="utf-8",
            )
            return httpserver.url_for(name)

    yield Server()


# ---- the shape of a page --------------------------------------------------------------------


def test_the_runtime_declares_the_version_it_shipped_with(page, tmp_path):
    """The recorder writes the page's version into the recording log, so the page publishes one."""
    opened(page, deck(tmp_path, "version.html"))
    assert page.evaluate("() => window.__decktalk.version") == page.evaluate("() => window.DeckTalk.version")
    assert page.evaluate("() => /^\\d+\\.\\d+\\.\\d+/.test(window.__decktalk.version)")


def test_the_stylesheet_is_prepended_and_carries_no_specificity(page, tmp_path):
    """A page rule of equal weight has to win, so every runtime selector sits inside :where()."""
    head = "<style>.ball { color: rgb(1, 2, 3) }</style>"
    opened(page, write_page(tmp_path, "weight.html", MARKUP_SCENE, head=head + KATEX))
    sheet = page.evaluate("() => document.getElementById('dt-style').textContent")
    assert sheet.strip().startswith(":where(")
    # A still and the clear frame the caption band is drawn on are the two things a page may not argue,
    # so theirs are the only forced rules.
    forced = [line for line in sheet.splitlines() if "!important" in line]
    assert all("dt-frozen" in line or "dt-captioning" in line for line in forced)
    assert all(line.startswith(":where(") for line in sheet.splitlines() if "{" in line and "@keyframes" not in line)
    assert page.evaluate("() => document.head.firstElementChild.id") == "dt-style"
    settled(page, f"{write_page(tmp_path, 'weight-shown.html', MARKUP_SCENE, head=head + KATEX)}?freeze=1.1")
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ball')).color") == "rgb(1, 2, 3)"


def test_wait_for_holds_ready_until_the_pages_own_condition(page, errors, tmp_path):
    """A deck with a condition of its own adds it to the runtime rather than replacing a global."""
    body = "<script>DeckTalk.waitFor(new Promise((r) => setTimeout(() => { window.__late = 1; r(); }, 200)));</script>"
    opened(page, deck(tmp_path, "gate.html", body + MARKUP_SCENE))
    assert page.evaluate("() => window.__late") == 1
    assert not errors


# ---- slides written as markup ------------------------------------------------------------------


def test_a_markup_scene_needs_no_javascript(page, tmp_path):
    """The whole contract is attributes, so a deck of templates is a deck with no script in it."""
    opened(page, deck(tmp_path, "markup.html"))
    catalog = page.evaluate("() => window.__decktalk.catalog")
    assert [entry["scene"] for entry in catalog] == ["1"]
    assert catalog[0]["name"] == "Open"
    assert catalog[0]["slides"] == ["1.1", "1.2", "1.3"]
    assert warnings_of(page) == []


def test_a_template_slide_writes_a_backslash_once(page, tmp_path):
    """Markup is parsed and not evaluated, which is the reason to prefer a template to a render string."""
    settled(page, f"{deck(tmp_path, 'tex.html')}?freeze=1.2")
    assert page.evaluate("() => document.querySelector('.sum').getAttribute('data-tex')") == "\\sum_{i=1}^{n} x_i"


def test_markup_slides_join_a_scene_declared_in_script(page, tmp_path):
    """A deck may be half markup and half script, and neither half has to know about the other."""
    script = """
      DeckTalk.scene(1, { name: "Scripted", slides: [
        { id: "1.1" },
        { id: "1.9", owns: ["only"], render: () => "<p>built in script</p>" },
      ]});
    """
    opened(page, deck(tmp_path, "mixed.html", f"<script>{script}</script>{MARKUP_SCENE}"))
    catalog = page.evaluate("() => window.__decktalk.catalog")
    assert catalog[0]["name"] == "Scripted"
    assert catalog[0]["slides"] == ["1.1", "1.9", "1.2", "1.3"]
    assert catalog[0]["cues"]["1.9"] == ["1.9:only"]


# ---- every moment reaches the cue order ------------------------------------------------------


@pytest.mark.parametrize(
    ("attribute", "local"),
    [("data-in", "arrive"), ("data-back", "dim"), ("data-front", "lift"), ("data-out", "go")],
)
def test_every_moment_attribute_joins_the_cue_order(page, tmp_path, attribute, local):
    """Every moment joins the cue order, because a moment outside it is a cue no check can see."""
    scene = f"""
    <div data-scene="2">
      <template data-slide="2.1">
        <p data-in="arrive" data-describe="the line">a line</p>
        <p {attribute}="{local}" data-describe="the subject">the subject</p>
      </template>
    </div>
    """
    opened(page, write_page(tmp_path, f"moment-{local}.html", scene))
    assert f"2.1:{local}" in page.evaluate("() => window.__decktalk.catalog[0].cues['2.1']")


def test_a_local_moment_is_qualified_with_the_slide_that_carries_it(page, tmp_path):
    """The author writes `ball` and its cue id is `1.1:ball`, which is the whole of the qualification."""
    opened(page, deck(tmp_path, "qualified.html"))
    cues = page.evaluate("() => window.__decktalk.catalog[0].cues")
    assert cues["1.1"] == ["1.1:ball", "1.1:step"]
    assert cues["1.3"] == ["1.3:late", "1.3:aside"]


def test_data_owns_claims_a_cue_only_a_handler_serves(page, tmp_path):
    """A cue with no element of its own still belongs to a slide, which is what declares its owner."""
    body = "<script>DeckTalk.on('1.3:aside', (slide) => { window.__aside = slide.dataset ? 1 : 1; });</script>"
    url = deck(tmp_path, "owns.html", body + MARKUP_SCENE)
    page.goto(f"{url}?scene=1&t0=0&cues=1.3:late@0.1,1.3:aside@0.2")
    page.wait_for_function("() => window.__aside === 1")
    assert "PAGE_NO_OWNER" not in codes_of(page)


def test_a_class_moment_joins_the_cue_order_and_declares_its_span(page, tmp_path):
    """A class hands the page's own stylesheet an animation at a cue, so its length is read off the page."""
    head = "<style>.stale { animation: fade 0.3s linear both } @keyframes fade { to { opacity: .2 } }</style>"
    scene = """
    <div data-scene="3">
      <template data-slide="3.1">
        <p data-in="show" data-class="cancel:stale" data-describe-class="cancel:the h is struck out"
           data-describe="the fraction">h over h</p>
      </template>
    </div>
    """
    opened(page, write_page(tmp_path, "class.html", scene, head=head))
    entry = page.evaluate("() => window.__decktalk.catalog[0]")
    assert entry["cues"]["3.1"] == ["3.1:show", "3.1:cancel"]
    assert entry["spans"]["3.1:cancel"] == pytest.approx(0.3)
    assert warnings_of(page) == []


# ---- the modes -------------------------------------------------------------------------------


def test_cue_mode_fires_in_order_and_reports_each_cue(page, tmp_path):
    """The recorder passes cue ids and seconds, and the page fires each at its own second."""
    url = deck(tmp_path, "cued.html")
    settled(page, f"{url}?scene=1&t0=0&cues=1.1:ball@0.1,1.1:step@0.3,1.2:sum@0.6")
    page.wait_for_function("() => window.__decktalk.fired.length === 3")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball", "1.1:step", "1.2:sum"]
    assert page.evaluate("() => window.__decktalk.mode") == "record"


def test_the_first_slide_is_mounted_before_the_clock_starts(page, tmp_path):
    """A recording that opened on an empty stage would spend its first frames on nothing."""
    url = deck(tmp_path, "first.html")
    opened(page, f"{url}?scene=1&t0=signal&cues=1.1:ball@0.2,1.2:sum@0.6")
    assert page.evaluate("() => window.__decktalk.slide") == "1.1"
    assert page.evaluate("() => window.__decktalk.started()") is False
    assert page.evaluate("() => window.__decktalk.fired") == []
    page.evaluate("() => window.DeckTalk.stepTo(0.6)")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball", "1.2:sum"]


def test_a_stepped_page_shows_each_second_the_recorder_steps_it_to(page, tmp_path):
    """The recorder moves the section clock, and the page holds every animation at the second it shows.

    The ball pops in at 0.1, so one stepped frame later it is exactly one frame into its entrance,
    whatever the compositor was doing, and stepping back to that second gives the same opacity again.
    """
    url = deck(tmp_path, "stepped.html")
    opened(page, f"{url}?scene=1&t0=signal&cues=1.1:ball@0.1,1.2:sum@0.6")
    page.evaluate("() => window.DeckTalk.stepTo(0)")
    assert page.evaluate("() => window.__decktalk.fired") == []
    page.evaluate("() => window.DeckTalk.stepTo(0.1)")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball"]
    page.evaluate(f"() => window.DeckTalk.stepTo({0.1 + FRAME_STEP_MS / 1000})")
    opacity = page.evaluate("() => Number(getComputedStyle(document.querySelector('.ball')).opacity)")
    held = page.evaluate(
        """() => document.querySelector('.ball').getAnimations()
            .map((one) => [one.playState, Math.round(one.currentTime)])"""
    )
    assert held == [["paused", FRAME_STEP_MS]]
    assert 0 < opacity < 1
    page.evaluate("() => window.DeckTalk.stepTo(0.6)")
    assert page.evaluate("() => window.__decktalk.slide") == "1.2"
    assert page.evaluate("() => window.__decktalk.now()") == 0.6


def test_a_frame_hook_draws_every_stepped_second(page, tmp_path):
    """`DeckTalk.frame` is the per-frame hook a canvas or a typed line is drawn from, on the section clock."""
    script = (
        'DeckTalk.scene(15, { slides: [{ id: "15.1", owns: ["beat"], render: () => "<p id=bar></p>" }] });'
        "window.seen = []; DeckTalk.frame((t) => window.seen.push(t));"
    )
    opened(page, f"{script_page(tmp_path, 'frame.html', script)}?scene=15&t0=signal&cues=15.1:beat@0.1")
    for seconds in (0, 0.04, 0.08):
        page.evaluate(f"() => window.DeckTalk.stepTo({seconds})")
    assert page.evaluate("() => window.seen") == [0, 0.04, 0.08]


def test_the_index_page_reports_a_frame_hook_that_throws_before_it_is_ready(page, tmp_path):
    """Off the stepped clock a hook draws once as it is handed over, so `check` meets a throw on every machine.

    The index page draws no animation frame before it is ready, so a hook that waited for one would be
    reported or not by the race between the frame and the probe. The hook is handed the second the
    page shows, which is zero before the clock starts, and is never called again once it has thrown.
    """
    script = (
        'DeckTalk.scene(15, { slides: [{ id: "15.1", owns: ["beat"], render: () => "<p></p>" }] });'
        "window.handed = [];"
        'DeckTalk.frame((t) => { window.handed.push(t); throw new Error("no"); }, { region: "p" });'
        "window.atOnce = window.__decktalk.warnings.map((row) => row.code);"
    )
    opened(page, script_page(tmp_path, "index-threw.html", script))
    assert page.evaluate("() => window.__decktalk.mode") == "index"
    assert Code.PAGE_FRAME_THREW.value in page.evaluate("() => window.atOnce")
    page.evaluate("() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    assert page.evaluate("() => window.handed") == [0]


MOVING_SCENE = """
<style>
  @keyframes travel { from { transform: translateX(0px) } to { transform: translateX(1000px) } }
  @keyframes spin { to { transform: rotate(360deg) } }
</style>
<div data-scene="16" data-name="Moving">
  <template data-slide="16.1">
    <div class="packet" style="width:40px;height:40px;background:#000" data-motion="travel"
         data-motion-from="go" data-motion-to="land" data-motion-ease="firm"></div>
    <div class="orbit" style="width:40px;height:40px;background:#000" data-loop="spin" data-loop-seconds="6"></div>
    <p data-in="land" data-describe="the landing">landed</p>
  </template>
</div>
"""


def translated(page: Page, selector: str) -> float:
    """How far an element sits along x, read from the transform the page computed for it."""
    return page.evaluate(f"() => new DOMMatrix(getComputedStyle(document.querySelector('{selector}')).transform).m41")


def firm(part: float) -> float:
    """The firm ease the contract publishes, solved here so the test does not trust the page's own solver."""
    curve = MOTION_EASES["firm"]["curve"]
    assert isinstance(curve, list)
    x1, y1, x2, y2 = (float(point) for point in curve)
    lo, hi = 0.0, 1.0
    for _ in range(60):
        s = (lo + hi) / 2
        x = 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s**2 * x2 + s**3
        lo, hi = (s, hi) if x < part else (lo, s)
    return 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s**2 * y2 + s**3


def test_a_motion_travels_between_its_two_cues_on_the_stepped_clock_and_lands_on_the_second(page, tmp_path):
    """The length is the two cues' seconds and never a duration in markup, so a re-voiced line re-times it."""
    url = write_page(tmp_path, "moving.html", MOVING_SCENE)
    opened(page, f"{url}?scene=16&t0=signal&cues=16.1:go@1.0,16.1:land@2.0")
    for seconds in (0, 0.5, 1.0):
        page.evaluate(f"() => window.DeckTalk.stepTo({seconds})")
        assert translated(page, ".packet") == 0
    page.evaluate("() => window.DeckTalk.stepTo(1.5)")
    assert abs(translated(page, ".packet") - 1000 * firm(0.5)) < 1
    page.evaluate("() => window.DeckTalk.stepTo(2.0)")
    assert translated(page, ".packet") == 1000
    page.evaluate("() => window.DeckTalk.stepTo(2.5)")
    assert translated(page, ".packet") == 1000


def test_a_motion_s_arrival_is_a_cue_the_slide_declares_even_when_nothing_else_lands_on_it(page, tmp_path):
    scene = MOVING_SCENE.replace('<p data-in="land" data-describe="the landing">landed</p>', "")
    assert scene != MOVING_SCENE
    url = write_page(tmp_path, "arrival.html", scene)
    opened(page, f"{url}?scene=16&t0=signal&cues=16.1:go@1.0,16.1:land@2.0")
    page.evaluate("() => window.DeckTalk.stepTo(2.0)")
    assert Code.PAGE_CUE_UNKNOWN.value not in codes_of(page)


def test_a_loop_turns_from_the_mount_for_as_long_as_its_slide_is_on_screen(page, tmp_path):
    url = write_page(tmp_path, "looping.html", MOVING_SCENE)
    opened(page, f"{url}?scene=16&t0=signal&cues=16.1:go@1.0,16.1:land@2.0")
    page.evaluate("() => window.DeckTalk.stepTo(1.5)")
    turned = page.evaluate("() => document.querySelector('.orbit').getAnimations().map((one) => one.currentTime)")
    assert [round(at) for at in turned] == [1500]


def test_the_catalog_publishes_every_declared_motion_with_its_cues_and_its_ease(page, tmp_path):
    opened(page, write_page(tmp_path, "motions.html", MOVING_SCENE))
    motions = page.evaluate("() => window.__decktalk.catalog[0].motions")
    assert motions == {
        "16.1": [
            {"kind": "motion", "name": "travel", "from": "16.1:go", "to": "16.1:land", "ease": "firm", "seconds": None},
            {"kind": "loop", "name": "spin", "from": None, "to": None, "ease": None, "seconds": 6},
        ]
    }
    assert "16.1:go" in page.evaluate("() => window.__decktalk.catalog[0].cues['16.1']")


def test_a_loop_nobody_declared_is_refused_by_the_page(page, tmp_path):
    """Undeclared motion is what makes verify's control dishonest, so it is refused rather than tolerated."""
    declared = 'style="width:40px;height:40px;background:#000" data-loop="spin" data-loop-seconds="6"'
    undeclared = 'style="width:40px;height:40px;background:#000;animation:spin 6s linear infinite"'
    scene = MOVING_SCENE.replace(declared, undeclared)
    assert scene != MOVING_SCENE
    opened(page, write_page(tmp_path, "undeclared.html", scene))
    assert Code.PAGE_MOTION_UNDECLARED.value in codes_of(page)
    opened(page, write_page(tmp_path, "declared.html", MOVING_SCENE))
    assert Code.PAGE_MOTION_UNDECLARED.value not in codes_of(page)


def test_a_frame_hook_with_no_region_is_undeclared_motion(page, tmp_path):
    script = (
        'DeckTalk.scene(17, { slides: [{ id: "17.1", owns: ["beat"], render: () => "<canvas id=c></canvas>" }] });'
        "DeckTalk.frame(() => {});"
    )
    opened(page, f"{script_page(tmp_path, 'bare-hook.html', script)}?scene=17&t0=signal&cues=17.1:beat@0.1")
    assert Code.PAGE_MOTION_UNDECLARED.value in codes_of(page)
    declared = script.replace("DeckTalk.frame(() => {});", "DeckTalk.frame(() => {}, { region: '#c' });")
    opened(page, f"{script_page(tmp_path, 'region-hook.html', declared)}?scene=17&t0=signal&cues=17.1:beat@0.1")
    assert Code.PAGE_MOTION_UNDECLARED.value not in codes_of(page)


def test_a_video_on_a_page_is_refused_because_the_stepped_clock_cannot_hold_it(page, tmp_path):
    scene = MOVING_SCENE.replace("<p data-in", '<video src="clip.mp4"></video><p data-in')
    opened(page, write_page(tmp_path, "video.html", scene))
    assert Code.PAGE_REALTIME_MEDIA.value in codes_of(page)


CLIP_ATTRS_SCENE = """
<div data-scene="19" data-name="Clip attributes">
  <template data-slide="19.1" data-owns="play">
    <div class="box" style="width:320px;height:180px" data-clip="deck/clips/film.mp4" data-clip-from="play"></div>
  </template>
</div>
"""
"""A slide owning the cue `play`, whose element names a clip in the attributes a page no longer plays one from."""


def test_a_page_that_names_a_clip_in_data_clip_is_told_the_contract_does_not_know_the_attribute(page, tmp_path):
    """A page plays no clip, so `data-clip` and `data-clip-from` are two misspelled attributes like any other."""
    url = write_page(tmp_path, "clip-attrs.html", CLIP_ATTRS_SCENE)
    opened(page, f"{url}?scene=19&t0=signal&cues=19.1:play@0.1")
    page.evaluate("() => window.DeckTalk.stepTo(0.5)")
    unknown = sorted(row["attr"] for row in warnings_of(page) if row["code"] == Code.PAGE_ATTR_UNKNOWN.value)
    assert unknown == ["data-clip", "data-clip-from"]
    assert page.evaluate("() => window.__decktalk.catalog[0].motions") == {"19.1": []}
    assert page.evaluate("() => document.querySelector('.box').childElementCount") == 0, "nothing plays in it"


def test_a_script_motion_draws_its_eased_progress_between_two_cues_and_refuses_a_cue_nobody_owns(page, tmp_path):
    script = (
        'DeckTalk.scene(18, { slides: [{ id: "18.1", owns: ["go", "land"], render: () => "<p id=bar></p>" }] });'
        "window.drawn = [];"
        "DeckTalk.on('18.1:go', (slide) => DeckTalk.motion(slide.querySelector('#bar'),"
        " { from: '18.1:go', to: '18.1:land', ease: 'glide', draw: (p, t) => window.drawn.push([p, t]) }));"
        "DeckTalk.on('18.1:land', (slide) => DeckTalk.motion(slide.querySelector('#bar'),"
        " { from: '18.1:land', to: '18.1:nowhere', draw: () => {} }));"
    )
    opened(page, f"{script_page(tmp_path, 'scripted.html', script)}?scene=18&t0=signal&cues=18.1:go@1.0,18.1:land@2.0")
    for seconds in (1.0, 1.5, 2.0, 2.5):
        page.evaluate(f"() => window.DeckTalk.stepTo({seconds})")
    drawn = page.evaluate("() => window.drawn")
    # The arrival is drawn once half a second past it, to learn whether the draw still moves there, and then at it.
    later = 2.0 + MEASURABLE_SPAN_SECONDS
    assert [(round(progress, 6), seconds) for progress, seconds in drawn] == [
        (0, 1.0),
        (0.5, 1.5),
        (1, later),
        (1, 2.0),
    ]
    assert Code.PAGE_MOTION_CUE_UNKNOWN.value in codes_of(page)
    assert Code.PAGE_MOTION_UNFINISHED.value not in codes_of(page)


def test_a_composition_reads_every_cue_s_second_and_every_spoken_word_from_the_runtime(page, tmp_path):
    """A composition page keys its own drawing on the section's cues and words, which the runtime hands it."""
    script = 'DeckTalk.scene(19, { slides: [{ id: "19.1", owns: ["go"], render: () => "<canvas id=c></canvas>" }] });'
    url = script_page(tmp_path, "composition.html", script)
    opened(page, f"{url}?scene=19&t0=signal&cues=19.1:go@1.25&words=one@0.5,two@1.1&turns=narrator@0.40,guest@1.00")
    assert page.evaluate("() => window.DeckTalk.cues") == {"19.1:go": 1.25}
    assert page.evaluate("() => window.DeckTalk.words") == [
        {"word": "one", "at": 0.5, "voice": "narrator", "turn": 1},
        {"word": "two", "at": 1.1, "voice": "guest", "turn": 2},
    ]


CAPTION_JS = """() => window.__decktalk.caption({
  lines: ["[door opens]", "Says who?"], voice: "guest", turn: 2, spoken: [1, 2],
}).then(() => {
  const caption = document.querySelector("#dt-captions .dt-caption");
  return {
    voice: caption.dataset.voice,
    turn: caption.dataset.turn,
    lines: [...caption.children].map((line) => [line.textContent, "sound" in line.dataset]),
    text: caption.textContent,
    body: getComputedStyle(document.body).visibility,
    band: getComputedStyle(caption).visibility,
    ground: getComputedStyle(document.documentElement).backgroundColor,
    align: getComputedStyle(caption).textAlign,
  };
})"""


def test_caption_mode_draws_one_caption_in_the_band_that_names_its_voice_only_as_data(page, tmp_path):
    """The captioned copy is drawn from this mode: the page's own picture is cleared and the band styled by the page."""
    head = '<style>html,body{background:#123456}.dt-caption[data-voice="guest"]{text-align:right}</style>'
    opened(page, f"{write_page(tmp_path, 'caption.html', MARKUP_SCENE, head=head)}?caption=1")
    assert page.evaluate("() => window.__decktalk.mode") == "caption"
    drawn = page.evaluate(CAPTION_JS)
    assert drawn == {
        "voice": "guest",
        "turn": "2",
        "lines": [["[door opens]", True], ["Says who?", False]],
        "text": "[door opens]Says who?",
        "body": "hidden",
        "band": "visible",
        "ground": "rgba(0, 0, 0, 0)",
        "align": "right",
    }
    page.evaluate("() => window.__decktalk.caption(null)")
    assert page.evaluate("() => document.querySelector('#dt-captions').children.length") == 0
    assert page.evaluate("() => window.__decktalk.fired") == []


def test_freeze_mode_fires_every_cue_the_slide_declares(page, tmp_path):
    """A still is the whole slide, which is what the index links and what a screenshot records."""
    settled(page, f"{deck(tmp_path, 'frozen.html')}?freeze=1.1")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball", "1.1:step"]
    assert page.evaluate("() => window.__decktalk.mode") == "freeze"
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ball')).opacity") == "1"


def test_the_index_page_lists_every_scene_and_slide(page, tmp_path):
    """A page with no query is a contents page, which is where a person starts and where boxes are measured."""
    opened(page, deck(tmp_path, "index.html"))
    assert page.evaluate("() => window.__decktalk.mode") == "index"
    links = page.evaluate("() => [...document.querySelectorAll('#dt-index a')].map((a) => a.getAttribute('href'))")
    assert "?scene=1" in links
    assert "?freeze=1.1" in links
    assert page.evaluate("() => getComputedStyle(document.getElementById('dt-canvas')).display") == "none"


def test_a_preview_without_cue_times_still_shows_every_cue(page, tmp_path):
    """A deck whose cues have never been resolved must still preview, or the stage opens empty."""
    page.goto(f"{deck(tmp_path, 'preview.html')}?scene=1&speed=8")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1:step')", timeout=15000)
    assert page.evaluate("() => window.__decktalk.mode") == "preview"
    assert page.evaluate("() => window.__decktalk.fired")[:2] == ["1.1:ball", "1.1:step"]


def test_a_preview_plays_the_cue_times_the_project_resolved(page, origin):
    """The point of a preview is to review the film's own timing without making a recording for it."""
    origin.publish({"sections": [{"key": "01", "scene": "1", "cues": [{"cue": "1.1:step", "at": 0.2}]}]})
    page.goto(f"{origin.write('timed.html', MARKUP_SCENE)}?scene=1")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:step"]
    assert page.evaluate("() => window.__decktalk.mode") == "preview"
    assert warnings_of(page) == []


def test_two_sections_naming_one_scene_cannot_be_told_apart(page, origin):
    """A preview that guessed which section's timing to play would show a film nobody is making."""
    origin.publish(
        {
            "sections": [
                {"key": "01", "scene": "1", "cues": [{"cue": "1.1:ball", "at": 0.2}]},
                {"key": "02", "scene": "1", "cues": [{"cue": "1.1:step", "at": 0.2}]},
            ]
        }
    )
    page.goto(f"{origin.write('twice.html', MARKUP_SCENE)}?scene=1&speed=8")
    page.wait_for_function("() => window.__decktalk.warnings.length > 0")
    assert "PAGE_PREVIEW_AMBIGUOUS" in codes_of(page)


def test_an_absent_cue_times_file_never_fails_a_preview(page, errors, origin):
    """Nothing on the preview path may fail a page, so a project that has never run `cue` previews anyway."""
    origin.publish(None)
    page.goto(f"{origin.write('absent.html', MARKUP_SCENE)}?scene=1&speed=8")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1:ball')", timeout=15000)
    assert warnings_of(page) == []
    assert not errors


# ---- how a moment looks ------------------------------------------------------------------------


@pytest.mark.parametrize("style", sorted(ENTRANCES))
def test_every_entrance_draws_its_share_inside_the_first_captured_frame(page, tmp_path, style):
    """An entrance that drew almost nothing in its first frame is an onset `verify` cannot read."""
    scene = f"""
    <div data-scene="4">
      <template data-slide="4.1">
        <p class="subject" data-in="show" data-in-style="{style}" data-describe="the subject">a subject</p>
      </template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, f'onset-{style}.html', scene)}?scene=4&t0=0&cues=4.1:show@0.05")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    assert opacity_one_frame_in(page, ".subject") >= ONSET_FIRST_FRAME_PERCENT / 100


def test_an_entrance_plays_for_the_length_its_author_wrote(page, tmp_path):
    """`data-in-seconds` governs the entrance alone, which is the span and never the onset."""
    scene = """
    <div data-scene="4">
      <template data-slide="4.1">
        <p class="subject" data-in="show" data-in-seconds="0.4" data-describe="the subject">a subject</p>
      </template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, 'length.html', scene)}?scene=4&t0=0&cues=4.1:show@0.05")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    length = page.evaluate("() => document.querySelector('.subject').getAnimations()[0].effect.getTiming().duration")
    assert length == pytest.approx(400)


def test_a_step_back_dims_the_element_without_hiding_it(page, tmp_path):
    """Stepping back is emphasis, which is dim enough to read as secondary and light enough to read."""
    url = deck(tmp_path, "back.html")
    opened(page, f"{url}?scene=1&t0=signal&cues=1.1:step@0.05,1.1:ball@0.3")
    stepped(page, 0.3 + ATTENTION["back"].seconds)
    assert float(page.evaluate("() => getComputedStyle(document.querySelector('.step')).opacity")) == pytest.approx(
        BACK_OPACITY
    )


def test_an_exit_plays_the_word_the_author_chose(page, tmp_path):
    """Both exits are the one length an exit is allowed, and the word decides what it looks like."""
    scene = """
    <div data-scene="5">
      <template data-slide="5.1">
        <p class="gone" data-in="show" data-out="hide" data-out-style="fall" data-describe="the wrong answer">two</p>
      </template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, 'exit.html', scene)}?scene=5&t0=0&cues=5.1:show@0.05,5.1:hide@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    names = page.evaluate("() => document.querySelector('.gone').getAnimations().map((a) => a.animationName)")
    assert "dt-out-fall" in names
    length = page.evaluate(
        "() => document.querySelector('.gone').getAnimations()"
        ".find((a) => a.animationName === 'dt-out-fall').effect.getTiming().duration"
    )
    assert length == pytest.approx(EXITS["fall"].seconds * 1000)


def test_a_staggered_container_spreads_one_cue_across_its_children(page, tmp_path):
    """One cue and several arrivals is a row of tiles, which is invisible to a frozen frame and exact here."""
    scene = """
    <div data-scene="6">
      <template data-slide="6.1">
        <ul class="row" data-in="tiles" data-stagger="0.08" data-describe="the three tiles">
          <li>one</li><li>two</li><li>three</li>
        </ul>
      </template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, 'stagger.html', scene)}?scene=6&t0=0&cues=6.1:tiles@0.05")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    delays = page.evaluate("() => [...document.querySelectorAll('.row li')].map((li) => li.style.animationDelay)")
    assert delays == ["0s", "0.08s", "0.16s"]
    span = page.evaluate("() => window.__decktalk.catalog[0].spans['6.1:tiles']")
    assert span == pytest.approx(0.08 * 2 + ENTRANCES["rise"].seconds)
    # The published span is the honest arithmetic, which is what makes the overrun an error.
    assert span > MEASURABLE_SPAN_SECONDS - FRAME_STEP_MS / 1000


def test_a_spotlight_brings_each_child_forward_and_steps_the_ones_before_it_back(page, tmp_path):
    """A spotlit list is written once and lands in the catalog as though every moment were typed."""
    scene = """
    <div data-scene="7">
      <template data-slide="7.1">
        <ul class="list" data-spotlight>
          <li class="one" data-in="first" data-describe="the first point">one</li>
          <li class="two" data-in="second" data-describe="the second point">two</li>
        </ul>
      </template>
    </div>
    """
    opened(
        page, f"{write_page(tmp_path, 'spotlight.html', scene)}?scene=7&t0=signal&cues=7.1:first@0.05,7.1:second@0.3"
    )
    stepped(page, 0.3 + ATTENTION["back"].seconds)
    assert page.evaluate("() => document.querySelector('.one').classList.contains('dt-back')") is True
    assert page.evaluate("() => document.querySelector('.two').classList.contains('dt-back')") is False
    assert page.evaluate("() => window.__decktalk.catalog[0].cues['7.1']") == ["7.1:first", "7.1:second"]


SWAP_FRAMES = """() => new Promise((done) => {
  const outgoing = document.querySelector('.wrong');
  const incoming = document.querySelector('.right');
  const frames = [];
  const read = () => {
    const fired = window.__decktalk.fired.length;
    const opacity = Number(getComputedStyle(outgoing).opacity);
    frames.push({ fired, arriving: incoming.getAnimations().length > 0, outgoing: opacity });
    if (fired === 2 && opacity < 0.5) done(frames);
    else requestAnimationFrame(read);
  };
  requestAnimationFrame(read);
})"""
"""Every frame of the swap test: how many cues have fired, whether the entrance plays, and the outgoing opacity."""


def test_a_swap_holds_what_it_replaces_until_it_has_arrived(page, tmp_path):
    """A swap with nothing under it is a cut, so the outgoing element waits for the incoming one."""
    scene = """
    <div data-scene="8">
      <template data-slide="8.1">
        <p class="wrong" data-in="show" data-out="fix" data-describe="the wrong answer">two</p>
        <p class="right" data-in="fix" data-swaps data-describe="the right answer">three</p>
      </template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, 'swap.html', scene)}?scene=8&t0=0&cues=8.1:show@0.05,8.1:fix@1")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    # Every frame from before the swap's cue until the outgoing element is half gone, read in the page,
    # so the test judges the frames the page drew rather than the moment a round trip came back.
    frames = page.evaluate(SWAP_FRAMES)
    arriving = [frame for frame in frames if frame["fired"] == 2 and frame["arriving"]]
    assert arriving, "no frame was read while the incoming element was arriving"
    assert {frame["outgoing"] for frame in arriving} == {1}
    assert frames[-1]["outgoing"] < 0.5
    assert warnings_of(page) == []


def test_the_crossfade_holds_the_outgoing_slide_at_full_opacity(page, tmp_path):
    """Two half-faded slides compose to a dip towards the page background, which is a flash on a white deck."""
    url = deck(tmp_path, "cross.html")
    page.goto(f"{url}?scene=1&t0=0&cues=1.1:ball@0.05,1.2:sum@0.4")
    page.wait_for_function("() => document.querySelectorAll('#dt-pan .dt-slide').length === 2")
    pair = page.evaluate(
        """([half]) => {
            const arriving = document.querySelector('.dt-slide.dt-arriving');
            for (const one of arriving.getAnimations()) { one.pause(); one.currentTime = half; }
            const leaving = document.querySelector('.dt-slide.dt-leaving');
            return [Number(getComputedStyle(leaving).opacity), Number(getComputedStyle(arriving).opacity)];
        }""",
        [SLIDE_ENTRANCES["crossfade"].seconds * 1000 / 2],
    )
    leaving, arriving = pair
    assert leaving == 1.0
    assert 0 < arriving < 1
    # The composite of an opaque slide under a half-faded one is opaque, so nothing shows through mid-fade.
    assert leaving + (1 - leaving) * arriving == pytest.approx(1.0)


def test_the_outgoing_slide_goes_on_the_crossfades_own_end(page, tmp_path):
    """A length written twice is a slide cut off, so the removal waits for the animation and not a timer."""
    head = f"<style>:root {{ --dt-span: {SLIDE_ENTRANCES['crossfade'].seconds}s }}</style>"
    url = write_page(tmp_path, "retire.html", MARKUP_SCENE, head=head + KATEX)
    page.goto(f"{url}?scene=1&t0=0&cues=1.1:ball@0.05,1.2:sum@0.3")
    page.wait_for_function("() => document.querySelectorAll('#dt-pan .dt-slide').length === 1", timeout=5000)
    assert page.evaluate("() => document.querySelector('#dt-pan .dt-slide').dataset === undefined") is False


# ---- the text effects -------------------------------------------------------------------------


def test_a_count_runs_up_to_the_number_the_author_wrote(page, tmp_path):
    """A number growing towards its own value is the one slow change `verify` reads as an onset."""
    scene = """
    <div data-scene="9">
      <template data-slide="9.1">
        <p class="tile" data-in="show" data-count="last" data-describe="the share of games">1 in 1,250</p>
      </template>
    </div>
    """
    opened(page, f"{write_page(tmp_path, 'count.html', scene)}?scene=9&t0=signal&cues=9.1:show@0.05")
    stepped(page, 0.05 + COUNTS["last"].seconds)
    assert page.evaluate("() => document.querySelector('.tile').textContent") == "1 in 1,250"


def test_a_line_is_shown_word_by_word_on_the_voice(page, tmp_path):
    """One word is far under the change floor, so the line reports itself through the telemetry seam."""
    scene = """
    <div data-scene="10">
      <template data-slide="10.1">
        <p class="said" data-in="say" data-words data-describe="the line the voice reaches">two words here</p>
      </template>
    </div>
    """
    url = write_page(tmp_path, "words.html", scene)
    page.goto(f"{url}?scene=10&t0=0&cues=10.1:say@0.05&words=two@0.3,words@0.5,here@0.7")
    page.wait_for_function("() => document.querySelectorAll('.said .dt-word.dt-shown').length === 3", timeout=5000)
    assert page.evaluate("() => document.querySelectorAll('.said .dt-word').length") == 3
    assert warnings_of(page) == []


def test_a_word_is_whole_by_the_second_the_voice_reaches_it(page, tmp_path):
    """The lead is the length of a word's own fade, so a word arrives as it is said and not after."""
    scene = """
    <div data-scene="10">
      <template data-slide="10.1">
        <p class="said" data-in="say" data-words data-describe="the line">alpha beta</p>
      </template>
    </div>
    """
    url = write_page(tmp_path, "lead.html", scene)
    page.goto(f"{url}?scene=10&t0=0&cues=10.1:say@0.05&words=alpha@1.2,beta@1.6")
    page.wait_for_function("() => document.querySelectorAll('.said .dt-word.dt-shown').length === 1", timeout=5000)
    shown_at = page.evaluate("() => window.__decktalk.now()")
    assert shown_at <= 1.2 - WORD_STYLES["highlight"].seconds + 0.1


LONG_LINE = [f"word{index}" for index in range(APPEAR_WORDS_MAX + 2)]
"""A line two words past what `appear` may carry, one word at a time."""


@pytest.mark.parametrize(
    ("line", "style", "words", "code"),
    [
        # A line that is not the spoken text cannot be shown on the voice, so the page says so.
        pytest.param("nothing like it", "", "alpha@0.3,beta@0.5", "PAGE_WORDS_NOT_FOUND", id="a line never said"),
        # One word at a time is a cue's worth of motion, and a long line is more than a cue can carry.
        pytest.param(
            " ".join(LONG_LINE),
            '="appear"',
            ",".join(f"{word}@{0.3 + index / 10}" for index, word in enumerate(LONG_LINE)),
            "PAGE_APPEAR_TOO_LONG",
            id="appear on a long line",
        ),
    ],
)
def test_a_line_the_voice_cannot_carry_is_reported(page, tmp_path, line, style, words, code):
    scene = f"""
    <div data-scene="10">
      <template data-slide="10.1"><p data-in="say" data-words{style} data-describe="the line">{line}</p></template>
    </div>
    """
    url = write_page(tmp_path, "played.html", scene)
    page.goto(f"{url}?scene=10&t0=0&cues=10.1:say@0.05&words={words}")
    page.wait_for_function("() => window.__decktalk.warnings.length > 0")
    assert code in codes_of(page)


# ---- the transcript ---------------------------------------------------------------------------


def test_each_moment_of_one_element_fires_in_the_order_its_cues_land(page, tmp_path):
    """One element owns four moments, and each fires as its own cue lands. The sentence each moment
    composes is read back through the probe in test_probe.py, which is where the transcript lives."""
    scene = """
    <div data-scene="11">
      <template data-slide="11.1">
        <p data-in="show" data-back="aside" data-front="back-to-it" data-out="go"
           data-describe="the definition of the derivative">f'(x)</p>
      </template>
    </div>
    """
    url = write_page(tmp_path, "transcript.html", scene)
    page.goto(f"{url}?scene=11&t0=0&cues=11.1:show@0.05,11.1:aside@0.2,11.1:back-to-it@0.35,11.1:go@0.5")
    page.wait_for_function("() => window.__decktalk.fired.length === 4")
    assert page.evaluate("() => window.__decktalk.fired") == [
        "11.1:show",
        "11.1:aside",
        "11.1:back-to-it",
        "11.1:go",
    ]


def test_a_decorative_element_writes_no_line(page, tmp_path):
    """An empty description is the author saying the element means nothing, which the transcript honours."""
    scene = """
    <div data-scene="11">
      <template data-slide="11.1"><p class="rule" data-in="show" data-describe="">---</p></template>
    </div>
    """
    settled(page, f"{write_page(tmp_path, 'decorative.html', scene)}?freeze=11.1")
    assert warnings_of(page) == []


# ---- handlers ---------------------------------------------------------------------------------


def test_a_slide_handler_and_a_deck_handler_both_receive_the_slide(page, tmp_path):
    """A handler is the escape hatch, so it is handed the mounted slide and where in the deck it ran."""
    script = """
      window.__seen = [];
      DeckTalk.scene(12, { name: "Handled", slides: [{
        id: "12.1", owns: ["beat"], render: () => "<p>drawn</p>",
        entered: (el, ctx) => window.__seen.push(["entered", ctx.slideId]),
        on: { beat: (el, ctx) => window.__seen.push(["slide", ctx.id, el.className]) },
      }]});
      DeckTalk.on("12.1:beat", (el, ctx) => window.__seen.push(["deck", ctx.id, ctx.frozen]));
    """
    page.goto(f"{script_page(tmp_path, 'handlers.html', script)}?scene=12&t0=0&cues=12.1:beat@0.1")
    page.wait_for_function("() => window.__seen.length === 3")
    assert page.evaluate("() => window.__seen") == [
        ["entered", "12.1"],
        ["slide", "12.1:beat", "dt-slide"],
        ["deck", "12.1:beat", False],
    ]


@pytest.mark.parametrize(
    ("script", "code"),
    [
        (
            'DeckTalk.scene(13, { slides: [{ id: "13.1", owns: ["beat"],'
            ' render: () => { throw new Error("no"); } }] });',
            "PAGE_RENDER_THREW",
        ),
        (
            'DeckTalk.scene(13, { slides: [{ id: "13.1", owns: ["beat"], render: () => "<p>x</p>",'
            ' entered: () => { throw new Error("no"); } }] });',
            "PAGE_ENTER_THREW",
        ),
        (
            'DeckTalk.scene(13, { slides: [{ id: "13.1", owns: ["beat"], render: () => "<p>x</p>",'
            ' on: { beat: () => { throw new Error("no"); } } }] });',
            "PAGE_SLIDE_HANDLER_THREW",
        ),
        (
            'DeckTalk.scene(13, { slides: [{ id: "13.1", owns: ["beat"], render: () => "<p>x</p>" }] });'
            'DeckTalk.on("13.1:beat", () => { throw new Error("no"); });',
            "PAGE_HANDLER_THREW",
        ),
        (
            'DeckTalk.scene(13, { slides: [{ id: "13.1", owns: ["beat"], render: () => "<p>x</p>" }] });'
            'DeckTalk.frame(() => { throw new Error("no"); }, { region: "p" });',
            Code.PAGE_FRAME_THREW.value,
        ),
    ],
)
def test_a_page_callback_that_throws_becomes_a_warning(page, errors, tmp_path, script, code):
    """A deck that threw at its author would cost a recording rather than save one."""
    page.goto(f"{script_page(tmp_path, f'threw-{code}.html', script)}?scene=13&t0=0&cues=13.1:beat@0.1")
    page.wait_for_function("() => window.__decktalk.warnings.length > 0")
    assert code in codes_of(page)
    assert not errors


# ---- what the page cannot honour ---------------------------------------------------------------


def test_an_attribute_the_registry_does_not_define_is_reported(page, tmp_path):
    """Every misspelling of every attribute is one condition, and this is the code that names it."""
    scene = """
    <div data-scene="14">
      <template data-slide="14.1"><p data-inn="show" data-describe="the line">a line</p></template>
    </div>
    """
    opened(page, write_page(tmp_path, "misspelled.html", scene))
    rows = warnings_of(page)
    assert [row["code"] for row in rows] == ["PAGE_ATTR_UNKNOWN"]
    assert rows[0]["attr"] == "data-inn"


def test_a_value_outside_its_published_set_is_reported(page, tmp_path):
    """A closed word is closed, and a range is the safe range, so a value outside either is named."""
    scene = """
    <div data-scene="14">
      <template data-slide="14.1">
        <p data-in="show" data-in-style="zoom" data-describe="the line">a line</p>
        <p data-in="late" data-in-seconds="9" data-describe="another line">another</p>
      </template>
    </div>
    """
    opened(page, write_page(tmp_path, "bad-value.html", scene))
    rows = [row for row in warnings_of(page) if row["code"] == "PAGE_BAD_VALUE"]
    assert {row["attr"] for row in rows} == {"data-in-style", "data-in-seconds"}


def test_a_slide_that_owns_no_listed_cue_is_reported(page, tmp_path):
    """A slide with no cue in the list never appears, so nothing it declares reaches the recording."""
    page.goto(f"{deck(tmp_path, 'unused.html')}?scene=1&t0=0&cues=1.1:ball@0.1")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    rows = [row for row in warnings_of(page) if row["code"] == "PAGE_SLIDE_UNUSED"]
    assert {row["slide"] for row in rows} == {"1.2", "1.3"}


def test_katex_refusing_a_value_leaves_the_readable_text(page, tmp_path):
    """The element's own text is the equation's readable fallback, which is what it falls back to."""
    scene = """
    <div data-scene="19">
      <template data-slide="19.1">
        <p class="bad" data-in="show" data-tex="\\frac{1}{" data-describe="a broken equation">one over</p>
      </template>
    </div>
    """
    settled(page, f"{write_page(tmp_path, 'katex-bad.html', scene, head=KATEX)}?freeze=19.1")
    assert "PAGE_KATEX_ERROR" in codes_of(page)
    assert page.evaluate("() => document.querySelector('.bad').textContent") == "one over"


MISTAKES = {
    # The transcript is the reason a class may ship at all, so a class with no description is refused.
    "class-undescribed": (
        {"PAGE_CLASS_UNDESCRIBED"},
        '<template data-slide="3.1"><p data-in="show" data-class="cancel:stale">h over h</p></template>',
    ),
    # A stagger over no children spreads one entrance over nothing, which is never what was meant.
    "stagger-empty": (
        {"PAGE_STAGGER_EMPTY"},
        '<template data-slide="3.1"><p data-in="tiles" data-stagger="0.08" data-describe="nothing">x</p></template>',
    ),
    # A spotlight walks its cued children, so a container with none of them brings nothing forward.
    "spotlight-empty": (
        {Code.PAGE_SPOTLIGHT_EMPTY.value},
        '<template data-slide="3.1"><ul data-spotlight><li data-describe="a point">one</li></ul></template>',
    ),
    # Zero candidates and two candidates are both guesses, and the page refuses to make either.
    "swap-ambiguous": (
        {"PAGE_SWAP_AMBIGUOUS"},
        '<template data-slide="3.1"><p data-in="fix" data-swaps data-describe="the right answer">three</p></template>',
    ),
    # A moment is qualified by the template it is written in, so one written outside has no owner.
    "moment-unknown": (
        {"PAGE_MOMENT_UNKNOWN"},
        '<p data-in="loose" data-describe="a line outside every slide">loose</p>'
        '<template data-slide="3.1"><p data-in="show" data-describe="the line">a line</p></template>',
    ),
    # The declared order makes the comparison exact, so this is an error and not a guess.
    "moment-order": (
        {"PAGE_MOMENT_ORDER"},
        '<template data-slide="3.1"><p data-in="show" data-out="show" data-describe="the line">a line</p></template>',
    ),
    # A scene that declares no slide and a slide that declares no id both reach no recording.
    "scene-empty": ({"PAGE_SCENE_EMPTY"}, ""),
    "slide-no-id": ({"PAGE_SLIDE_NO_ID"}, "<template></template>"),
    # Every moment local to a doubled id has two owners, which no cue id can tell apart.
    "slide-doubled": (
        {"PAGE_SLIDE_DOUBLED"},
        '<template data-slide="3.1"><p data-in="a" data-describe="one">one</p></template>'
        '<template data-slide="3.1"><p data-in="b" data-describe="two">two</p></template>',
    ),
    # A template nothing ever mounts is markup an author believes is on screen and is not.
    "template-ignored": (
        {"PAGE_TEMPLATE_IGNORED"},
        '<template data-slide="3.1"><template><p>never mounted</p></template></template>',
    ),
    # A recording of plain TeX is a recording nobody can use, so the page reports the missing typesetter.
    "katex-missing": (
        {"PAGE_KATEX_MISSING"},
        '<template data-slide="3.1"><p data-in="show" data-tex="x^2" data-describe="x squared">x</p></template>',
    ),
}
"""Every mistake a page can make in its markup alone, as the codes it is reported under and the scene 3
that makes it. The reason each is a mistake is the comment above its row."""


@pytest.mark.parametrize(("codes", "scene"), MISTAKES.values(), ids=MISTAKES.keys())
def test_a_mistake_in_the_markup_is_reported_under_its_own_code(page, tmp_path, codes, scene):
    opened(page, write_page(tmp_path, "mistake.html", f'<div data-scene="3">{scene}</div>'))
    assert set(codes_of(page)) >= codes


def test_a_listed_cue_nothing_owns_or_draws_is_reported(page, tmp_path):
    """A cue nothing owns mounts nothing, which is the one failure that empties a whole recording, and
    a cue that drifted between the page and the project file is almost never a cue anyone meant."""
    page.goto(f"{deck(tmp_path, 'orphan.html')}?scene=1&t0=0&cues=1.1:ball@0.1,1.1:nobody@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    assert set(codes_of(page)) >= {"PAGE_NO_OWNER", "PAGE_CUE_UNKNOWN"}


@pytest.mark.parametrize(
    ("promise", "code"),
    [("Promise.reject(new Error('nope'))", "PAGE_WAIT_REJECTED"), ("new Promise(() => {})", "PAGE_WAIT_UNSETTLED")],
)
def test_a_page_promise_that_rejects_or_never_settles_does_not_hold_the_page(page, tmp_path, promise, code):
    """A page's own readiness is the page's own business, and a recording that waited forever would
    cost more than a page drawn without one condition, so either is a warning and not a stall."""
    page.goto(deck(tmp_path, "waited.html", f"<script>DeckTalk.waitFor({promise});</script>" + MARKUP_SCENE))
    assert page.evaluate("() => window.__decktalk.ready") is True
    assert code in codes_of(page)


# ---- the reduced-motion render ------------------------------------------------------------------


@pytest.fixture
def quiet(page: Page) -> Iterator[Tab]:
    """A page that asks for reduced motion, which is what `motion.reduce` asks Chromium for."""
    browser = page.context.browser
    assert browser is not None
    other = Tab.of(browser.new_page(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce"))
    yield other
    other.page.close()


TRAVELLED = """
<div data-scene="20">
  <template data-slide="20.1">
    <p class="subject" data-in="show" data-in-style="rise" data-describe="the subject">a subject</p>
  </template>
</div>
"""


def test_a_reduced_render_keeps_every_length_and_drops_every_travel(quiet, tmp_path):
    """Nothing in the reduced render moves a cue, so the film is the same duration with the same captions."""
    url = write_page(tmp_path, "reduced.html", TRAVELLED)
    quiet.page.goto(f"{url}?scene=20&t0=0&cues=20.1:show@0.05")
    quiet.page.wait_for_function("() => window.__decktalk.fired.length === 1")
    assert quiet.page.evaluate("() => document.documentElement.classList.contains('dt-reduced')") is True
    playing = quiet.page.evaluate(
        "() => document.querySelector('.subject').getAnimations()"
        ".map((a) => [a.animationName, a.effect.getTiming().duration])"
    )
    assert playing == [["dt-in-fade", ENTRANCES["rise"].seconds * 1000]]
    assert not quiet.errors


def test_a_full_motion_page_keeps_the_travel_its_style_declares(page, tmp_path):
    """The reduced render is a render, so the same page opened without it moves exactly as it says."""
    url = write_page(tmp_path, "travelled.html", TRAVELLED)
    page.goto(f"{url}?scene=20&t0=0&cues=20.1:show@0.05")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    names = page.evaluate("() => document.querySelector('.subject').getAnimations().map((a) => a.animationName)")
    assert names == ["dt-in-rise"]


def test_the_motion_scale_multiplies_the_length_and_the_declared_span(page, tmp_path):
    """`motion.scale` reaches the page as one declaration on the root, and it moves both numbers together."""
    head = "<style>:root { --dt-motion-scale: 1.2 }</style>"
    url = write_page(tmp_path, "scaled.html", TRAVELLED, head=head)
    opened(page, url)
    assert page.evaluate("() => window.__decktalk.catalog[0].spans['20.1:show']") == pytest.approx(
        ENTRANCES["rise"].seconds * 1.2
    )
    page.goto(f"{url}?scene=20&t0=0&cues=20.1:show@0.05")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    length = page.evaluate("() => document.querySelector('.subject').getAnimations()[0].effect.getTiming().duration")
    assert length == pytest.approx(ENTRANCES["rise"].seconds * 1.2 * 1000)


def test_a_class_that_still_animates_under_reduced_motion_is_reported(quiet, tmp_path):
    """The page's own stylesheet owns the class, so the page's own stylesheet owes the reduction."""
    head = "<style>.stale { animation: fade 0.3s linear both } @keyframes fade { to { opacity: .2 } }</style>"
    scene = """
    <div data-scene="21">
      <template data-slide="21.1">
        <p data-in="show" data-class="cancel:stale" data-describe-class="cancel:the h is struck out"
           data-describe="the fraction">h over h</p>
      </template>
    </div>
    """
    opened(quiet.page, write_page(tmp_path, "not-reduced.html", scene, head=head))
    reported = quiet.page.evaluate("() => window.__decktalk.warnings")
    assert [row["code"] for row in reported] == ["PAGE_CLASS_NOT_REDUCED"]
    assert reported[0]["cue"] == "21.1:cancel"
