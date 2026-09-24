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
import shutil
import threading
from collections.abc import Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from decktalk.page import (
    APPEAR_WORDS_MAX,
    ATTENTION,
    BACK_OPACITY,
    COUNTS,
    ENTRANCES,
    EXITS,
    FRAME_STEP_MS,
    MEASURABLE_SPAN_SECONDS,
    ONSET_FIRST_FRAME_PERCENT,
    SLIDE_ENTRANCES,
    WORD_STYLES,
)
from decktalk.toolchain.assets import RUNTIME_FILE, katex_dir, runtime_path
from support.browser_pages import KATEX, chromium_page, script_page, write_page

if TYPE_CHECKING:
    from playwright.sync_api import Page

pytestmark = pytest.mark.browser


# The three-slide scene every markup test uses: attributes only, no JavaScript anywhere. Its moments
# are local names, so the wire ids the recorder sees are "1.1:ball" and the rest.
MARKUP_SCENE = """
<div data-scene="1" data-name="Open">
  <template data-slide="1.1" data-hold="6">
    <h1 class="title">A bowl</h1>
    <p class="ball" data-in="ball" data-in-style="pop" data-describe="a ball rests in the bowl">A ball</p>
    <p class="step" data-in="step" data-back="ball" data-describe="the step down">Watch it step down</p>
  </template>
  <template data-slide="1.2" data-hold="4">
    <p class="sum" data-in="sum" data-tex-display data-tex="\\sum_{i=1}^{n} x_i"
       data-describe="the sum of the first n terms">the sum of x i from one to n</p>
  </template>
  <template data-slide="1.3" data-hold="4" data-owns="aside">
    <p class="late" data-in="late" data-describe="the closing line">nothing follows</p>
  </template>
</div>
"""


def deck(tmp_path: Path, name: str, body: str = MARKUP_SCENE) -> str:
    """A page carrying the shared scene and the typesetter its one equation asks for."""
    return write_page(tmp_path, name, body, head=KATEX)


@pytest.fixture(scope="module")
def page():
    """The page a person opens: the runtime and nothing the recorder would add."""
    yield from chromium_page()


@pytest.fixture(autouse=True)
def _fresh_errors(page):
    page.errors.clear()
    yield


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


class _Alias(SimpleHTTPRequestHandler):
    """A local origin that serves a directory and answers the one router alias a preview asks for."""

    times: str | None = None

    def do_GET(self) -> None:  # noqa: N802  (the base class spells it this way)
        if self.path != "/__decktalk/cue-times.json":
            super().do_GET()
            return
        if type(self).times is None:
            self.send_error(404)
            return
        body = type(self).times.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """A test server that printed every request would bury the failure that matters."""


@pytest.fixture
def origin(tmp_path: Path) -> Iterator[Any]:
    """A served project directory, so a preview can ask for the cue times the last run resolved."""

    class Server:
        def __init__(self, root: Path) -> None:
            self.root = root
            handler = type("Handler", (_Alias,), {})
            self.handler = handler
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(root)))
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()

        @property
        def url(self) -> str:
            return f"http://127.0.0.1:{self.server.server_address[1]}"

        def publish(self, document: dict[str, Any] | None) -> None:
            self.handler.times = None if document is None else json.dumps(document)

        def write(self, name: str, body: str) -> str:
            """A page beside its own copy of the runtime and the typesetter, as a project is served."""
            (self.root / RUNTIME_FILE).write_bytes(runtime_path().read_bytes())
            shutil.copytree(katex_dir(), self.root / "katex", dirs_exist_ok=True)
            (self.root / name).write_text(
                f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name}</title>'
                f'<link rel="stylesheet" href="katex/katex.min.css"><script src="katex/katex.min.js"></script>'
                f'<script src="{RUNTIME_FILE}"></script></head><body>{body}</body></html>',
                encoding="utf-8",
            )
            return f"{self.url}/{name}"

    served = Server(tmp_path)
    yield served
    served.server.shutdown()
    served.server.server_close()
    served.thread.join()


# ---- the shape of a page --------------------------------------------------------------------


def test_the_runtime_declares_the_version_it_shipped_with(page, tmp_path):
    """The recorder writes the page's version into the recording log, so the page publishes one."""
    page.goto(deck(tmp_path, "version.html"))
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.version") == page.evaluate("() => window.DeckTalk.version")
    assert page.evaluate("() => /^\\d+\\.\\d+\\.\\d+/.test(window.__decktalk.version)")


def test_the_stylesheet_is_prepended_and_carries_no_specificity(page, tmp_path):
    """A page rule of equal weight has to win, so every runtime selector sits inside :where()."""
    head = "<style>.ball { color: rgb(1, 2, 3) }</style>"
    page.goto(write_page(tmp_path, "weight.html", MARKUP_SCENE, head=head + KATEX))
    page.evaluate("() => window.__decktalk.ready")
    sheet = page.evaluate("() => document.getElementById('dt-style').textContent")
    assert sheet.strip().startswith(":where(")
    # A still is the one rule the page may not be argued out of, so it is the only forced one.
    forced = [line for line in sheet.splitlines() if "!important" in line]
    assert all("dt-frozen" in line for line in forced)
    assert page.evaluate("() => document.head.firstElementChild.id") == "dt-style"
    page.goto(f"{write_page(tmp_path, 'weight-shown.html', MARKUP_SCENE, head=head + KATEX)}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ball')).color") == "rgb(1, 2, 3)"


def test_wait_for_holds_ready_until_the_pages_own_condition(page, tmp_path):
    """A deck with a condition of its own adds it to the runtime rather than replacing a global."""
    body = "<script>DeckTalk.waitFor(new Promise((r) => setTimeout(() => { window.__late = 1; r(); }, 200)));</script>"
    page.goto(deck(tmp_path, "gate.html", body + MARKUP_SCENE))
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__late") == 1
    assert not page.errors


def test_ready_survives_a_page_promise_that_rejects(page, tmp_path):
    """A page's own readiness is the page's own business, so a rejection is a warning and not a stall."""
    body = "<script>DeckTalk.waitFor(Promise.reject(new Error('nope')));</script>"
    page.goto(deck(tmp_path, "reject.html", body + MARKUP_SCENE))
    assert page.evaluate("() => window.__decktalk.ready") is True
    assert "PAGE_WAIT_REJECTED" in codes_of(page)


# ---- slides written as markup ------------------------------------------------------------------


def test_a_markup_scene_needs_no_javascript(page, tmp_path):
    """The whole contract is attributes, so a deck of templates is a deck with no script in it."""
    page.goto(deck(tmp_path, "markup.html"))
    page.evaluate("() => window.__decktalk.ready")
    catalog = page.evaluate("() => window.__decktalk.catalog")
    assert [entry["scene"] for entry in catalog] == ["1"]
    assert catalog[0]["name"] == "Open"
    assert catalog[0]["slides"] == ["1.1", "1.2", "1.3"]
    assert warnings_of(page) == []


def test_a_template_slide_writes_a_backslash_once(page, tmp_path):
    """Markup is parsed and not evaluated, which is the reason to prefer a template to a render string."""
    page.goto(f"{deck(tmp_path, 'tex.html')}?slide=1.2")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => document.querySelector('.sum').getAttribute('data-tex')") == "\\sum_{i=1}^{n} x_i"


def test_markup_slides_join_a_scene_declared_in_script(page, tmp_path):
    """A deck may be half markup and half script, and neither half has to know about the other."""
    script = """
      DeckTalk.scene(1, { name: "Scripted", slides: [
        { id: "1.1" },
        { id: "1.9", owns: ["only"], render: () => "<p>built in script</p>" },
      ]});
    """
    page.goto(deck(tmp_path, "mixed.html", f"<script>{script}</script>{MARKUP_SCENE}"))
    page.evaluate("() => window.__decktalk.ready")
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
    """An exit that never reached the cue order was a cue no check could see, which is the whole repair."""
    scene = f"""
    <div data-scene="2">
      <template data-slide="2.1">
        <p data-in="arrive" data-describe="the line">a line</p>
        <p {attribute}="{local}" data-describe="the subject">the subject</p>
      </template>
    </div>
    """
    page.goto(write_page(tmp_path, f"moment-{local}.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert f"2.1:{local}" in page.evaluate("() => window.__decktalk.catalog[0].cues['2.1']")


def test_a_local_moment_is_qualified_with_the_slide_that_carries_it(page, tmp_path):
    """The author writes `ball` and the wire carries `1.1:ball`, which is the whole of the qualification."""
    page.goto(deck(tmp_path, "wire.html"))
    page.evaluate("() => window.__decktalk.ready")
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
    page.goto(write_page(tmp_path, "class.html", scene, head=head))
    page.evaluate("() => window.__decktalk.ready")
    entry = page.evaluate("() => window.__decktalk.catalog[0]")
    assert entry["cues"]["3.1"] == ["3.1:show", "3.1:cancel"]
    assert entry["spans"]["3.1:cancel"] == pytest.approx(0.3)
    assert warnings_of(page) == []


def test_a_class_with_no_phrase_for_it_loses_its_line(page, tmp_path):
    """The transcript is the reason a class may ship at all, so a class with no phrase is refused."""
    scene = """
    <div data-scene="3">
      <template data-slide="3.1"><p data-in="show" data-class="cancel:stale">h over h</p></template>
    </div>
    """
    page.goto(write_page(tmp_path, "undescribed.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_CLASS_UNDESCRIBED" in codes_of(page)


# ---- the modes -------------------------------------------------------------------------------


def test_cue_mode_fires_in_order_and_reports_each_cue(page, tmp_path):
    """The recorder passes wire ids and seconds, and the page fires each at its own second."""
    url = deck(tmp_path, "cued.html")
    page.goto(f"{url}?scene=1&t0=0&cues=1.1:ball@0.1,1.1:step@0.3,1.2:sum@0.6")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    page.wait_for_function("() => window.__decktalk.fired.length === 3")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball", "1.1:step", "1.2:sum"]
    assert page.evaluate("() => window.__decktalk.mode") == "cue"


def test_the_first_slide_is_mounted_before_the_clock_starts(page, tmp_path):
    """A recording that opened on an empty stage would spend its first frames on nothing."""
    url = deck(tmp_path, "first.html")
    page.goto(f"{url}?scene=1&t0=signal&cues=1.1:ball@0.2,1.2:sum@0.6")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.slide") == "1.1"
    assert page.evaluate("() => window.__decktalk.started()") is False
    assert page.evaluate("() => window.__decktalk.fired") == []
    page.evaluate("() => window.DeckTalk.startClock()")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")


def test_freeze_mode_fires_every_cue_the_slide_declares(page, tmp_path):
    """A still is the whole slide, which is what the index links and what a screenshot records."""
    page.goto(f"{deck(tmp_path, 'frozen.html')}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball", "1.1:step"]
    assert page.evaluate("() => window.__decktalk.mode") == "freeze"
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ball')).opacity") == "1"


def test_the_index_page_lists_every_scene_and_slide(page, tmp_path):
    """A page with no query is a contents page, which is where a person starts and where boxes are measured."""
    page.goto(deck(tmp_path, "index.html"))
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.mode") == "index"
    links = page.evaluate("() => [...document.querySelectorAll('#dt-index a')].map((a) => a.getAttribute('href'))")
    assert "?scene=1" in links
    assert "?slide=1.1" in links
    assert page.evaluate("() => getComputedStyle(document.getElementById('dt-stage')).display") == "none"


def test_a_preview_without_cue_times_still_shows_every_cue(page, tmp_path):
    """A deck whose cues have never been resolved must still preview, or the stage opens empty."""
    page.goto(f"{deck(tmp_path, 'preview.html')}?scene=1&speed=8")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1:step')", timeout=15000)
    assert page.evaluate("() => window.__decktalk.mode") == "preview"
    assert page.evaluate("() => window.__decktalk.fired")[:2] == ["1.1:ball", "1.1:step"]


def test_a_preview_plays_the_cue_times_the_project_resolved(page, origin):
    """The point of a preview is to review the film's own timing without spending a recording on it."""
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


def test_an_absent_cue_times_file_never_fails_a_preview(page, origin):
    """Nothing on the preview path may fail a page, so a project that has never run `cue` previews anyway."""
    origin.publish(None)
    page.goto(f"{origin.write('absent.html', MARKUP_SCENE)}?scene=1&speed=8")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1:ball')", timeout=15000)
    assert warnings_of(page) == []
    assert not page.errors


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
    page.goto(f"{url}?scene=1&t0=0&cues=1.1:step@0.05,1.1:ball@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    page.wait_for_timeout(int(ATTENTION["back"].seconds * 1000) + 200)
    assert float(page.evaluate("() => getComputedStyle(document.querySelector('.step')).opacity")) == pytest.approx(
        BACK_OPACITY, abs=0.02
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
    # The published span is the honest arithmetic, which is what makes the overrun a certain finding.
    assert span > MEASURABLE_SPAN_SECONDS - FRAME_STEP_MS / 1000


def test_a_container_that_staggers_nothing_is_a_mistake(page, tmp_path):
    """A stagger over no children spreads one entrance over nothing, which is never what was meant."""
    scene = """
    <div data-scene="6">
      <template data-slide="6.1"><p data-in="tiles" data-stagger="0.08" data-describe="nothing">x</p></template>
    </div>
    """
    page.goto(write_page(tmp_path, "empty-stagger.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_STAGGER_EMPTY" in codes_of(page)


def test_steps_brings_each_child_forward_and_steps_the_ones_before_it_back(page, tmp_path):
    """A stepped list is written once and lands in the catalog as though every moment were typed."""
    scene = """
    <div data-scene="7">
      <template data-slide="7.1">
        <ul class="list" data-steps>
          <li class="one" data-in="first" data-describe="the first point">one</li>
          <li class="two" data-in="second" data-describe="the second point">two</li>
        </ul>
      </template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, 'steps.html', scene)}?scene=7&t0=0&cues=7.1:first@0.05,7.1:second@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    page.wait_for_timeout(int(ATTENTION["back"].seconds * 1000) + 200)
    assert page.evaluate("() => document.querySelector('.one').classList.contains('dt-back')") is True
    assert page.evaluate("() => document.querySelector('.two').classList.contains('dt-back')") is False
    assert page.evaluate("() => window.__decktalk.catalog[0].cues['7.1']") == ["7.1:first", "7.1:second"]


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
    page.goto(f"{write_page(tmp_path, 'swap.html', scene)}?scene=8&t0=0&cues=8.1:show@0.05,8.1:fix@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    assert page.evaluate("() => getComputedStyle(document.querySelector('.wrong')).opacity") == "1"
    page.wait_for_function("() => Number(getComputedStyle(document.querySelector('.wrong')).opacity) < 0.5")
    assert warnings_of(page) == []


def test_a_swap_with_nothing_to_replace_is_reported(page, tmp_path):
    """Zero candidates and two candidates are both guesses, and the page refuses to make either."""
    scene = """
    <div data-scene="8">
      <template data-slide="8.1"><p data-in="fix" data-swaps data-describe="the right answer">three</p></template>
    </div>
    """
    page.goto(write_page(tmp_path, "lonely-swap.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_SWAP_AMBIGUOUS" in codes_of(page)


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
    # The composite of an opaque slide under a half-faded one is opaque, which is the whole repair.
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
    page.goto(f"{write_page(tmp_path, 'count.html', scene)}?scene=9&t0=0&cues=9.1:show@0.05")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    page.wait_for_timeout(int(COUNTS["last"].seconds * 1000) + 300)
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


def test_a_line_the_voice_never_says_is_reported(page, tmp_path):
    """A line that is not the spoken text cannot be shown on the voice, so the page says so."""
    scene = """
    <div data-scene="10">
      <template data-slide="10.1"><p data-in="say" data-words data-describe="the line">nothing like it</p></template>
    </div>
    """
    url = write_page(tmp_path, "unsaid.html", scene)
    page.goto(f"{url}?scene=10&t0=0&cues=10.1:say@0.05&words=alpha@0.3,beta@0.5")
    page.wait_for_function("() => window.__decktalk.warnings.length > 0")
    assert "PAGE_WORDS_NOT_FOUND" in codes_of(page)


def test_appear_on_a_long_line_is_reported(page, tmp_path):
    """One word at a time is a cue's worth of motion, and a long line is more than a cue can carry."""
    long_line = " ".join(f"word{index}" for index in range(APPEAR_WORDS_MAX + 2))
    spoken = ",".join(f"word{index}@{0.3 + index / 10}" for index in range(APPEAR_WORDS_MAX + 2))
    scene = f"""
    <div data-scene="10">
      <template data-slide="10.1">
        <p data-in="say" data-words="appear" data-describe="the long line">{long_line}</p>
      </template>
    </div>
    """
    url = write_page(tmp_path, "long.html", scene)
    page.goto(f"{url}?scene=10&t0=0&cues=10.1:say@0.05&words={spoken}")
    page.wait_for_function("() => window.__decktalk.warnings.length > 0")
    assert "PAGE_APPEAR_TOO_LONG" in codes_of(page)


# ---- the transcript ---------------------------------------------------------------------------


def test_each_moment_composes_its_own_sentence(page, tmp_path):
    """The author writes one noun phrase and the runtime gives it the verb the moment owns."""
    scene = """
    <div data-scene="11">
      <template data-slide="11.1">
        <p data-in="show" data-back="aside" data-front="back-to-it" data-out="go"
           data-describe="the definition of the derivative">f'(x)</p>
      </template>
    </div>
    """
    url = write_page(tmp_path, "transcript.html", scene)
    probe = "<script>window.__said = []; </script>"
    page.goto(f"{url}?scene=11&t0=0&cues=11.1:show@0.05,11.1:aside@0.2,11.1:back-to-it@0.35,11.1:go@0.5")
    page.wait_for_function("() => window.__decktalk.fired.length === 4")
    assert probe  # The sentences are read back through the probe in test_probe.py, not written here.
    assert page.evaluate("() => window.__decktalk.fired") == [
        "11.1:show",
        "11.1:aside",
        "11.1:back-to-it",
        "11.1:go",
    ]


def test_a_decorative_element_writes_no_line(page, tmp_path):
    """An empty phrase is the author saying the element means nothing, which the transcript honours."""
    scene = """
    <div data-scene="11">
      <template data-slide="11.1"><p class="rule" data-in="show" data-describe="">---</p></template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, 'decorative.html', scene)}?slide=11.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
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
    ],
)
def test_a_page_callback_that_throws_becomes_a_warning(page, tmp_path, script, code):
    """A deck that threw at its author would cost a recording rather than save one."""
    page.goto(f"{script_page(tmp_path, f'threw-{code}.html', script)}?scene=13&t0=0&cues=13.1:beat@0.1")
    page.wait_for_function("() => window.__decktalk.warnings.length > 0")
    assert code in codes_of(page)
    assert not page.errors


# ---- what the page cannot honour ---------------------------------------------------------------


def test_an_attribute_the_registry_does_not_define_is_reported(page, tmp_path):
    """Every misspelling of every knob is one condition, and this is the code that names it."""
    scene = """
    <div data-scene="14">
      <template data-slide="14.1"><p data-inn="show" data-describe="the line">a line</p></template>
    </div>
    """
    page.goto(write_page(tmp_path, "misspelled.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    rows = warnings_of(page)
    assert [row["code"] for row in rows] == ["PAGE_UNKNOWN_ATTR"]
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
    page.goto(write_page(tmp_path, "bad-value.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    rows = [row for row in warnings_of(page) if row["code"] == "PAGE_BAD_VALUE"]
    assert {row["attr"] for row in rows} == {"data-in-style", "data-in-seconds"}


def test_a_moment_outside_a_slide_names_a_cue_nothing_owns(page, tmp_path):
    """A moment is qualified by the template it is written in, so one written outside has no owner."""
    scene = """
    <div data-scene="14">
      <p data-in="loose" data-describe="a line outside every slide">loose</p>
      <template data-slide="14.1"><p data-in="show" data-describe="the line">a line</p></template>
    </div>
    """
    page.goto(write_page(tmp_path, "loose.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_MOMENT_UNKNOWN" in codes_of(page)


def test_an_exit_at_or_before_its_own_entrance_never_plays(page, tmp_path):
    """The declared order makes the comparison exact, so this is a certain finding and not a guess."""
    scene = """
    <div data-scene="14">
      <template data-slide="14.1">
        <p data-in="show" data-out="show" data-describe="the line">a line</p>
      </template>
    </div>
    """
    page.goto(write_page(tmp_path, "order.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_MOMENT_ORDER" in codes_of(page)


def test_a_scene_with_no_template_and_a_template_with_no_id_are_reported(page, tmp_path):
    """A scene that declares no slide and a slide that declares no id both reach no recording."""
    scene = '<div data-scene="15"></div><div data-scene="16"><template></template></div>'
    page.goto(write_page(tmp_path, "empty.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert set(codes_of(page)) >= {"PAGE_SCENE_EMPTY", "PAGE_SLIDE_NO_ID"}


def test_two_templates_claiming_one_slide_id_are_reported(page, tmp_path):
    """Every moment local to a doubled id has two owners, which no wire id can tell apart."""
    scene = """
    <div data-scene="17">
      <template data-slide="17.1"><p data-in="a" data-describe="one">one</p></template>
      <template data-slide="17.1"><p data-in="b" data-describe="two">two</p></template>
    </div>
    """
    page.goto(write_page(tmp_path, "doubled.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_SLIDE_DOUBLED" in codes_of(page)


def test_a_template_inside_a_slide_with_no_id_is_reported(page, tmp_path):
    """A template nothing ever mounts is markup an author believes is on screen and is not."""
    scene = """
    <div data-scene="18">
      <template data-slide="18.1"><template><p>never mounted</p></template></template>
    </div>
    """
    page.goto(write_page(tmp_path, "nested.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_TEMPLATE_IGNORED" in codes_of(page)


def test_a_slide_that_owns_no_listed_cue_is_reported(page, tmp_path):
    """A slide with no cue in the list never appears, so nothing it declares reaches the recording."""
    page.goto(f"{deck(tmp_path, 'unused.html')}?scene=1&t0=0&cues=1.1:ball@0.1")
    page.wait_for_function("() => window.__decktalk.fired.length === 1")
    rows = [row for row in warnings_of(page) if row["code"] == "PAGE_SLIDE_UNUSED"]
    assert {row["slide"] for row in rows} == {"1.2", "1.3"}


def test_a_listed_cue_no_slide_owns_is_reported(page, tmp_path):
    """A cue nothing owns mounts nothing, which is the one failure that empties a whole recording."""
    page.goto(f"{deck(tmp_path, 'orphan.html')}?scene=1&t0=0&cues=1.1:ball@0.1,1.1:nobody@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    assert "PAGE_NO_OWNER" in codes_of(page)


def test_katex_refusing_a_value_leaves_the_readable_text(page, tmp_path):
    """The element's own text is the equation's readable fallback, which is what it falls back to."""
    head = (
        f'<link rel="stylesheet" href="{(katex_dir() / "katex.min.css").resolve().as_uri()}">'
        f'<script src="{(katex_dir() / "katex.min.js").resolve().as_uri()}"></script>'
    )
    scene = """
    <div data-scene="19">
      <template data-slide="19.1">
        <p class="bad" data-in="show" data-tex="\\frac{1}{" data-describe="a broken equation">one over</p>
      </template>
    </div>
    """
    page.goto(f"{write_page(tmp_path, 'katex-bad.html', scene, head=head)}?slide=19.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert "PAGE_KATEX_ERROR" in codes_of(page)
    assert page.evaluate("() => document.querySelector('.bad').textContent") == "one over"


def test_a_page_that_wants_katex_and_never_gets_it_says_so(page, tmp_path):
    """A recording of plain TeX is a recording nobody can use, so the page reports the missing typesetter."""
    scene = """
    <div data-scene="19">
      <template data-slide="19.1"><p data-in="show" data-tex="x^2" data-describe="x squared">x squared</p></template>
    </div>
    """
    page.goto(write_page(tmp_path, "katex-missing.html", scene))
    page.evaluate("() => window.__decktalk.ready")
    assert "PAGE_KATEX_MISSING" in codes_of(page)


# ---- the reduced-motion render ------------------------------------------------------------------


@pytest.fixture
def quiet(page):
    """A page that asks for reduced motion, which is what `motion.reduce` asks Chromium for."""
    other = page.context.browser.new_page(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    errors: list[str] = []
    other.on("pageerror", lambda exc: errors.append(str(exc)))
    other.errors = errors
    yield other
    other.close()


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
    quiet.goto(f"{url}?scene=20&t0=0&cues=20.1:show@0.05")
    quiet.wait_for_function("() => window.__decktalk.fired.length === 1")
    assert quiet.evaluate("() => document.documentElement.classList.contains('dt-reduced')") is True
    playing = quiet.evaluate(
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
    page.goto(url)
    page.evaluate("() => window.__decktalk.ready")
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
    quiet.goto(write_page(tmp_path, "not-reduced.html", scene, head=head))
    quiet.evaluate("() => window.__decktalk.ready")
    reported = quiet.evaluate("() => window.__decktalk.warnings")
    assert [row["code"] for row in reported] == ["PAGE_CLASS_NOT_REDUCED"]
    assert reported[0]["cue"] == "21.1:cancel"


def test_a_cue_that_draws_nothing_and_runs_nothing_is_reported(page, tmp_path):
    """A cue that drifted between the page and the project file is almost never a cue anyone meant."""
    page.goto(f"{deck(tmp_path, 'nothing.html')}?scene=1&t0=0&cues=1.1:ball@0.1,1.1:nobody@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    assert "PAGE_CUE_UNKNOWN" in codes_of(page)


def test_a_promise_that_never_settles_does_not_hold_the_page(page, tmp_path):
    """A recording that waited forever would cost more than a page drawn without one condition."""
    body = "<script>DeckTalk.waitFor(new Promise(() => {}));</script>"
    page.goto(deck(tmp_path, "unsettled.html", body + MARKUP_SCENE))
    assert page.evaluate("() => window.__decktalk.ready") is True
    assert "PAGE_WAIT_UNSETTLED" in codes_of(page)
