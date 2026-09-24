"""decktalk-probe.js, the instrumentation a DeckTalk command injects into the page it opens.

Every page here is the same page `tests/contract/test_runtime.py` writes, opened through the hook
`media/browser.py` uses, so what these tests measure is what the probe adds and nothing the deck
carries. The cover, the wait helper, the measured boxes, the one report and the freeze that stops at
one cue live here, and none of them may reach a page that no command is driving.

    uv run pytest -m browser tests/contract/test_probe.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from contract.test_runtime import MARKUP_SCENE, deck

from decktalk.page import REPORT
from decktalk.toolchain.assets import (
    KATEX_DIR,
    PROBE_FILE,
    RUNTIME_FILE,
    katex_dir,
    package_file,
    probe_path,
    runtime_path,
)
from support.browser_pages import chromium_page, write_page

pytestmark = pytest.mark.browser


def instrument(page):
    """Add decktalk-probe.js to every page this one loads, which is what every command does.

    The recorder reaches the same bundle through `media/browser.py`, and that module's own test holds
    the hook. This file holds what the bundle does once it is there, so it adds it for itself.
    """
    page.add_init_script(probe_path().read_text(encoding="utf-8"))
    return page


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
    template = package_file("template")
    loaded = [p for p in template.rglob("*.html") if PROBE_FILE in p.read_text(encoding="utf-8")]
    assert loaded == []
    assert not list(template.rglob(PROBE_FILE))


def test_the_probe_imports_the_contract_and_the_seam_and_nothing_else():
    """The split is only honest while the probe knows the vocabulary and the sink and no DOM the runtime owns."""
    source = (package_file("runtime") / "src" / "probe" / "probe.ts").read_text(encoding="utf-8")
    imported = {line.split('"')[1] for line in source.splitlines() if line.startswith("import ")}
    assert imported == {"../contract.ts", "../telemetry.ts"}


def test_a_page_without_the_probe_still_freezes_lists_and_plays(page, tmp_path):
    """The split is only safe while a deck that no command drives keeps every mode it had."""
    # A second page of the same browser, opened the way a person opens one, with no init script.
    bare = page.context.browser.new_page(viewport={"width": 1920, "height": 1080})
    try:
        url = deck(tmp_path, "bare.html")
        bare.goto(f"{url}?slide=1.1")
        bare.wait_for_function("() => document.body.dataset.done === '1'")
        assert bare.evaluate("() => window.__decktalk.fired") == ["1.1:ball", "1.1:step"]
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
    page.goto(f"{deck(tmp_path, 'cover.html')}?scene=1&t0=signal")
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
    page.goto(deck(tmp_path, "wait.html", body + MARKUP_SCENE))
    assert page.evaluate("() => window.__dtprobe.ready()") is True
    assert page.evaluate("() => window.__late") is True
    assert not page.errors


# ---- the measured catalog -------------------------------------------------


def test_the_catalog_measures_every_cued_element(page, tmp_path):
    """In index mode each slide is laid out once, so every element carries a box in stage pixels."""
    head = "<style>.title { position: absolute; left: 120px; top: 80px; width: 600px; height: 90px; margin: 0 }</style>"
    page.goto(write_page(tmp_path, "boxes.html", MARKUP_SCENE, head=head))
    page.evaluate("() => window.__decktalk.ready")
    rows = page.evaluate("() => window.__decktalk.catalog[0].elements['1.1']")
    by_cue = {row["moments"].get("data-in"): row for row in rows}
    assert set(by_cue) == {"1.1:ball", "1.1:step", None}
    ball = by_cue["1.1:ball"]
    assert ball["attrs"]["data-describe"] == "a ball rests in the bowl"
    assert ball["attrs"]["data-in-style"] == "pop"
    assert ball["text"] == "A ball"
    assert ball["box"]["w"] > 0 and ball["box"]["h"] > 0
    assert 0 <= ball["box"]["x"] < 1920 and 0 <= ball["box"]["y"] < 1080
    # A moment other than an arrival is qualified exactly like one, so every moment is on the wire.
    assert by_cue["1.1:step"]["moments"]["data-back"] == "1.1:ball"
    # The title has no moment, so it is on screen from the mount, and the scan measures it all the same.
    uncued = by_cue[None]
    assert uncued["text"] == "A bowl"
    assert uncued["box"] == {"x": 120, "y": 80, "w": 600, "h": 90}
    # The equation's TeX travels with its row, so a check can read it without opening the page.
    tex = page.evaluate("() => window.__decktalk.catalog[0].elements['1.2'][0]")
    assert tex["attrs"]["data-tex"] == "\\sum_{i=1}^{n} x_i"
    assert tex["moments"]["data-in"] == "1.2:sum"
    assert page.evaluate("() => window.__decktalk.mode") == "index"
    assert page.evaluate("() => !!document.getElementById('dt-index')")
    assert not page.errors


def test_measuring_leaves_nothing_on_the_stage(page, tmp_path):
    """The measuring layer is hidden while it is used and gone when the index page shows."""
    page.goto(deck(tmp_path, "clean.html"))
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => !document.getElementById('dt-measure')")
    assert page.evaluate("() => !document.getElementById('dt-spans')")
    assert page.evaluate("() => document.querySelectorAll('#dt-pan .dt-slide').length") == 0
    assert page.evaluate("() => getComputedStyle(document.getElementById('dt-stage')).display") == "none"


def test_a_played_scene_is_not_measured(page, tmp_path):
    """Measuring mounts every slide, so it never runs in a mode a recording could be made in."""
    page.goto(f"{deck(tmp_path, 'unmeasured.html')}?scene=1&t0=0&cues=1.1:ball@0.1")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => window.__decktalk.catalog.every((c) => c.elements === undefined)")
    assert page.evaluate("() => document.querySelectorAll('#dt-pan .dt-slide').length") == 1


# ---- the one report ------------------------------------------------------------------------


def test_the_recorder_reads_the_whole_page_back_in_one_call(page, tmp_path):
    """Six round trips into one call is what the seam bought, so the report carries every published field."""
    url = deck(tmp_path, "report.html")
    page.goto(f"{url}?scene=1&t0=0&cues=1.1:ball@0.1,1.1:step@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length === 2")
    report = page.evaluate("() => window.__dtprobe.report()")
    assert set(report) == set(REPORT)
    assert report["mode"] == "cue"
    assert report["scene"] == "1"
    assert report["slide"] == "1.1"
    assert [row["id"] for row in report["cues"]] == ["1.1:ball", "1.1:step"]
    assert report["cues"][0]["due"] == 0.1
    assert report["cues"][0]["ran"] >= 0.1
    # The transcript is composed by the runtime, one sentence per cue, in document order.
    assert report["cues"][0]["describe"] == "a ball rests in the bowl appears. A ball"
    assert report["version"] == page.evaluate("() => window.__decktalk.version")


def test_a_synced_line_reports_itself_through_the_seam(page, tmp_path):
    """One word is far under the change floor, so the only record of it is the row the seam carries."""
    scene = """
    <div data-scene="20">
      <template data-slide="20.1">
        <p data-in="say" data-words data-describe="the line the voice reaches">alpha beta</p>
      </template>
    </div>
    """
    url = write_page(tmp_path, "spoken.html", scene)
    page.goto(f"{url}?scene=20&t0=0&cues=20.1:say@0.05&words=alpha@0.4,beta@0.8")
    page.wait_for_function("() => window.__dtprobe.report().words.length === 1")
    row = page.evaluate("() => window.__dtprobe.report().words[0]")
    assert row["text"] == "alpha beta"
    assert row["count"] == 2
    assert row["runAt"] == 0.4
    assert row["firstOn"] <= row["runAt"]


# ---- freezing at one cue ------------------------------------------------------------------


def test_freeze_at_and_before_one_cue(page, tmp_path):
    """?after=ID stops after that cue, ?before=ID stops just before it, and after wins over before."""
    url = deck(tmp_path, "freezecue.html")
    on = "(sel) => document.querySelector(sel).classList.contains('dt-shown')"
    page.goto(f"{url}?slide=1.1&after=1.1:ball")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball"]
    assert page.evaluate(on, ".ball") is True
    assert page.evaluate(on, ".step") is False

    page.goto(f"{url}?slide=1.1&before=1.1:step")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball"]
    assert page.evaluate(on, ".step") is False

    page.goto(f"{url}?slide=1.1&before=1.1:ball")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == []
    assert page.evaluate(on, ".ball") is False

    page.goto(f"{url}?slide=1.1&after=1.1:step&before=1.1:ball")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["1.1:ball", "1.1:step"]

    page.goto(f"{url}?slide=1.1&after=nope")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    reported = page.evaluate("() => window.__decktalk.warnings")
    assert [row["code"] for row in reported] == ["PAGE_FREEZE_CUE_UNKNOWN"]
    assert (reported[0]["slide"], reported[0]["cue"]) == ("1.1", "nope")
    assert not page.errors


def starter_deck(tmp_path: Path) -> str:
    """The deck `decktalk init` writes, laid out beside the runtime and the KaTeX release it loads."""
    root = tmp_path / "deck"
    shutil.copytree(package_file("template") / "starter" / "deck", root)
    shutil.copyfile(runtime_path(), root / RUNTIME_FILE)
    shutil.copytree(katex_dir(), root / KATEX_DIR)
    return (root / "index.html").resolve().as_uri()


def test_the_frame_before_a_cue_and_the_frame_at_it_are_two_pictures(page, tmp_path):
    """The two stills a check compares are the reveal itself, so an arrival held back stays hidden.

    This is the whole of what `check` measures: it freezes the starter's own slide either side of one
    cue and reads the share of the frame that changed. A page that drew both stills the same way
    would answer every cue of every project with CUE_NO_CHANGE while the film played the reveal
    perfectly, so the two files are compared here as bytes rather than as classes alone.
    """
    url = starter_deck(tmp_path)
    shown = "() => getComputedStyle(document.querySelector('.end')).opacity"
    page.goto(f"{url}?slide=3.1&before=3.1:make")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["3.1:idea", "3.1:again"]
    assert float(page.evaluate(shown)) == 0
    before = page.screenshot()

    page.goto(f"{url}?slide=3.1&after=3.1:make")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == ["3.1:idea", "3.1:again", "3.1:make"]
    assert float(page.evaluate(shown)) == 1
    assert page.evaluate("() => document.querySelector('.end').classList.contains('dt-shown')") is True
    after = page.screenshot()

    assert before != after
    assert not page.errors
