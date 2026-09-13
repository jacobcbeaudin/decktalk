"""The page runtime, in a real headless Chromium. Skipped when Chromium is unavailable.

uv run pytest -m browser
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.scaffold import init, katex_cached, runtime_path

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def deck(tmp_path_factory) -> Path:
    root = init(tmp_path_factory.mktemp("proj") / "p", name="p")
    return (root / "deck" / "index.html").resolve()


@pytest.fixture(scope="module")
def page(deck):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {str(exc).splitlines()[0]}")
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.errors = errors  # type: ignore[attr-defined]
        yield page
        browser.close()


def template_cues(deck: Path, section: str) -> list[str]:
    """The cue ids the scaffold's cues.json lists for one section, in order."""
    data = json.loads((deck.parent.parent / "cues.json").read_text())
    return [c["cue"] for c in data["sections"][section]["cues"]]


def full_beats(deck: Path, section: str, start: float = 0.1, gap: float = 0.05) -> str:
    """A ?beats= value with every cue of the section, spaced `gap` seconds apart."""
    return ",".join(f"{cue}@{start + i * gap:.2f}" for i, cue in enumerate(template_cues(deck, section)))


def custom_page(tmp_path: Path, name: str, script: str) -> str:
    """A page with the runtime and one inline script, returned as a file URL."""
    html = tmp_path / name
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        f'<script src="{runtime_path().resolve().as_uri()}"></script>'
        f"<script>{script}</script></body></html>"
    )
    return html.resolve().as_uri()


# Two steps whose handlers record what they receive. Step 1.2 also keeps a zero-argument handler.
HANDLER_PAGE = """
window.__got = [];
DeckTalk.scene(1, { steps: [
  { id: '1.1', cues: { '1.1a': 0.1 }, render: () => `<p data-cue="1.1a">a</p>`,
    enter: (slide, ctx) => window.__got.push({ kind: 'enter', step: slide.dataset.step, ctx }),
    on: { '1.1a': (slide, ctx) => window.__got.push({ kind: 'on', step: slide.dataset.step,
      current: slide === document.querySelector('.dt-slide:not(.dt-leave)'), ctx }) } },
  { id: '1.2', cues: { '1.2a': 0.1 }, render: () => `<p data-cue="1.2a">b</p>`,
    on: { '1.2a': () => window.__got.push({ kind: 'zero' }) } },
]});
DeckTalk.on('1.2a', (slide, ctx) => window.__got.push({ kind: 'global', step: slide.dataset.step, ctx }));
DeckTalk.on('1.2a', () => window.__got.push({ kind: 'global-zero' }));
"""


def test_index_mode_exposes_catalog(page, deck):
    page.goto(deck.as_uri())
    catalog = page.evaluate("() => window.__decktalk.catalog")
    assert [c["scene"] for c in catalog] == ["1", "2", "3", "4", "5"]
    assert [c["steps"] for c in catalog] == [["1.1"], ["2.1"], ["3.1"], ["4.1"], ["5.1"]]
    assert [c["name"] for c in catalog] == ["Open", "Four files", "Gradient descent", "The edit", "Close"]
    assert page.evaluate("() => window.__decktalk.mode") == "index"
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_freeze_mode_reveals_everything(page, deck):
    page.goto(f"{deck.as_uri()}?step=2.1")
    page.wait_for_timeout(200)
    assert page.evaluate("() => window.__decktalk.mode") == "frozen"
    assert page.evaluate("() => document.body.dataset.done") == "1"
    hidden = page.evaluate(
        "() => [...document.querySelectorAll('.dt-reveal')].filter(e => !e.classList.contains('dt-on')).length"
    )
    assert hidden == 0
    # The listed cues fired too: 2.1one lights the shared phrase and dims the rest of every panel.
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1script", "2.1words", "2.1cue", "2.1slide", "2.1one"]
    assert page.evaluate("() => document.querySelector('.dt-slide').classList.contains('lit')")
    assert page.evaluate("() => document.querySelectorAll('.k').length") == 5


def test_cue_mode_fires_in_order_and_first_step_mounts_at_zero(page, deck):
    beats = "2.1script@0.3,2.1words@0.9,2.1cue@1.2,2.1slide@1.4,2.1one@1.6"
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&beats={beats}")
    page.wait_for_timeout(120)
    # Step 2.1 mounted at t=0 even though its first cue is at 0.3 s, and its listed reveals wait.
    assert page.evaluate("() => window.__decktalk.step") == "2.1"
    hidden = "() => document.querySelector('[data-cue=\"2.1script\"]').classList.contains('dt-on')"
    assert page.evaluate(hidden) is False
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1script"]
    assert page.evaluate("() => document.querySelector('[data-cue=\"2.1script\"]').classList.contains('dt-on')") is True
    page.wait_for_function("() => window.__decktalk.fired.length >= 5")
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1script", "2.1words", "2.1cue", "2.1slide", "2.1one"]
    # The step's `on` handler ran for 2.1one, and the single step means the scene is done at mount.
    assert page.evaluate("() => document.querySelector('.dt-slide').classList.contains('lit')")
    assert page.evaluate("() => document.body.dataset.done") == "1"
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_handlers_and_unknown_cues(page, deck):
    page.goto(f"{deck.as_uri()}?scene=1&t0=0&beats={full_beats(deck, '1')},custom@0.5")
    page.evaluate("() => { window.__hits = []; DeckTalk.on('custom', () => window.__hits.push('custom')); }")
    page.wait_for_function("() => window.__decktalk.fired.length >= 5")
    assert page.evaluate("() => window.__hits") == ["custom"]
    assert page.evaluate("() => window.__decktalk.fired") == [*template_cues(deck, "1"), "custom"]


def test_warnings_report_an_unknown_cue_id(page, deck):
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&beats=nope@0.1")
    page.wait_for_timeout(200)
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert any("unknown cue id nope" in w for w in warnings), warnings
    assert any("no step owns" in w for w in warnings), warnings
    assert not page.errors


def test_autoplay_uses_holds(page, deck):
    page.goto(f"{deck.as_uri()}?scene=2&speed=20")  # the 28 s hold becomes 1.4 s, and the listed cues fire inside it
    page.wait_for_function("() => document.body.dataset.done === '1'", timeout=5000)
    assert page.evaluate("() => window.__decktalk.mode") == "autoplay"
    assert page.evaluate("() => window.__decktalk.step") == "2.1"
    page.wait_for_function("() => window.__decktalk.fired.includes('2.1one')", timeout=5000)


def test_signal_mode_waits_for_start_clock(page, deck):
    """With t0=signal the clock does not start at load, and starts when the recorder says so."""
    page.goto(f"{deck.as_uri()}?scene=2&t0=signal&beats={full_beats(deck, '2')}")
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__decktalk.fired") == []
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function("() => window.__decktalk.fired.includes('2.1script')", timeout=2000)


def test_katex_typesets_the_equation(page, deck):
    """The vendored KaTeX renders every [data-tex] line of scene 3, and __sceneReady waits for it."""
    if katex_cached() is None:
        pytest.skip("KaTeX is not cached (run `decktalk setup`)")
    assert (deck.parent / "katex" / "katex.min.js").exists()
    page.goto(f"{deck.as_uri()}?step=3.1")
    page.evaluate("() => window.__sceneReady")
    # The two learning rate chips and the four spans of the update rule.
    assert page.evaluate("() => document.querySelectorAll('[data-tex][data-typeset] .katex').length") == 6
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_scene_3_draws_its_surface_in_webgl(page, deck):
    """The frozen step 3.1 mounts a 960 by 570 three.js canvas in the figure region, with no page error."""
    page.goto(f"{deck.as_uri()}?step=3.1")
    page.evaluate("() => window.__sceneReady")
    size = page.evaluate("() => { const c = document.querySelector('.fig3 canvas'); return c && [c.width, c.height]; }")
    if size is None:
        pytest.skip("no WebGL context in this Chromium")
    assert size == [960, 570]
    assert page.evaluate("() => document.querySelector('.fig3 canvas').dataset.warm") is None
    assert not page.errors


def test_scene_3_warms_its_figure_before_the_clock_starts(page, deck):
    """In cue mode the figure is drawn off the page while it loads, and the step mounts that canvas."""
    page.goto(f"{deck.as_uri()}?scene=3&t0=signal&beats={full_beats(deck, '3')}")
    page.evaluate("() => window.__sceneReady")
    assert page.evaluate("() => document.querySelector('.fig3')") is None
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function("() => window.__decktalk.fired.includes('3.1min')", timeout=5000)
    canvases = page.evaluate(
        "() => [...document.querySelectorAll('.fig3 canvas')].map((c) => [c.width, c.height, c.dataset.warm])"
    )
    if not canvases:
        pytest.skip("no WebGL context in this Chromium")
    assert canvases == [[960, 570, "1"]]
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_synced_words_stay_dim_until_spoken(page, deck):
    """A data-sync line shows every word dim at mount and turns each one on at its spoken second."""
    words = "The@0,curve@0.2,rises@0.4,then@5,the@5.2,number@5.4,lands@5.6"
    beats = "1.1curve@0.2,1.1number@5.4,1.1mark@6,1.1cap@7"
    page.goto(f"{deck.as_uri()}?scene=1&t0=0&beats={beats}&words={words}")
    page.wait_for_function("() => document.querySelectorAll('.dt-w.dt-on').length >= 3", timeout=3000)
    assert page.evaluate("() => document.querySelectorAll('.dt-w').length") == 7
    assert page.evaluate("() => document.querySelectorAll('.dt-w.dt-on').length") == 3
    assert page.evaluate("() => getComputedStyle(document.querySelector('.dt-w:not(.dt-on)')).opacity") == "1"
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_scene_ready_warns_when_katex_never_loads(page, tmp_path):
    """A page with [data-tex] and no KaTeX resolves __sceneReady after the 5 s poll with a warning."""
    html = tmp_path / "notex.html"
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        f'<script src="{runtime_path().resolve().as_uri()}"></script>'
        "<script>DeckTalk.scene(1, { steps: [ { id: '1.1', render: () => `<p data-tex=\"x^2\">x^2</p>` } ] });</script>"
        "</body></html>"
    )
    page.goto(f"{html.resolve().as_uri()}?step=1.1")
    page.evaluate("() => window.__sceneReady")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert any("KaTeX" in w for w in warnings), warnings
    assert page.evaluate("() => document.querySelector('[data-tex]').textContent") == "x^2"


def test_a_cue_that_hits_nothing_is_a_warning(page, deck):
    """A cue id owned by a step by prefix but with no data-cue, handler, or step of its own is reported."""
    page.goto(f"{deck.as_uri()}?scene=4&t0=0&beats={full_beats(deck, '4')},4.1answer@0.7")
    page.wait_for_function("() => window.__decktalk.fired.includes('4.1answer')")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert warnings == ['cue "4.1answer" matches no element, handler, or step'], warnings


def test_every_template_step_freezes_without_warnings(page, deck):
    """Every cue the template lists reveals an element or runs a handler, so no step warns when frozen."""
    page.goto(deck.as_uri())
    catalog = page.evaluate("() => window.__decktalk.catalog")
    for entry in catalog:
        for step in entry["steps"]:
            page.goto(f"{deck.as_uri()}?step={step}")
            page.evaluate("() => window.__sceneReady")
            assert page.evaluate("() => window.__decktalk.warnings") == [], step
    assert not page.errors


def test_every_template_scene_plays_its_full_cue_list_without_warnings(page, deck):
    """Each scene in cue mode with every cue its cues.json section lists warns nothing and logs no error."""
    logged: list[str] = []

    def on_console(msg):
        if msg.type == "error":
            logged.append(msg.text)

    page.on("console", on_console)
    try:
        data = json.loads((deck.parent.parent / "cues.json").read_text())
        for section in data["sections"]:
            cues = template_cues(deck, section)
            page.goto(f"{deck.as_uri()}?scene={section}&t0=0&beats={full_beats(deck, section)}")
            page.evaluate("() => window.__sceneReady")
            page.wait_for_function(f"() => window.__decktalk.fired.length >= {len(cues)}", timeout=10000)
            assert page.evaluate("() => window.__decktalk.fired") == cues, section
            assert page.evaluate("() => window.__decktalk.warnings") == [], section
    finally:
        page.remove_listener("console", on_console)
    assert logged == []
    assert not page.errors


def test_katex_parse_error_is_a_warning(page, deck, tmp_path):
    """A data-tex value KaTeX cannot parse renders in red and is reported, since throwOnError is off."""
    if katex_cached() is None:
        pytest.skip("KaTeX is not cached (run `decktalk setup`)")
    katex = (deck.parent / "katex" / "katex.min.js").resolve().as_uri()
    html = tmp_path / "badtex.html"
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<script src="{katex}"></script><script src="{runtime_path().resolve().as_uri()}"></script></head><body>'
        "<script>DeckTalk.scene(1, { steps: [ { id: '1.1', "
        'render: () => `<p data-tex="\\\\frac{1}">x</p>` } ] });</script>'
        "</body></html>"
    )
    page.goto(f"{html.resolve().as_uri()}?step=1.1")
    page.evaluate("() => window.__sceneReady")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert (
        'data-tex could not be parsed: "\\frac{1}" (write \\\\ for every backslash inside a template literal)'
        in warnings
    ), warnings
    assert page.evaluate("() => !!document.querySelector('.katex-error')")


def test_step_handlers_receive_the_slide_and_ctx(page, tmp_path):
    """A step's on[id] is called with the mounted slide element and { id, at, frozen, step }."""
    page.goto(f"{custom_page(tmp_path, 'handlers.html', HANDLER_PAGE)}?scene=1&t0=0&beats=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    got = page.evaluate("() => window.__got")
    on = next(g for g in got if g["kind"] == "on")
    assert on["step"] == "1.1" and on["current"] is True
    assert set(on["ctx"]) == {"id", "at", "frozen", "step"}
    assert on["ctx"]["id"] == "1.1a" and on["ctx"]["step"] == "1.1" and on["ctx"]["frozen"] is False
    assert 0.1 <= on["ctx"]["at"] < 0.4, on
    # A handler that takes no arguments keeps working.
    assert any(g["kind"] == "zero" for g in got), got
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_global_handlers_receive_the_slide_and_ctx(page, tmp_path):
    """Every DeckTalk.on handler is called with the slide of the step mounted when the cue fires."""
    page.goto(f"{custom_page(tmp_path, 'handlers.html', HANDLER_PAGE)}?scene=1&t0=0&beats=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    got = page.evaluate("() => window.__got")
    glob = next(g for g in got if g["kind"] == "global")
    assert glob["step"] == "1.2"
    assert glob["ctx"]["id"] == "1.2a" and glob["ctx"]["step"] == "1.2" and glob["ctx"]["frozen"] is False
    assert glob["ctx"]["at"] >= 0.4, glob
    # The step handler runs before the global ones, and a zero-argument global handler still runs.
    kinds = [g["kind"] for g in got]
    assert kinds.index("zero") < kinds.index("global") < kinds.index("global-zero"), kinds


def test_enter_receives_the_same_ctx(page, tmp_path):
    """A step's enter gets the slide and a ctx whose id and step are the step id, frozen or not."""
    url = custom_page(tmp_path, "handlers.html", HANDLER_PAGE)
    page.goto(f"{url}?scene=1&t0=0&beats=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    enter = next(g for g in page.evaluate("() => window.__got") if g["kind"] == "enter")
    assert enter["step"] == "1.1"
    assert set(enter["ctx"]) == {"id", "at", "frozen", "step"}
    assert enter["ctx"]["id"] == "1.1" and enter["ctx"]["step"] == "1.1" and enter["ctx"]["frozen"] is False
    assert 0 <= enter["ctx"]["at"] < 0.1, enter
    page.goto(f"{url}?step=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    frozen = [g for g in page.evaluate("() => window.__got") if g["kind"] in ("enter", "on")]
    assert [g["kind"] for g in frozen] == ["enter", "on"]
    assert all(g["ctx"]["frozen"] is True and g["ctx"]["step"] == "1.1" for g in frozen), frozen


def test_scene_4_strikes_the_old_line_when_frozen(page, deck):
    """The template's scene 4 handlers find their elements through the slide they receive."""
    page.goto(f"{deck.as_uri()}?step=4.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => !!document.querySelector('.dt-slide .old.struck')")
    assert page.evaluate("() => !!document.querySelector('.dt-slide .s4.lit')")
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_data_sync_has_no_container_animation(page, deck):
    """A data-sync element without data-fx gets data-fx="none", so the container never fades or rises."""
    page.goto(f"{deck.as_uri()}?scene=5&t0=0&beats={full_beats(deck, '5')}")
    page.wait_for_function("() => window.__decktalk.fired.includes('5.1cap')")
    cap = "document.querySelector('[data-cue=\"5.1cap\"]')"
    assert page.evaluate(f"() => {cap}.getAttribute('data-fx')") == "none"
    assert page.evaluate(f"() => {cap}.classList.contains('dt-on')")
    assert page.evaluate(f"() => getComputedStyle({cap}).animationName") == "none"
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_data_sync_keeps_an_explicit_fx(page, tmp_path):
    """A data-sync element that sets its own data-fx keeps it."""
    script = (
        "DeckTalk.scene(1, { steps: [ { id: '1.1', render: () => "
        '`<p data-at="0" data-sync data-fx="fade">Hello there</p>` } ] });'
    )
    page.goto(f"{custom_page(tmp_path, 'syncfx.html', script)}?scene=1")
    page.wait_for_function("() => !!document.querySelector('[data-sync].dt-on')")
    assert page.evaluate("() => document.querySelector('[data-sync]').getAttribute('data-fx')") == "fade"
    assert page.evaluate("() => getComputedStyle(document.querySelector('[data-sync]')).animationName") == "dt-fadein"


def test_warns_when_a_step_owns_no_listed_cue(page, tmp_path):
    """In cue mode a step that owns none of the listed cues never mounts, and the page says so."""
    script = (
        "DeckTalk.scene(3, { steps: ["
        " { id: '3.1', cues: ['3.1a'], render: () => `<p data-cue=\"3.1a\">a</p>` },"
        " { id: '3.2', cues: ['3.2a'], render: () => `<p data-cue=\"3.2a\">b</p>` } ] });"
    )
    page.goto(f"{custom_page(tmp_path, 'nocue.html', script)}?scene=3&t0=0&beats=3.1a@0.1")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.warnings") == [
        'step "3.2" owns no cue in ?beats=, so it never appears'
    ]


def test_warns_when_a_data_cue_is_not_listed(page, deck):
    """In cue mode an element waiting for a cue that ?beats= leaves out reveals on its timer, with a warning."""
    beats = ",".join(part for part in full_beats(deck, "4").split(",") if not part.startswith("4.1change@"))
    page.goto(f"{deck.as_uri()}?scene=4&t0=0&beats={beats}")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.warnings") == [
        'data-cue "4.1change" is not in ?beats=, so it reveals at its data-at time after the mount'
    ]
    assert page.evaluate("() => document.querySelector('.script4').classList.contains('dt-on')")


def test_warns_when_a_reveal_mode_has_no_trigger(page, tmp_path):
    """data-sync, data-count, or data-type without data-cue or data-at never reveals, in any mode."""
    script = (
        "DeckTalk.scene(1, { steps: [ { id: '1.1', render: () => "
        '`<p data-sync>hi there</p><b data-count>5</b><i data-type="30">x</i>'
        '<p data-at="0" data-sync>fine</p>` } ] });'
    )
    page.goto(f"{custom_page(tmp_path, 'notrigger.html', script)}?step=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert sorted(page.evaluate("() => window.__decktalk.warnings")) == sorted(
        f'{attr} on an element without data-cue or data-at never reveals, so add data-at="0"'
        for attr in ("data-sync", "data-count", "data-type")
    )
    assert not page.errors


def test_warns_when_an_autoplay_cue_falls_past_the_hold(page, tmp_path):
    """An autoplay cue at or past its step's hold fires on the next step, except on the last step."""
    script = (
        "DeckTalk.scene(3, { steps: ["
        " { id: '3.1', hold: 30, cues: { '3.1go': 1, '3.1min': 32 },"
        '   render: () => `<p data-cue="3.1go">a</p><p data-cue="3.1min">b</p>` },'
        " { id: '3.2', hold: 5, cues: { '3.2late': 9 }, render: () => `<p data-cue=\"3.2late\">c</p>` } ] });"
    )
    url = custom_page(tmp_path, "pasthold.html", script)
    page.goto(f"{url}?scene=3&speed=50")
    page.wait_for_function("() => window.__decktalk.mode === 'autoplay'")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert 'step "3.1" fires "3.1min" at 32 s but holds 30 s, so it fires on the next step' in warnings, warnings
    assert not any("3.2late" in w for w in warnings), warnings
    # Cue mode ignores holds, so the same page warns nothing about them.
    page.goto(f"{url}?scene=3&t0=0&beats=3.1go@0.1,3.1min@0.2,3.2late@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length >= 3")
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_freeze_at_one_cue_stops_there(page, deck):
    """?step=4.1&cue=4.1valley fires the step's cues up to 4.1valley and leaves later reveals hidden."""
    page.goto(f"{deck.as_uri()}?step=4.1&cue=4.1valley")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.mode") == "frozen"
    assert page.evaluate("() => window.__decktalk.fired") == ["4.1change", "4.1valley"]
    on = "(sel) => document.querySelector(sel).classList.contains('dt-on')"
    assert page.evaluate(on, ".script4") is True
    assert page.evaluate(on, ".new") is True
    assert page.evaluate(on, "[data-cue='4.1build']") is False
    assert page.evaluate(on, ".final") is False
    assert page.evaluate("() => !!document.querySelector('.old.struck')")
    assert page.evaluate("() => !document.querySelector('.s4.lit')")
    assert page.evaluate("() => window.__decktalk.warnings") == []
    # An id that is not one of the step's cues warns and freezes the whole step.
    page.goto(f"{deck.as_uri()}?step=4.1&cue=nope")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.warnings") == ['cue "nope" is not one of step 4.1\'s cues']
    assert page.evaluate("() => window.__decktalk.fired") == template_cues(deck, "4")
    assert page.evaluate("() => !!document.querySelector('.s4.lit')")
    assert not page.errors


def test_record_page_stores_page_errors_in_the_sidecar(page, tmp_path):
    """A page that throws, and a page without the runtime, both leave page_errors that check turns into PAGE ERROR."""
    from decktalk.config import AlignConfig
    from decktalk.media.browser import NO_CATALOG, record_page
    from decktalk.stages.measure import sidecar_verdicts

    runtime = runtime_path().resolve().as_uri()
    broken = tmp_path / "broken.html"
    broken.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>\n'
        f'<script src="{runtime}"></script>\n'
        "<script>DeckTalk.scene(1, { steps: [ { id: '1.1', render: () => `<p>hi</p>` } ] });</script>\n"
        "<script>\nnotDefinedAnywhere();\n</script>\n"
        "</body></html>"
    )
    bare = tmp_path / "bare.html"
    bare.write_text("<!doctype html><html><body><p>no runtime here</p></body></html>")
    kw = dict(settle_seconds=0.1, min_lead_seconds=0.1, width=640, height=360, color_scheme="light")
    browser = page.context.browser  # the module's Playwright already owns this thread's sync loop
    side = record_page(browser, f"{broken.as_uri()}?step=1.1", 0.5, tmp_path / "01-section.webm", **kw)
    side2 = record_page(browser, bare.as_uri(), 0.5, tmp_path / "02-section.webm", **kw)
    assert len(side.page_errors) == 1, side.page_errors
    assert side.page_errors[0].startswith("ReferenceError: notDefinedAnywhere is not defined"), side.page_errors
    assert "(broken.html:5)" in side.page_errors[0], side.page_errors
    assert side2.page_errors == [NO_CATALOG]
    for s, name in ((side, "01-section.json"), (side2, "02-section.json")):
        assert "PAGE ERROR" in sidecar_verdicts(s, AlignConfig())
        reloaded = type(s).load(tmp_path / name)
        assert reloaded is not None and reloaded.page_errors == s.page_errors
