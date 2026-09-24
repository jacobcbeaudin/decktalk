"""decktalk-probe.js, the instrumentation a DeckTalk command injects into the page it opens.

Every page here is the same page `tests/contract/test_runtime.py` writes, opened through the hook
`media/browser.py` uses, so what these tests measure is what the probe adds and nothing the deck
carries. The cover, the wait helper, the measured boxes and the freeze that stops at one cue live
here, and none of them may reach a page that no command is driving.

    uv run pytest -m browser
"""

from __future__ import annotations

import pytest

from decktalk.media.browser import instrument
from support.browser_pages import MARKUP_SCENE, chromium_page, write_page

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def page():
    """The page a command opens: decktalk-probe.js added as an init script, as the recorder adds it."""
    yield from chromium_page(instrument)


@pytest.fixture(autouse=True)
def _fresh_errors(page):
    page.errors.clear()
    yield


# ---- the probe is the recorder's and nobody else's ----------------------------------------


def test_no_page_references_the_probe():
    """The probe is injected, so it is never a file a deck loads and never a file init copies."""
    from decktalk.toolchain.assets import PROBE_FILE, package_file

    template = package_file("template")
    loaded = [p for p in template.rglob("*.html") if PROBE_FILE in p.read_text(encoding="utf-8")]
    assert loaded == []
    assert not list(template.rglob(PROBE_FILE))


def test_a_page_without_the_probe_still_freezes_lists_and_plays(page, tmp_path):
    """The split is only safe while a deck that no command drives keeps every mode it had."""
    # A second page of the same browser, opened the way a person opens one, with no init script.
    bare = page.context.browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        url = write_page(tmp_path, "bare.html", MARKUP_SCENE)
        bare.goto(f"{url}?slide=1.1")
        bare.wait_for_function("() => document.body.dataset.done === '1'")
        assert bare.evaluate("() => window.__decktalk.fired") == ["1.1ball", "1.1count"]
        bare.goto(url)
        bare.evaluate("() => window.__decktalk.ready")
        assert bare.evaluate("() => !!document.getElementById('dt-index')")
        # Nothing measures the boxes, which is the one thing a page loses with no probe.
        assert bare.evaluate("() => window.__decktalk.catalog.every((c) => c.elements === undefined)")
        assert bare.evaluate("() => !window.__dtprobe")
    finally:
        bare.close()


# ---- the cover and the clock ---------------------------------------------------------------


def test_the_cover_hides_the_page_until_the_clock_starts(page, tmp_path):
    """The recorder starts capturing before the page settles, so the cover owns every frame until t=0."""
    page.goto(f"{write_page(tmp_path, 'cover.html', MARKUP_SCENE)}?scene=1&t0=signal")
    page.evaluate("() => window.__dtprobe.cover()")
    box = page.evaluate("() => document.getElementById('__t0cover').getBoundingClientRect().toJSON()")
    assert (box["width"], box["height"]) == (1920, 1080)
    paint = page.evaluate("() => getComputedStyle(document.getElementById('__t0cover')).backgroundColor")
    assert paint == "rgb(255, 0, 255)"
    # The keep-alive turns for the whole recording, so the compositor keeps painting frames.
    alive = "() => document.getElementById('__dtkeepalive').getAnimations().length"
    assert page.evaluate(alive) == 1
    at = page.evaluate("() => window.__dtprobe.lift()")
    assert page.evaluate("() => document.getElementById('__t0cover')") is None
    assert at > 0
    # The clock started on the frame that showed the cover gone, so the page's t=0 is that frame.
    assert page.evaluate("() => window.__decktalk.now()") >= 0
    assert page.evaluate(alive) == 1
    assert not page.errors


def test_the_wait_helper_waits_for_the_pages_own_condition(page, tmp_path):
    """The recorder waits with one call, and what it waits for is the fonts and the runtime's promise."""
    body = (
        "<script>DeckTalk.waitFor(new Promise((r) => setTimeout(() => { window.__late = true; r(); }, 300)));</script>"
    )
    page.goto(write_page(tmp_path, "wait.html", body + MARKUP_SCENE))
    assert page.evaluate("() => window.__dtprobe.ready()") is True
    assert page.evaluate("() => window.__late") is True
    assert not page.errors


# ---- the measured catalog -------------------------------------------------


def test_the_catalog_measures_every_cued_element(page, tmp_path):
    """In index mode each slide is laid out once, so every reveal carries a box in stage pixels."""
    head = "<style>.title { position: absolute; left: 120px; top: 80px; width: 600px; height: 90px; margin: 0 }</style>"
    page.goto(write_page(tmp_path, "boxes.html", MARKUP_SCENE, head=head))
    page.evaluate("() => window.__decktalk.ready")
    rows = page.evaluate("() => window.__decktalk.catalog[0].elements['1.1']")
    by_cue = {r["cue"]: r for r in rows}
    assert set(by_cue) == {"1.1ball", "1.1count", None}
    ball = by_cue["1.1ball"]
    assert ball["describe"] == "a ball rests in the bowl" and ball["reveal"] == "pop"
    assert ball["text"] == "A ball"
    assert ball["box"]["w"] > 0 and ball["box"]["h"] > 0
    assert 0 <= ball["box"]["x"] < 1920 and 0 <= ball["box"]["y"] < 1080
    # The title has no cue and no delay, so it is on screen from the mount and the scan says so.
    uncued = by_cue[None]
    assert uncued["text"] == "A bowl"
    assert uncued["box"] == {"x": 120, "y": 80, "w": 600, "h": 90}
    # The equation's TeX travels with its row, so a check can read it without opening the page.
    tex = page.evaluate("() => window.__decktalk.catalog[0].elements['1.2'][0]")
    assert tex["tex"] == "\\sum_{i=1}^{n} x_i" and tex["cue"] == "1.2sum"
    assert page.evaluate("() => window.__decktalk.mode") == "index"
    assert page.evaluate("() => !!document.getElementById('dt-index')")
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_measuring_leaves_nothing_on_the_stage(page, tmp_path):
    """The measuring layer is hidden while it is used and gone when the index page shows."""
    page.goto(write_page(tmp_path, "clean.html", MARKUP_SCENE))
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => !document.getElementById('dt-measure')")
    assert page.evaluate("() => document.querySelectorAll('#dt-pan .dt-slide').length") == 0
    assert page.evaluate("() => getComputedStyle(document.getElementById('dt-stage')).display") == "none"


def test_a_played_scene_is_not_measured(page, tmp_path):
    """Measuring mounts every slide, so it never runs in a mode a recording could be made in."""
    page.goto(f"{write_page(tmp_path, 'unmeasured.html', MARKUP_SCENE)}?scene=1&t0=0&cues=1.1ball@0.1")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.catalog.every((c) => c.elements === undefined)")
    assert page.evaluate("() => document.querySelectorAll('#dt-pan .dt-slide').length") == 1


# ---- freezing at one cue ------------------------------------------------------------------


def test_freeze_at_and_before_one_cue(page, tmp_path):
    """?after=ID stops after that cue, ?before=ID stops just before it, and after wins over before."""
    url = write_page(tmp_path, "freezecue.html", MARKUP_SCENE)
    on = "(sel) => document.querySelector(sel).classList.contains('dt-shown')"
    page.goto(f"{url}?slide=1.1&after=1.1ball")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1ball"]
    assert page.evaluate(on, '[data-cue="1.1ball"]') is True
    assert page.evaluate(on, '[data-cue="1.1count"]') is False

    page.goto(f"{url}?slide=1.1&before=1.1count")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1ball"]
    assert page.evaluate(on, '[data-cue="1.1count"]') is False

    page.goto(f"{url}?slide=1.1&before=1.1ball")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == []
    assert page.evaluate(on, '[data-cue="1.1ball"]') is False

    page.goto(f"{url}?slide=1.1&after=1.1count&before=1.1ball")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1ball", "1.1count"]

    page.goto(f"{url}?slide=1.1&after=nope")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.warnings") == ['cue "nope" is not one of slide 1.1\'s cues']
    assert not page.errors
