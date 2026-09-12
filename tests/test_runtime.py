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
    assert [c["scene"] for c in catalog] == ["1", "2", "3", "4"]
    assert catalog[1]["steps"] == ["2.1", "2.2"]
    assert catalog[3]["steps"] == ["4.1", "4.2"]
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
    assert page.evaluate("() => document.querySelector('.tile').textContent") == "3 steps"


def test_cue_mode_fires_in_order_and_first_step_mounts_at_zero(page, deck):
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&beats=2.1a@0.3,2.1b@0.9,2.2@1.6")
    page.wait_for_timeout(120)
    # Step 2.1 mounted at t=0 even though its first cue is at 0.3 s, and its listed reveals wait.
    assert page.evaluate("() => window.__decktalk.step") == "2.1"
    assert page.evaluate("() => document.querySelector('[data-cue=\"2.1a\"]').classList.contains('dt-on')") is False
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1a"]
    assert page.evaluate("() => document.querySelector('[data-cue=\"2.1a\"]').classList.contains('dt-on')") is True
    page.wait_for_function("() => window.__decktalk.step === '2.2'")
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1a", "2.1b", "2.2"]
    assert page.evaluate("() => document.body.dataset.done") == "1"
    assert not page.errors


def test_handlers_and_unknown_cues(page, deck):
    page.goto(f"{deck.as_uri()}?scene=1&t0=0&beats=1.1a@0.1,custom@0.2")
    page.evaluate("() => { window.__hits = []; DeckTalk.on('custom', () => window.__hits.push('custom')); }")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    assert page.evaluate("() => window.__hits") == ["custom"]
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1a", "custom"]


def test_warnings_report_an_unknown_cue_id(page, deck):
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&beats=nope@0.1")
    page.wait_for_timeout(200)
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert any("unknown cue id nope" in w for w in warnings), warnings
    assert any("no step owns" in w for w in warnings), warnings
    assert not page.errors


def test_autoplay_uses_holds(page, deck):
    page.goto(f"{deck.as_uri()}?scene=2&speed=20")  # holds 12 s and 6 s become 0.6 s and 0.3 s
    page.wait_for_function("() => document.body.dataset.done === '1'", timeout=5000)
    assert page.evaluate("() => window.__decktalk.mode") == "autoplay"
    assert page.evaluate("() => window.__decktalk.step") == "2.2"


def test_signal_mode_waits_for_start_clock(page, deck):
    """With t0=signal the clock does not start at load, and starts when the recorder says so."""
    page.goto(f"{deck.as_uri()}?scene=2&t0=signal&beats=2.1a@0.1")
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__decktalk.fired") == []
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function("() => window.__decktalk.fired.includes('2.1a')", timeout=2000)


def test_katex_typesets_the_derivation(page, deck):
    """The vendored KaTeX renders every [data-tex] line of scene 3, and __sceneReady waits for it."""
    if katex_cached() is None:
        pytest.skip("KaTeX is not cached (run `decktalk setup`)")
    assert (deck.parent / "katex" / "katex.min.js").exists()
    page.goto(f"{deck.as_uri()}?step=3.1")
    page.evaluate("() => window.__sceneReady")
    assert page.evaluate("() => document.querySelectorAll('[data-tex][data-typeset] .katex').length") == 3
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


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
