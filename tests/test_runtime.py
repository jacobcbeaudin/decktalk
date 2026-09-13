"""The page runtime, in a real headless Chromium. Skipped when Chromium is unavailable.

uv run pytest -m browser
"""

from __future__ import annotations

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
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&beats=2.1script@0.3,2.1words@0.9,2.1one@1.6")
    page.wait_for_timeout(120)
    # Step 2.1 mounted at t=0 even though its first cue is at 0.3 s, and its listed reveals wait.
    assert page.evaluate("() => window.__decktalk.step") == "2.1"
    hidden = "() => document.querySelector('[data-cue=\"2.1script\"]').classList.contains('dt-on')"
    assert page.evaluate(hidden) is False
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1script"]
    assert page.evaluate("() => document.querySelector('[data-cue=\"2.1script\"]').classList.contains('dt-on')") is True
    page.wait_for_function("() => window.__decktalk.fired.length >= 3")
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1script", "2.1words", "2.1one"]
    # The step's `on` handler ran for 2.1one, and the single step means the scene is done at mount.
    assert page.evaluate("() => document.querySelector('.dt-slide').classList.contains('lit')")
    assert page.evaluate("() => document.body.dataset.done") == "1"
    assert not page.errors


def test_handlers_and_unknown_cues(page, deck):
    page.goto(f"{deck.as_uri()}?scene=1&t0=0&beats=1.1curve@0.1,custom@0.2")
    page.evaluate("() => { window.__hits = []; DeckTalk.on('custom', () => window.__hits.push('custom')); }")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    assert page.evaluate("() => window.__hits") == ["custom"]
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1curve", "custom"]


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
    page.goto(f"{deck.as_uri()}?scene=2&t0=signal&beats=2.1script@0.1")
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
    assert not page.errors


def test_synced_words_stay_dim_until_spoken(page, deck):
    """A data-sync line shows every word dim at mount and turns each one on at its spoken second."""
    words = "The@0,curve@0.2,rises@0.4,then@5,the@5.2,number@5.4,lands@5.6"
    page.goto(f"{deck.as_uri()}?scene=1&t0=0&beats=1.1curve@0.2&words={words}")
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
