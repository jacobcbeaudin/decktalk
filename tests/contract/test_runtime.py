"""The page runtime, in a real headless Chromium. Skipped when Chromium is unavailable.

Every page here is written by the test, so this module measures the runtime and nothing else. It
opens each page the way a person does, with no probe injected, so what it holds to is what a deck
gets on its own. The instrumentation a command injects is `tests/contract/test_probe.py`, and what the
scaffold's own pages do with the runtime is `tests/decktalk/scaffold/test_scaffold.py`.

    uv run pytest -m browser
"""

from __future__ import annotations

import pytest

from decktalk.toolchain.assets import RUNTIME_FILE
from decktalk.verdicts import Verdict
from support.browser_pages import (
    KATEX,
    MARKUP_SCENE,
    chromium_page,
    script_page,
    served_page,
    warnings_of,
    write_page,
)

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def page():
    """The page a person opens: decktalk-runtime.js and nothing a command injected."""
    yield from chromium_page()


@pytest.fixture(autouse=True)
def _fresh_errors(page):
    """One page serves every test, so each test starts with an empty error list of its own."""
    page.errors.clear()
    yield


# ---- the shape of a page ---------------------------------------------------------------


def test_the_runtime_declares_the_version_it_shipped_with(page, tmp_path):
    """DeckTalk.version is the package version, so a page and the CLI that records it can be compared."""
    from decktalk import __version__

    page.goto(write_page(tmp_path, "version.html", MARKUP_SCENE))
    assert page.evaluate("() => DeckTalk.version") == page.evaluate("() => window.__decktalk.version")
    if __version__ != "0+unknown":
        assert page.evaluate("() => DeckTalk.version") == __version__


def test_the_stylesheet_is_prepended_and_carries_no_specificity(page, tmp_path):
    """Every runtime selector sits in :where(), so one page class wins over it, and it is the first sheet."""
    head = "<style>.mine { opacity: .5 }</style>"
    url = write_page(tmp_path, "where.html", MARKUP_SCENE.replace('class="title"', 'class="mine"'), head=head)
    page.goto(f"{url}?scene=1&speed=40")
    page.wait_for_function("() => window.__decktalk.slide === '1.1'")
    assert page.evaluate("() => document.head.firstElementChild.id") == "dt-style"
    assert page.evaluate("() => document.head.firstElementChild.textContent.includes(':where(.dt-reveal)')")
    # .mine is one class and :where(.dt-reveal) is none, so the page wins even on an unrevealed element.
    page.evaluate("() => document.querySelector('.mine').classList.add('dt-reveal')")
    assert page.evaluate("() => getComputedStyle(document.querySelector('.mine')).opacity") == "0.5"


def test_wait_for_holds_ready_until_the_pages_own_condition(page, tmp_path):
    """DeckTalk.waitFor adds a readiness condition instead of replacing a global."""
    script = (
        "window.__late = false;"
        "DeckTalk.waitFor(new Promise((r) => setTimeout(() => { window.__late = true; r(); }, 300)));"
        "DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => `<p>hi</p>` } ] });"
    )
    page.goto(script_page(tmp_path, "waitfor.html", script))
    assert page.evaluate("() => window.__decktalk.ready instanceof Promise")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__late") is True
    assert not page.errors


def test_ready_survives_a_page_promise_that_rejects(page, tmp_path):
    """A rejected DeckTalk.waitFor promise is a warning, because every reader treats ready as a promise
    that resolves, and a rejection would leave the index page blank and the catalog without boxes."""
    script = (
        "DeckTalk.waitFor(Promise.reject(new Error('no network')));"
        "DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => `<p>hi</p>` } ] });"
    )
    page.goto(script_page(tmp_path, "reject.html", script))
    assert page.evaluate("() => window.__decktalk.ready") is True
    assert any("rejected" in w for w in warnings_of(page))
    assert page.evaluate("() => !!document.getElementById('dt-index')")


# ---- slides written as markup ----------------------------------------------------------


def test_a_markup_scene_needs_no_javascript(page, tmp_path):
    """A [data-scene] wrapper of <template data-slide> elements is a whole scene, with no script."""
    page.goto(write_page(tmp_path, "markup.html", MARKUP_SCENE))
    catalog = page.evaluate("() => window.__decktalk.catalog")
    assert [c["scene"] for c in catalog] == ["1"]
    assert catalog[0]["name"] == "Open" and catalog[0]["camera"] == "push"
    assert catalog[0]["slides"] == ["1.1", "1.2", "1.3"]
    # A slide's cues come from its own markup, in document order, with no preview object needed.
    assert catalog[0]["cues"] == {"1.1": ["1.1ball", "1.1count"], "1.2": ["1.2sum"], "1.3": ["odd-one"]}
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_a_template_slide_writes_a_backslash_once(page, tmp_path):
    """Markup is not a JavaScript string, so TeX carries single backslashes and typesets."""
    page.goto(f"{write_page(tmp_path, 'tex.html', MARKUP_SCENE, head=KATEX)}?slide=1.2")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => document.querySelectorAll('[data-tex][data-typeset] .katex').length") == 1
    assert page.evaluate("() => !document.querySelector('.katex-error')")
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_a_template_fills_a_slide_that_defines_no_render(page, tmp_path):
    """A scene registered in script takes each slide's markup from the template with its id."""
    body = (
        '<div data-scene="2"><template data-slide="2.1"><p data-cue="2.1a">from markup</p></template>'
        '<template data-slide="2.2"><p>unused</p></template></div>'
        "<script>DeckTalk.scene(2, { name: 'Mixed', slides: ["
        " { id: '2.1' },"
        " { id: '2.2', render: () => `<p data-cue=\"2.2a\">from render</p>` } ] });</script>"
    )
    page.goto(f"{write_page(tmp_path, 'mixed.html', body)}?slide=2.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => document.querySelector('.dt-slide p').textContent") == "from markup"
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert warnings == ['slide "2.2" has both a render function and a <template data-slide>, so the template is unused']


def test_markup_slides_join_a_scene_declared_in_script(page, tmp_path):
    """A template whose slide id the script never declared is appended to that scene."""
    body = (
        '<div data-scene="2"><template data-slide="2.2"><p data-cue="2.2a">second</p></template></div>'
        "<script>DeckTalk.scene(2, { slides: [ { id: '2.1', render: () => `<p>first</p>` } ] });</script>"
    )
    page.goto(write_page(tmp_path, "join.html", body))
    assert page.evaluate("() => window.__decktalk.catalog[0].slides") == ["2.1", "2.2"]
    assert not page.errors


# ---- the first frame of a recording ----------------------------------------------------


def test_the_first_slide_is_mounted_before_the_clock_starts(page, tmp_path):
    """The stage is never empty on the first frame the recorder keeps.

    The recorder covers the page, settles, removes the cover and starts the clock on the next
    animation frame, and that frame is t=0. A first slide mounted from the clock's own queue is
    therefore drawn one frame *after* t=0, and the recording opens on one white frame. The first
    slide is mounted while the cover is still up instead, and it enters with no animation.
    """
    url = write_page(tmp_path, "race.html", MARKUP_SCENE)
    page.goto(f"{url}?scene=1&t0=signal&cues=1.1ball@0.4,1.1count@0.8")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.fired") == []  # the clock has not started
    assert page.evaluate("() => window.__decktalk.slide") == "1.1"
    assert page.evaluate("() => document.querySelectorAll('#dt-pan .dt-slide').length") == 1
    slide = "document.querySelector('#dt-pan .dt-slide')"
    assert page.evaluate(f"() => getComputedStyle({slide}).animationName") == "none"
    assert page.evaluate(f"() => getComputedStyle({slide}).opacity") == "1"
    assert page.evaluate("() => getComputedStyle(document.querySelector('.title')).opacity") == "1"
    # The slide's own reveals still wait for their cues, which start on the recorder's signal.
    assert (
        page.evaluate("() => document.querySelector('[data-cue=\"1.1ball\"]').classList.contains('dt-shown')") is False
    )
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2", timeout=3000)
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1ball", "1.1count"]
    assert warnings_of(page) == []
    assert not page.errors


def test_a_later_slide_still_enters_on_its_own_cue(page, tmp_path):
    """Only the first mount skips the entrance, so a mid-section slide change still crossfades."""
    page.goto(f"{write_page(tmp_path, 'enter.html', MARKUP_SCENE)}?scene=1&t0=0&cues=1.1ball@0.1,1.2sum@0.3")
    page.wait_for_function("() => window.__decktalk.slide === '1.2'")
    mounted = "document.querySelector('#dt-pan [data-slide=\"1.2\"]')"
    assert page.evaluate(f"() => getComputedStyle({mounted}).animationName") in (
        "dt-fadein",
        "none",  # the .35 s crossfade may already have finished
    )
    assert page.evaluate(f"() => {mounted}.classList.contains('dt-enter')")


def test_a_template_with_no_slide_id_is_ignored_with_a_warning(page, tmp_path):
    """An empty data-slide would be a prefix of every cue id, so the slide is dropped rather than
    made the owner of the whole page."""
    body = (
        '<div data-scene="1"><template data-slide><p data-cue="1.1a">nameless</p></template>'
        '<template data-slide="1.1"><p data-cue="1.1a">named</p></template></div>'
    )
    page.goto(write_page(tmp_path, "noid.html", body))
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.catalog[0].slides") == ["1.1"]
    assert any("no id" in w for w in warnings_of(page))


def test_a_template_on_a_scripted_slide_warns_about_the_fields_it_cannot_give(page, tmp_path):
    """A slide the script declared takes only the template's markup, so its other attributes warn."""
    body = (
        '<div data-scene="1"><template data-slide="1.1" data-hold="20" data-preview="1.1a@2">'
        '<p data-cue="1.1a">from markup</p></template></div>'
        "<script>DeckTalk.scene(1, { slides: [ { id: '1.1', hold: 4 } ] });</script>"
    )
    page.goto(write_page(tmp_path, "mixed.html", body))
    page.evaluate("() => window.__decktalk.ready")
    assert any("hold, owns and preview are ignored" in w for w in warnings_of(page))
    # The warning is only worth having if the runtime does what it says, so the script's hold stands.
    assert page.evaluate("() => DeckTalk.findSlide('1.1').slide.hold") == 4


# ---- cue ownership ---------------------------------------------------------------------


def test_the_id_prefix_is_the_ownership_rule(page, tmp_path):
    """A cue belongs to the slide whose id is the longest prefix of the cue id."""
    body = (
        '<div data-scene="4">'
        '<template data-slide="4.2"><p data-cue="4.2a">first</p></template>'
        '<template data-slide="4.2b"><p data-cue="4.2b1">second</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'prefix.html', body)}?scene=4&t0=0&cues=4.2a@0.1,4.2b1@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    # "4.2b" is a longer prefix of "4.2b1" than "4.2" is, so the second cue mounted the second slide.
    assert page.evaluate("() => window.__decktalk.slide") == "4.2b"
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_preview_seconds_grant_no_ownership(page, tmp_path):
    """`preview` is timing only, so a cue no slide id or owns list claims is unknown."""
    script = (
        "DeckTalk.scene(5, { slides: [ { id: '5.1', preview: { 'borrowed': 1 },"
        ' render: () => `<p data-cue="borrowed">mine?</p>` } ] });'
    )
    page.goto(f"{script_page(tmp_path, 'preview.html', script)}?scene=5&t0=0&cues=borrowed@0.1")
    page.wait_for_timeout(300)
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert "unknown cue id borrowed (no slide id or owns list matches it)" in warnings, warnings
    assert "no slide owns any listed cue, so nothing will mount" in warnings, warnings


def test_owns_claims_a_cue_id_that_carries_no_slide_id(page, tmp_path):
    """`owns` is the exception the prefix rule needs, and it is enough on its own."""
    page.goto(f"{write_page(tmp_path, 'owns.html', MARKUP_SCENE)}?scene=1&t0=0&cues=odd-one@0.1")
    page.wait_for_function("() => window.__decktalk.fired.includes('odd-one')")
    assert page.evaluate("() => window.__decktalk.slide") == "1.3"
    assert page.evaluate("() => document.querySelector('[data-cue=\"odd-one\"]').classList.contains('dt-shown')")


# ---- modes ------------------------------------------------------------------------------


def test_cue_mode_fires_in_order_and_logs_each_cue(page, tmp_path):
    cues = "1.1ball@0.3,1.1count@0.9,1.2sum@1.4,odd-one@1.8"
    page.goto(f"{write_page(tmp_path, 'cues.html', MARKUP_SCENE)}?scene=1&t0=0&cues={cues}")
    page.wait_for_function("() => window.__decktalk.fired.length >= 4", timeout=5000)
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1ball", "1.1count", "1.2sum", "odd-one"]
    assert page.evaluate("() => document.body.dataset.done") == "1"
    page.wait_for_function("() => window.__decktalk.cueLog.every((e) => e.after !== null)")
    log = page.evaluate("() => window.__decktalk.cueLog")
    for e, due in zip(log, (0.3, 0.9, 1.4, 1.8), strict=True):
        assert e["due"] == due and e["ran"] >= due, e
        assert e["frame"] <= e["ran"] + 0.001 and e["frame"] < e["next"] < e["after"], e
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_each_cue_log_row_carries_what_its_reveals_describe(page, tmp_path):
    """The transcript is built from these strings, so the row a recording leaves has to carry them."""
    cues = "1.1ball@0.3,1.1count@0.9,1.2sum@1.4"
    page.goto(f"{write_page(tmp_path, 'described.html', MARKUP_SCENE)}?scene=1&t0=0&cues={cues}")
    page.wait_for_function("() => window.__decktalk.cueLog.length >= 3", timeout=5000)
    described = {e["id"]: e["describe"] for e in page.evaluate("() => window.__decktalk.cueLog")}
    # Only 1.1ball carries data-describe in the scene, and a reveal without one writes null.
    assert described == {"1.1ball": "a ball rests in the bowl", "1.1count": None, "1.2sum": None}
    assert not page.errors


def test_preview_mode_uses_holds_and_preview_seconds(page, tmp_path):
    page.goto(f"{write_page(tmp_path, 'preview.html', MARKUP_SCENE)}?scene=1&speed=20")
    assert page.evaluate("() => window.__decktalk.mode") == "preview"
    assert page.evaluate("() => window.__decktalk.slide") == "1.1"
    page.wait_for_function("() => document.body.dataset.done === '1'", timeout=5000)
    page.wait_for_function("() => window.__decktalk.fired.includes('1.2sum')", timeout=5000)


def test_signal_mode_waits_for_start_clock(page, tmp_path):
    """With t0=signal the clock does not start at load, and starts when the recorder says so."""
    page.goto(f"{write_page(tmp_path, 'signal.html', MARKUP_SCENE)}?scene=1&t0=signal&cues=1.1ball@0.1")
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__decktalk.fired") == []
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1ball')", timeout=2000)


def test_freeze_mode_reveals_everything(page, tmp_path):
    page.goto(f"{write_page(tmp_path, 'freeze.html', MARKUP_SCENE)}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.mode") == "freeze"
    hidden = "() => [...document.querySelectorAll('.dt-reveal')].filter(e => !e.classList.contains('dt-shown')).length"
    assert page.evaluate(hidden) == 0
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1ball", "1.1count"]
    assert not page.errors


# ---- handlers ----------------------------------------------------------------------------

# Two slides whose handlers record what they receive. Slide 1.2 also keeps a zero-argument handler.
HANDLER_PAGE = """
window.__got = [];
DeckTalk.scene(1, { slides: [
  { id: '1.1', preview: { '1.1a': 0.1 }, render: () => `<p data-cue="1.1a">a</p>`,
    enter: (slide, ctx) => window.__got.push({ kind: 'enter', slide: slide.dataset.slide, ctx }),
    on: { '1.1a': (slide, ctx) => window.__got.push({ kind: 'on', slide: slide.dataset.slide,
      current: slide === document.querySelector('.dt-slide:not(.dt-leave)'), ctx }) } },
  { id: '1.2', preview: { '1.2a': 0.1 }, render: () => `<p data-cue="1.2a">b</p>`,
    on: { '1.2a': () => window.__got.push({ kind: 'zero' }) } },
]});
DeckTalk.on('1.2a', (slide, ctx) => window.__got.push({ kind: 'global', slide: slide.dataset.slide, ctx }));
DeckTalk.on('1.2a', () => window.__got.push({ kind: 'global-zero' }));
"""


def test_slide_handlers_receive_the_slide_and_ctx(page, tmp_path):
    """A slide's on[id] is called with the mounted slide element and { id, at, frozen, slideId }."""
    page.goto(f"{script_page(tmp_path, 'handlers.html', HANDLER_PAGE)}?scene=1&t0=0&cues=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    got = page.evaluate("() => window.__got")
    on = next(g for g in got if g["kind"] == "on")
    assert on["slide"] == "1.1" and on["current"] is True
    assert set(on["ctx"]) == {"id", "at", "frozen", "slideId"}
    assert on["ctx"]["id"] == "1.1a" and on["ctx"]["slideId"] == "1.1" and on["ctx"]["frozen"] is False
    assert 0.1 <= on["ctx"]["at"] < 0.4, on
    assert any(g["kind"] == "zero" for g in got), got
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_global_handlers_receive_the_slide_and_ctx(page, tmp_path):
    """Every DeckTalk.on handler is called with the slide mounted when the cue fires."""
    page.goto(f"{script_page(tmp_path, 'handlers.html', HANDLER_PAGE)}?scene=1&t0=0&cues=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    got = page.evaluate("() => window.__got")
    glob = next(g for g in got if g["kind"] == "global")
    assert glob["slide"] == "1.2"
    assert glob["ctx"]["id"] == "1.2a" and glob["ctx"]["slideId"] == "1.2" and glob["ctx"]["frozen"] is False
    assert glob["ctx"]["at"] >= 0.4, glob
    kinds = [g["kind"] for g in got]
    assert kinds.index("zero") < kinds.index("global") < kinds.index("global-zero"), kinds


def test_enter_receives_the_same_ctx(page, tmp_path):
    """A slide's enter gets the slide and a ctx whose id and slideId are the slide id, frozen or not."""
    url = script_page(tmp_path, "handlers.html", HANDLER_PAGE)
    page.goto(f"{url}?scene=1&t0=0&cues=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    enter = next(g for g in page.evaluate("() => window.__got") if g["kind"] == "enter")
    assert enter["slide"] == "1.1"
    assert set(enter["ctx"]) == {"id", "at", "frozen", "slideId"}
    assert enter["ctx"]["id"] == "1.1" and enter["ctx"]["slideId"] == "1.1" and enter["ctx"]["frozen"] is False
    page.goto(f"{url}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    frozen = [g for g in page.evaluate("() => window.__got") if g["kind"] in ("enter", "on")]
    assert [g["kind"] for g in frozen] == ["enter", "on"]
    assert all(g["ctx"]["frozen"] is True and g["ctx"]["slideId"] == "1.1" for g in frozen), frozen


def test_an_unlisted_cue_id_reaches_a_handler(page, tmp_path):
    """A cue no slide owns still fires when a DeckTalk.on handler is waiting for it."""
    body = (
        '<div data-scene="1"><template data-slide="1.1"><p data-cue="1.1a">a</p></template></div>'
        "<script>window.__hits = []; DeckTalk.on('custom', () => window.__hits.push('custom'));</script>"
    )
    page.goto(f"{write_page(tmp_path, 'custom.html', body)}?scene=1&t0=0&cues=1.1a@0.1,custom@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    assert page.evaluate("() => window.__hits") == ["custom"]
    assert page.evaluate("() => window.__decktalk.warnings") == []


# ---- text modes --------------------------------------------------------------------------

# A data-text="spoken" caption styled to show every word unlit at its cue and light each one as it is spoken, with the
# word being spoken, the last lit one, in the accent.
SYNC_PAGE = """
<style>
  .cap .dt-word { opacity: 1; color: rgb(113, 113, 122); transition: none; }
  .cap .dt-word.dt-shown { color: rgb(9, 9, 11); }
  .cap .dt-word.dt-shown:has(+ .dt-word:not(.dt-shown)) { color: rgb(44, 31, 234); }
</style>
<div data-scene="1">
  <template data-slide="1.1">
    <p class="cap" data-cue="1.1cap" data-text="spoken">The height is the error, so lower is better.</p>
  </template>
</div>
"""


def test_synced_caption_lights_each_word_as_it_is_spoken(page, tmp_path):
    """A data-text="spoken" caption wraps every word at its cue and lights each one at its spoken second."""
    words = "The@0.1,height@0.15,is@0.2,the@0.25,error@0.3,so@0.35,lower@6,is@6.2,better@6.4"
    url = write_page(tmp_path, "sync.html", SYNC_PAGE)
    page.goto(f"{url}?scene=1&t0=0&cues=1.1cap@0.05&words={words}")
    page.wait_for_function("() => document.querySelectorAll('.cap .dt-word.dt-shown').length >= 6", timeout=3000)
    assert page.evaluate("() => document.querySelectorAll('.cap .dt-word').length") == 9
    assert page.evaluate("() => document.querySelectorAll('.cap .dt-word.dt-shown').length") == 6
    style = "(sel) => { const s = getComputedStyle(document.querySelector(sel)); return [s.opacity, s.color]; }"
    assert page.evaluate(style, ".cap .dt-word:not(.dt-shown)") == ["1", "rgb(113, 113, 122)"]
    assert page.evaluate(style, ".cap .dt-word.dt-shown:has(+ .dt-word:not(.dt-shown))") == ["1", "rgb(44, 31, 234)"]
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert page.evaluate("() => window.__decktalk.spokenLog")[0]["n"] == 9


def test_spoken_text_has_no_container_animation(page, tmp_path):
    """A data-text="spoken" element without data-reveal gets data-reveal="instant", so it never fades or rises."""
    page.goto(f"{write_page(tmp_path, 'sync.html', SYNC_PAGE)}?scene=1&t0=0&cues=1.1cap@0.05")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1cap')")
    cap = "document.querySelector('[data-cue=\"1.1cap\"]')"
    assert page.evaluate(f"() => {cap}.getAttribute('data-reveal')") == "instant"
    assert page.evaluate(f"() => getComputedStyle({cap}).animationName") == "none"


def test_spoken_text_keeps_an_explicit_reveal(page, tmp_path):
    """A data-text="spoken" element that sets its own data-reveal keeps it."""
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        '<p data-delay="0" data-text="spoken" data-reveal="fade">Hello there</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'syncfx.html', body)}?scene=1")
    page.wait_for_function("() => !!document.querySelector('[data-text=spoken].dt-shown')")
    assert page.evaluate("() => document.querySelector('[data-text=spoken]').getAttribute('data-reveal')") == "fade"
    assert (
        page.evaluate("() => getComputedStyle(document.querySelector('[data-text=spoken]')).animationName")
        == "dt-fadein"
    )


TEXT_MODES = (
    '<div data-scene="1"><template data-slide="1.1">'
    '<b data-cue="1.1n" data-text="count" data-duration="1.5">1 in 10</b>'
    '<i data-cue="1.1t" data-text="type 60ms">typed out</i></template></div>'
)


WATCH_TEXT = """(selector) => {
  window.__seen = [];
  const el = document.querySelector(selector);
  new MutationObserver(() => window.__seen.push(el.textContent)).observe(el, {
    childList: true,
    characterData: true,
    subtree: true,
  });
}"""
"""Record every text an element holds while a text mode runs. Sampling from outside sees the
finished text on a fast machine, and the finished text is the text the author already wrote, so
only the states in between tell a mode that ran from a mode that was deleted."""


def test_count_runs_up_to_the_last_number_and_stops_there(page, tmp_path):
    """data-text="count" counts the last number of the text up from zero and lands on it.

    The surrounding words never change, and the number passes through values below the target.
    """
    page.goto(f"{write_page(tmp_path, 'count.html', TEXT_MODES)}?scene=1&t0=signal&cues=1.1n@0.05,1.1t@9")
    page.evaluate("() => window.__decktalk.ready")
    page.evaluate(WATCH_TEXT, "b")
    page.evaluate("() => DeckTalk.startClock()")
    # The author already wrote "1 in 10", so waiting for that text alone would pass without a count.
    page.wait_for_function(
        "() => window.__seen.length > 0 && window.__seen[window.__seen.length - 1] === '1 in 10'",
        timeout=5000,
    )
    seen = page.evaluate("() => window.__seen")
    counts = [int(t.removeprefix("1 in ")) for t in seen]
    assert all(t.startswith("1 in ") for t in seen)
    assert min(counts) < 10 and counts[-1] == 10
    assert counts == sorted(counts)
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_count_first_counts_the_first_number_and_leaves_the_rest(page, tmp_path):
    """data-text="count first" counts the first number instead of the last, which is the other branch."""
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        '<b data-cue="1.1n" data-text="count first" data-duration="1.5">40 of 100</b></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'countfirst.html', body)}?scene=1&t0=signal&cues=1.1n@0.05")
    page.evaluate("() => window.__decktalk.ready")
    page.evaluate(WATCH_TEXT, "b")
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function(
        "() => window.__seen.length > 0 && window.__seen[window.__seen.length - 1] === '40 of 100'",
        timeout=5000,
    )
    seen = page.evaluate("() => window.__seen")
    assert all(t.endswith(" of 100") for t in seen)
    counts = [int(t.removesuffix(" of 100")) for t in seen]
    assert min(counts) < 40 and counts[-1] == 40


def test_type_writes_the_text_one_character_at_a_time(page, tmp_path):
    """data-text="type Nms" types the text out, so the element holds a growing prefix while it runs."""
    page.goto(f"{write_page(tmp_path, 'type.html', TEXT_MODES)}?scene=1&t0=signal&cues=1.1n@9,1.1t@0.05")
    page.evaluate("() => window.__decktalk.ready")
    page.evaluate(WATCH_TEXT, "i")
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function(
        "() => window.__seen.length > 0 && window.__seen[window.__seen.length - 1] === 'typed out'",
        timeout=5000,
    )
    seen = page.evaluate("() => window.__seen")
    assert all("typed out".startswith(t) for t in seen)
    assert len(seen) >= len("typed out")
    assert [len(t) for t in seen] == sorted(len(t) for t in seen)
    assert page.evaluate("() => window.__decktalk.warnings") == []


REVEAL_EFFECTS = {
    "rise": "dt-rise",
    "fade": "dt-fadein",
    "draw": "dt-draw",
    "drop": "dt-drop",
    "pop": "dt-pop",
    "dim": "dt-dim",
    "instant": "none",
}


@pytest.mark.parametrize(("effect", "keyframes"), sorted(REVEAL_EFFECTS.items()))
def test_every_reveal_effect_plays_its_own_animation(page, tmp_path, effect, keyframes):
    """Each data-reveal value names one animation of its own, and instant names none.

    The names are distinct, so deleting one effect's rule makes that effect fall back to `rise`.
    A computed animation name is only a string the stylesheet handed back, so the element is also
    asked for the animation it is running, which is what deleting the keyframes takes away.
    """
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        f'<p data-cue="1.1a" data-reveal="{effect}">shown</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, f'reveal-{effect}.html', body)}?scene=1&t0=0&cues=1.1a@0.1")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1a')")
    el = "document.querySelector('[data-cue=\"1.1a\"]')"
    assert page.evaluate(f"() => getComputedStyle({el}).animationName") == keyframes
    assert page.evaluate(f"() => {el}.getAnimations().length") == (0 if effect == "instant" else 1)
    assert len(set(REVEAL_EFFECTS.values())) == len(REVEAL_EFFECTS)


def test_dim_starts_visible_and_the_others_start_hidden(page, tmp_path):
    """A dim reveal is on screen before its cue and recedes on it, which is the opposite of the rest."""
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        '<p class="d" data-cue="1.1d" data-reveal="dim">receding</p>'
        '<p class="r" data-cue="1.1r" data-reveal="rise">arriving</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'dim.html', body)}?scene=1&t0=signal&cues=1.1d@0.2,1.1r@0.4")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => getComputedStyle(document.querySelector('.d')).opacity") == "1"
    assert page.evaluate("() => getComputedStyle(document.querySelector('.r')).opacity") == "0"


def test_a_typewriter_stops_when_its_slide_leaves(page, tmp_path):
    """A slide that has left the stage is removed, and nothing it started keeps painting it."""
    body = (
        '<div data-scene="1">'
        '<template data-slide="1.1"><i data-cue="1.1t" data-text="type 200ms">a long line of typing</i></template>'
        '<template data-slide="1.2"><p data-cue="1.2a">next</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'stop.html', body)}?scene=1&t0=0&cues=1.1t@0.1,1.2a@0.4")
    page.wait_for_function("() => document.querySelector('i') && (window.__typing = document.querySelector('i'))")
    page.wait_for_function("() => window.__decktalk.slide === '1.2'")
    page.wait_for_function("() => !document.body.contains(window.__typing)", timeout=2000)
    settled = page.evaluate("() => window.__typing.textContent.length")
    page.wait_for_timeout(700)
    assert page.evaluate("() => window.__typing.textContent.length") == settled
    assert settled < len("a long line of typing")
    assert not page.errors


def test_a_typewriter_keeps_the_box_of_its_finished_text(page, tmp_path):
    """A reveal measures its own box, so the slide is on the stage before the first reveal runs.

    An element that types from the mount is the case that catches it. A slide still detached has no
    layout, so the guard would set a minimum of zero and everything around the element would shift.
    """
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        '<i data-delay="0" data-text="type 200ms">a long line of typing</i></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'typebox.html', body)}?scene=1&t0=0")
    page.wait_for_function("() => document.querySelector('i').textContent.length > 0", timeout=3000)
    el = "document.querySelector('i')"
    assert page.evaluate(f"() => parseFloat(getComputedStyle({el}).minWidth)") > 0
    assert page.evaluate(f"() => parseFloat(getComputedStyle({el}).minHeight)") > 0


def test_a_page_callback_that_throws_becomes_a_warning(page, tmp_path):
    """A render, an enter and a handler are the page's code, so what they throw the recorder reads back."""
    script = (
        "DeckTalk.scene(1, { slides: [ { id: '1.1',"
        ' render: () => `<p data-cue="1.1a">hi</p>`,'
        " enter: () => { throw new Error('enter boom') },"
        " on: { '1.1a': () => { throw new Error('cue boom') } } } ] });"
    )
    page.goto(f"{script_page(tmp_path, 'throws.html', script)}?scene=1&t0=0&cues=1.1a@0.1")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1a')")
    warnings = warnings_of(page)
    assert any("enter function that threw" in w for w in warnings), warnings
    assert any("slide handler that threw" in w for w in warnings), warnings


def test_a_spoken_caption_stops_when_its_slide_leaves(page, tmp_path):
    """The word-by-word tick is the other thing a mount starts, so it stops with its slide too."""
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        '<p class="cap" data-cue="1.1cap" data-text="spoken">one two three four five</p></template>'
        '<template data-slide="1.2"><p data-cue="1.2a">next</p></template></div>'
    )
    words = "one@0.2,two@1.6,three@2.4,four@3.2,five@4"
    url = write_page(tmp_path, "spokenstop.html", body)
    page.goto(f"{url}?scene=1&t0=0&cues=1.1cap@0.1,1.2a@0.5&words={words}")
    page.wait_for_function("() => document.querySelector('.cap') && (window.__cap = document.querySelector('.cap'))")
    page.wait_for_function("() => window.__decktalk.slide === '1.2'")
    page.wait_for_function("() => !document.body.contains(window.__cap)", timeout=2000)
    settled = page.evaluate("() => window.__cap.querySelectorAll('.dt-word.dt-shown').length")
    page.wait_for_timeout(1200)
    assert page.evaluate("() => window.__cap.querySelectorAll('.dt-word.dt-shown').length") == settled
    assert settled < 5
    assert not page.errors


# ---- what the runtime cannot honor --------------------------------------------------------


def test_an_unknown_cue_id_is_a_warning(page, tmp_path):
    page.goto(f"{write_page(tmp_path, 'unknown.html', MARKUP_SCENE)}?scene=1&t0=0&cues=nope@0.1")
    page.wait_for_timeout(200)
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert "unknown cue id nope (no slide id or owns list matches it)" in warnings, warnings
    assert "no slide owns any listed cue, so nothing will mount" in warnings, warnings
    assert not page.errors


def test_a_cue_that_hits_nothing_is_a_warning(page, tmp_path):
    """A cue id owned by a slide by prefix but with no data-cue, handler, or slide of its own is reported."""
    cues = "1.1ball@0.1,1.1count@0.2,1.1answer@0.3"
    page.goto(f"{write_page(tmp_path, 'nothing.html', MARKUP_SCENE)}?scene=1&t0=0&cues={cues}")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1answer')")
    assert warnings_of(page) == ['cue "1.1answer" matches no element, handler, or slide']


def test_a_data_cue_left_out_of_the_cue_list_shows_at_the_mount(page, tmp_path):
    """An element whose cue ?cues= leaves out is shown rather than left invisible, with a warning."""
    page.goto(f"{write_page(tmp_path, 'unlisted.html', MARKUP_SCENE)}?scene=1&t0=0&cues=1.1ball@0.1")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert warnings_of(page) == ['data-cue "1.1count" is not in ?cues=, so it shows as soon as the slide mounts']
    assert page.evaluate("() => document.querySelector('[data-cue=\"1.1count\"]').classList.contains('dt-shown')")


def test_a_slide_with_no_listed_cue_warns(page, tmp_path):
    """In cue mode a slide that owns none of the listed cues never mounts, and the page says so."""
    page.goto(f"{write_page(tmp_path, 'nomount.html', MARKUP_SCENE)}?scene=1&t0=0&cues=1.1ball@0.1,1.1count@0.2")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert 'slide "1.2" owns no cue in ?cues=, so it never appears' in warnings, warnings
    assert 'slide "1.3" owns no cue in ?cues=, so it never appears' in warnings, warnings


def test_an_element_with_both_a_cue_and_a_delay_warns(page, tmp_path):
    """data-delay is the trigger for an element with no cue, so the two together are a mistake."""
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        '<p data-cue="1.1a" data-delay="2">which one?</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'both.html', body)}?scene=1&t0=0&cues=1.1a@0.1")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1a')")
    assert page.evaluate("() => window.__decktalk.warnings") == [
        'data-cue "1.1a" is on an element that also sets data-delay, so the delay is ignored'
    ]


def test_a_text_mode_with_no_trigger_warns(page, tmp_path):
    """A data-text mode without data-cue or data-delay never reveals, in any mode."""
    body = (
        '<div data-scene="1"><template data-slide="1.1">'
        '<p data-text="spoken">hi there</p><b data-text="count">5</b><i data-text="type 30ms">x</i>'
        '<p data-delay="0" data-text="spoken">fine</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'notrigger.html', body)}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert sorted(page.evaluate("() => window.__decktalk.warnings")) == sorted(
        f'data-text="{mode}" on an element without data-cue or data-delay never reveals, so add data-delay="0"'
        for mode in ("spoken", "count", "type 30ms")
    )
    assert not page.errors


def test_a_preview_cue_past_the_hold_warns(page, tmp_path):
    """A preview cue at or past its slide's hold fires on the next slide, except on the last slide."""
    body = (
        '<div data-scene="3">'
        '<template data-slide="3.1" data-hold="30" data-preview="3.1go@1 3.1min@32">'
        '<p data-cue="3.1go">a</p><p data-cue="3.1min">b</p></template>'
        '<template data-slide="3.2" data-hold="5" data-preview="3.2late@9">'
        '<p data-cue="3.2late">c</p></template></div>'
    )
    url = write_page(tmp_path, "pasthold.html", body)
    page.goto(f"{url}?scene=3&speed=50")
    page.wait_for_function("() => window.__decktalk.mode === 'preview'")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert 'slide "3.1" fires "3.1min" at 32 s but holds 30 s, so it fires on the next slide' in warnings, warnings
    assert not any("3.2late" in w for w in warnings), warnings
    # Cue mode ignores holds, so the same page warns nothing about them.
    page.goto(f"{url}?scene=3&t0=0&cues=3.1go@0.1,3.1min@0.2,3.2late@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length >= 3")
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_a_scene_wrapper_with_no_template_warns(page, tmp_path):
    page.goto(write_page(tmp_path, "empty.html", '<div data-scene="9"><p>nothing here</p></div>'))
    assert page.evaluate("() => window.__decktalk.warnings") == ['scene "9" holds no <template data-slide> element']
    assert page.evaluate("() => window.__decktalk.catalog") == []


def test_katex_parse_error_is_a_warning(page, tmp_path):
    """A data-tex value KaTeX cannot parse renders in red and is reported, since throwOnError is off."""
    body = '<div data-scene="1"><template data-slide="1.1"><p data-tex="\\frac{1}">x</p></template></div>'
    page.goto(f"{write_page(tmp_path, 'badtex.html', body, head=KATEX)}?slide=1.1")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.warnings") == ['data-tex could not be parsed: "\\frac{1}"']
    assert page.evaluate("() => !!document.querySelector('.katex-error')")


def test_ready_warns_when_katex_never_loads(page, tmp_path):
    """A page with [data-tex] and no KaTeX resolves __decktalk.ready after the 5 s poll with a warning."""
    body = '<div data-scene="1"><template data-slide="1.1"><p data-tex="x^2">x^2</p></template></div>'
    page.goto(f"{write_page(tmp_path, 'notex.html', body)}?slide=1.1")
    page.evaluate("() => window.__decktalk.ready")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert any("KaTeX" in w for w in warnings), warnings
    assert page.evaluate("() => document.querySelector('[data-tex]').textContent") == "x^2"


def test_cue_mode_warns_when_a_slide_mounts_equations_without_katex(page, tmp_path):
    """In cue mode a later slide mounts after the ready check ran, so a later check warns."""
    body = (
        '<div data-scene="1"><template data-slide="1.1"><p data-cue="1.1a">plain</p></template>'
        '<template data-slide="1.2"><p data-cue="1.2a" data-tex="x^2">x^2</p></template></div>'
    )
    page.goto(f"{write_page(tmp_path, 'notex-cues.html', body)}?scene=1&t0=0&cues=1.1a@0.1,1.2a@0.3")
    page.wait_for_function("() => window.__decktalk.warnings.some((w) => w.includes('KaTeX'))", timeout=9000)
    assert page.evaluate("() => document.querySelector('[data-tex]').textContent") == "x^2"


# ---- what the recorder reads back -----------------------------------------------------------


def test_record_page_stores_page_errors_in_the_recording_log(page, tmp_path):
    """A page that throws, and a page without the runtime, both leave page_errors that record turns into PAGE ERROR."""
    from decktalk.media.browser import NO_CATALOG, record_page
    from decktalk.media.origin import page_url
    from decktalk.settings import RecordConfig
    from decktalk.stages.record.checks import log_verdicts

    broken = served_page(
        tmp_path,
        "broken.html",
        '<div data-scene="1"><template data-slide="1.1"><p>hi</p></template></div>'
        "<script>\nnotDefinedAnywhere();\n</script>",
    )
    bare = tmp_path / "bare.html"
    bare.write_text("<!doctype html><html><body><p>no runtime here</p></body></html>", encoding="utf-8")
    kw = dict(root=tmp_path, settle_seconds=0.1, min_cover_seconds=0.1, width=640, height=360, color_scheme="light")
    browser = page.context.browser  # the module's Playwright already owns this thread's sync loop
    url = page_url(broken, {"slide": "1.1"})
    recording_log = record_page(browser, url, 0.5, tmp_path / "01-section.webm", **kw)
    recording_log2 = record_page(browser, page_url("bare.html"), 0.5, tmp_path / "02-section.webm", **kw)
    assert len(recording_log.page_errors) == 1, recording_log.page_errors
    assert recording_log.page_errors[0].startswith("ReferenceError: notDefinedAnywhere is not defined"), (
        recording_log.page_errors
    )
    assert "(broken.html:2)" in recording_log.page_errors[0], recording_log.page_errors
    assert recording_log2.page_errors == [NO_CATALOG]
    assert recording_log.assets == ["broken.html", RUNTIME_FILE]
    for recorded, name in ((recording_log, "01-section.json"), (recording_log2, "02-section.json")):
        assert Verdict.PAGE_ERROR in log_verdicts(recorded, RecordConfig())
        recorded.save(tmp_path / name)
        reloaded = type(recorded).load(tmp_path / name)
        assert reloaded is not None and reloaded.page_errors == recorded.page_errors
