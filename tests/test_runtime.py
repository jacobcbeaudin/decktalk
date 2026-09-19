"""The page runtime, in a real headless Chromium. Skipped when Chromium is unavailable.

uv run pytest -m browser
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from decktalk.scaffold import init, runtime_path

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


def template_cues(deck: Path, scene: str) -> list[str]:
    """The cue ids the scaffold's cues.json lists for one scene, in order.

    A cue id starts with its scene number, and a page keeps its scene numbers when a clip section
    shifts the section numbers around them, so the cues are found by id rather than by section key.
    """
    data = json.loads((deck.parent.parent / "cues.json").read_text(encoding="utf-8"))
    return [c["cue"] for s in data["sections"].values() for c in s["cues"] if c["cue"].split(".")[0] == scene]


def template_pages(deck: Path) -> list[tuple[Path, str]]:
    """(page, scene) for every page section of the scaffold's decktalk.toml, in order."""
    root = deck.parent.parent
    doc = tomllib.loads((root / "decktalk.toml").read_text(encoding="utf-8"))
    return [((root / s["page"]).resolve(), str(s["scene"])) for s in doc["section"] if "page" in s]


def full_cues(deck: Path, scene: str, start: float = 0.1, gap: float = 0.05) -> str:
    """A ?cues= value with every cue of the scene, spaced `gap` seconds apart."""
    return ",".join(f"{cue}@{start + i * gap:.2f}" for i, cue in enumerate(template_cues(deck, scene)))


def custom_page(tmp_path: Path, name: str, script: str) -> str:
    """A page with the runtime and one inline script, returned as a file URL."""
    html = tmp_path / name
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        f'<script src="{runtime_path().resolve().as_uri()}"></script>'
        f"<script>{script}</script></body></html>",
        encoding="utf-8",
    )
    return html.resolve().as_uri()


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


def test_index_mode_exposes_catalog(page, deck):
    page.goto(deck.as_uri())
    catalog = page.evaluate("() => window.__decktalk.catalog")
    # Scene 3 lives in lesson.html, so index.html holds the other five, in the order the page declares them.
    assert [c["scene"] for c in catalog] == ["1", "2", "4", "6", "7", "5"]
    assert [c["slides"] for c in catalog] == [["1.1"], ["2.1"], ["4.1"], ["6.1"], ["7.1"], ["5.1"]]
    assert [c["name"] for c in catalog] == ["Open", "How it works", "The edit", "The edit", "The edit", "Close"]
    assert page.evaluate("() => window.__decktalk.mode") == "index"
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


S2_CUES = ["2.1mark", "2.1script", "2.1aloud", "2.1word", "2.1follows"]


def test_freeze_mode_reveals_everything(page, deck):
    page.goto(f"{deck.as_uri()}?slide=2.1")
    page.wait_for_timeout(200)
    assert page.evaluate("() => window.__decktalk.mode") == "freeze"
    assert page.evaluate("() => document.body.dataset.done") == "1"
    hidden = page.evaluate(
        "() => [...document.querySelectorAll('.dt-reveal')].filter(e => !e.classList.contains('dt-shown')).length"
    )
    assert hidden == 0
    # The listed cues fired too: 2.1mark moved the wordmark up, and 2.1follows drew the arrow to the slide card.
    assert page.evaluate("() => window.__decktalk.fired") == template_cues(deck, "2")
    assert page.evaluate("() => !!document.querySelector('.dt-slide .s2-mark.up')")
    assert page.evaluate("() => !!document.querySelector('.dt-slide .follow.draw')")


def test_cue_mode_fires_in_order_and_first_slide_mounts_at_zero(page, deck):
    cues = "2.1mark@0.3,2.1script@0.9,2.1aloud@1.2,2.1word@1.4,2.1follows@1.6"
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&cues={cues}")
    page.wait_for_timeout(120)
    # Slide 2.1 mounted at t=0 even though its first cue is at 0.3 s, and its listed reveals wait.
    assert page.evaluate("() => window.__decktalk.slide") == "2.1"
    hidden = "() => document.querySelector('[data-cue=\"2.1script\"]').classList.contains('dt-shown')"
    assert page.evaluate(hidden) is False
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.fired") == ["2.1mark"]
    assert page.evaluate("() => !!document.querySelector('.s2-mark.up')") is True
    assert page.evaluate(hidden) is False
    page.wait_for_function("() => window.__decktalk.fired.length >= 5")
    assert page.evaluate("() => window.__decktalk.fired") == S2_CUES
    assert page.evaluate(hidden) is True
    # Each cue is logged with when it was due, when it ran, and when its frame and the next two began.
    page.wait_for_function("() => window.__decktalk.cueLog.every((e) => e.after !== null)")
    log = page.evaluate("() => window.__decktalk.cueLog")
    assert [e["id"] for e in log] == S2_CUES
    for e, due in zip(log, (0.3, 0.9, 1.2, 1.4, 1.6), strict=True):
        assert e["due"] == due and e["ran"] >= due, e
        assert e["frame"] <= e["ran"] + 0.001 and e["frame"] < e["next"] < e["after"], e
    # The single slide means the scene is done at mount.
    assert page.evaluate("() => document.body.dataset.done") == "1"
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_handlers_and_unknown_cues(page, deck):
    page.goto(f"{deck.as_uri()}?scene=1&t0=0&cues={full_cues(deck, '1')},custom@0.5")
    page.evaluate("() => { window.__hits = []; DeckTalk.on('custom', () => window.__hits.push('custom')); }")
    page.wait_for_function(f"() => window.__decktalk.fired.length >= {len(template_cues(deck, '1')) + 1}")
    assert page.evaluate("() => window.__hits") == ["custom"]
    assert page.evaluate("() => window.__decktalk.fired") == [*template_cues(deck, "1"), "custom"]


def test_warnings_report_an_unknown_cue_id(page, deck):
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&cues=nope@0.1")
    page.wait_for_timeout(200)
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert any("unknown cue id nope" in w for w in warnings), warnings
    assert any("no slide owns" in w for w in warnings), warnings
    assert not page.errors


def test_preview_uses_holds(page, deck):
    page.goto(f"{deck.as_uri()}?scene=2&speed=20")  # the 16 s hold becomes 0.8 s, and the listed cues fire inside it
    page.wait_for_function("() => document.body.dataset.done === '1'", timeout=5000)
    assert page.evaluate("() => window.__decktalk.mode") == "preview"
    assert page.evaluate("() => window.__decktalk.slide") == "2.1"
    page.wait_for_function("() => window.__decktalk.fired.includes('2.1follows')", timeout=5000)


def test_signal_mode_waits_for_start_clock(page, deck):
    """With t0=signal the clock does not start at load, and starts when the recorder says so."""
    page.goto(f"{deck.as_uri()}?scene=2&t0=signal&cues={full_cues(deck, '2')}")
    page.wait_for_timeout(400)
    assert page.evaluate("() => window.__decktalk.fired") == []
    page.evaluate("() => DeckTalk.startClock()")
    page.wait_for_function("() => window.__decktalk.fired.includes('2.1mark')", timeout=2000)


def test_katex_typesets_data_tex_with_the_vendored_copy(page, deck, tmp_path):
    """init vendors KaTeX beside the pages, and __decktalk.ready waits until every [data-tex] element is typeset."""
    assert "./katex/katex.min.js" in deck.read_text(encoding="utf-8")
    katex = deck.parent / "katex"
    html = tmp_path / "tex.html"
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<link rel="stylesheet" href="{(katex / "katex.min.css").resolve().as_uri()}">'
        f'<script src="{(katex / "katex.min.js").resolve().as_uri()}"></script>'
        f'<script src="{runtime_path().resolve().as_uri()}"></script></head><body>'
        "<script>DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => "
        '`<p data-tex="\\\\eta = 2">eta = 2</p><p data-tex-display data-tex="\\\\frac{1}{2}">1/2</p>` } ] });</script>'
        "</body></html>",
        encoding="utf-8",
    )
    page.goto(f"{html.resolve().as_uri()}?slide=1.1")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate("() => document.querySelectorAll('[data-tex][data-typeset] .katex').length") == 2
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_scene_1_shows_the_spoken_start_of_each_count_word(page, deck):
    """With ?words=, scene 1 writes the voice's own start times of one, two, and three under the boxes."""
    words = "A@0.1,bowl@0.3,count@3.9,One@5.12,Two@6.08,three@6.41,its@11.5,word@11.7"
    page.goto(f"{deck.as_uri()}?scene=1&t0=0&cues={full_cues(deck, '1')}&words={words}")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1word')")
    times = page.evaluate("() => [...document.querySelectorAll('.s1 .time')].map((e) => e.textContent)")
    assert times == ["5.12 s", "6.08 s", "6.41 s"]
    page.goto(f"{deck.as_uri()}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    frozen = page.evaluate("() => [...document.querySelectorAll('.s1 .time')].map((e) => e.textContent)")
    assert len(frozen) == 3 and all(t.endswith(" s") for t in frozen), frozen
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


# A data-text="spoken" caption styled to show every word unlit at its cue and light each one as it is spoken, with the
# word being spoken, the last lit one, in the accent.
SYNC_PAGE = """
document.head.insertAdjacentHTML('beforeend', `<style>
  .cap .dt-word { opacity: 1; color: rgb(113, 113, 122); transition: none; }
  .cap .dt-word.dt-shown { color: rgb(9, 9, 11); }
  .cap .dt-word.dt-shown:has(+ .dt-word:not(.dt-shown)) { color: rgb(44, 31, 234); }
</style>`);
DeckTalk.scene(1, { slides: [ { id: '1.1', owns: ['1.1cap'],
  render: () => `<p class="cap" data-cue="1.1cap" data-text="spoken">The height is the error, so lower is better.</p>`
  } ] });
"""


def test_synced_caption_lights_each_word_as_it_is_spoken(page, tmp_path):
    """A data-text="spoken" caption wraps every word at its cue and lights each one at its spoken second."""
    words = "The@0.1,height@0.15,is@0.2,the@0.25,error@0.3,so@0.35,lower@6,is@6.2,better@6.4"
    url = custom_page(tmp_path, "sync.html", SYNC_PAGE)
    page.goto(f"{url}?scene=1&t0=0&cues=1.1cap@0.05&words={words}")
    page.wait_for_function("() => document.querySelectorAll('.cap .dt-word.dt-shown').length >= 6", timeout=3000)
    assert page.evaluate("() => document.querySelectorAll('.cap .dt-word').length") == 9
    assert page.evaluate("() => document.querySelectorAll('.cap .dt-word.dt-shown').length") == 6
    style = "(sel) => { const s = getComputedStyle(document.querySelector(sel)); return [s.opacity, s.color]; }"
    assert page.evaluate(style, ".cap .dt-word:not(.dt-shown)") == ["1", "rgb(113, 113, 122)"]
    # The word being spoken is the last lit one, and it carries the accent.
    assert page.evaluate(style, ".cap .dt-word.dt-shown:has(+ .dt-word:not(.dt-shown))") == ["1", "rgb(44, 31, 234)"]
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_scene_ready_warns_when_katex_never_loads(page, tmp_path):
    """A page with [data-tex] and no KaTeX resolves __decktalk.ready after the 5 s poll with a warning."""
    html = tmp_path / "notex.html"
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        f'<script src="{runtime_path().resolve().as_uri()}"></script>'
        "<script>DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => `<p data-tex=\"x^2\">x^2</p>` } ] });"
        "</script>"
        "</body></html>",
        encoding="utf-8",
    )
    page.goto(f"{html.resolve().as_uri()}?slide=1.1")
    page.evaluate("() => window.__decktalk.ready")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert any("KaTeX" in w for w in warnings), warnings
    assert page.evaluate("() => document.querySelector('[data-tex]').textContent") == "x^2"


def test_cue_mode_warns_when_a_slide_mounts_equations_without_katex(page, tmp_path):
    """In cue mode a slide mounts after the ready check ran, so a later check warns that KaTeX is missing."""
    html = tmp_path / "notex-cues.html"
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        f'<script src="{runtime_path().resolve().as_uri()}"></script>'
        "<script>DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => "
        '`<p data-cue="1.1a" data-tex="x^2">x^2</p>` } ] });</script>'
        "</body></html>",
        encoding="utf-8",
    )
    page.goto(f"{html.resolve().as_uri()}?scene=1&t0=0&cues=1.1a@0.2")
    page.wait_for_function("() => window.__decktalk.warnings.some((w) => w.includes('KaTeX'))", timeout=9000)
    assert page.evaluate("() => document.querySelector('[data-tex]').textContent") == "x^2"


def test_a_cue_that_hits_nothing_is_a_warning(page, deck):
    """A cue id owned by a slide by prefix but with no data-cue, handler, or slide of its own is reported."""
    page.goto(f"{deck.as_uri()}?scene=4&t0=0&cues={full_cues(deck, '4')},4.1answer@0.7")
    page.wait_for_function("() => window.__decktalk.fired.includes('4.1answer')")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert warnings == ['cue "4.1answer" matches no element, handler, or slide'], warnings


def test_every_template_slide_freezes_without_warnings(page, deck):
    """Every cue the template lists reveals an element or runs a handler, so no slide warns when frozen."""
    for url in dict.fromkeys(p.as_uri() for p, _ in template_pages(deck)):
        page.goto(url)
        catalog = page.evaluate("() => window.__decktalk.catalog")
        assert catalog, url
        for entry in catalog:
            for slide in entry["slides"]:
                page.goto(f"{url}?slide={slide}")
                page.evaluate("() => window.__decktalk.ready")
                assert page.evaluate("() => window.__decktalk.warnings") == [], slide
    assert not page.errors


def test_every_template_scene_plays_its_full_cue_list_without_warnings(page, deck):
    """Each page section in cue mode with every cue of its scene warns nothing and logs no error."""
    logged: list[str] = []

    def on_console(msg):
        if msg.type == "error":
            logged.append(msg.text)

    page.on("console", on_console)
    try:
        for path, scene in template_pages(deck):
            cues = template_cues(deck, scene)
            assert cues, scene
            page.goto(f"{path.as_uri()}?scene={scene}&t0=0&cues={full_cues(deck, scene)}")
            page.evaluate("() => window.__decktalk.ready")
            page.wait_for_function(f"() => window.__decktalk.fired.length >= {len(cues)}", timeout=10000)
            assert page.evaluate("() => window.__decktalk.fired") == cues, scene
            assert page.evaluate("() => window.__decktalk.warnings") == [], scene
    finally:
        page.remove_listener("console", on_console)
    assert logged == []
    assert not page.errors


def test_katex_parse_error_is_a_warning(page, deck, tmp_path):
    """A data-tex value KaTeX cannot parse renders in red and is reported, since throwOnError is off."""
    katex = (deck.parent / "katex" / "katex.min.js").resolve().as_uri()
    html = tmp_path / "badtex.html"
    html.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<script src="{katex}"></script><script src="{runtime_path().resolve().as_uri()}"></script></head><body>'
        "<script>DeckTalk.scene(1, { slides: [ { id: '1.1', "
        'render: () => `<p data-tex="\\\\frac{1}">x</p>` } ] });</script>'
        "</body></html>",
        encoding="utf-8",
    )
    page.goto(f"{html.resolve().as_uri()}?slide=1.1")
    page.evaluate("() => window.__decktalk.ready")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert (
        'data-tex could not be parsed: "\\frac{1}" (write \\\\ for every backslash inside a template literal)'
        in warnings
    ), warnings
    assert page.evaluate("() => !!document.querySelector('.katex-error')")


def test_slide_handlers_receive_the_slide_and_ctx(page, tmp_path):
    """A slide's on[id] is called with the mounted slide element and { id, at, frozen, slideId }."""
    page.goto(f"{custom_page(tmp_path, 'handlers.html', HANDLER_PAGE)}?scene=1&t0=0&cues=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    got = page.evaluate("() => window.__got")
    on = next(g for g in got if g["kind"] == "on")
    assert on["slide"] == "1.1" and on["current"] is True
    assert set(on["ctx"]) == {"id", "at", "frozen", "slideId"}
    assert on["ctx"]["id"] == "1.1a" and on["ctx"]["slideId"] == "1.1" and on["ctx"]["frozen"] is False
    assert 0.1 <= on["ctx"]["at"] < 0.4, on
    # A handler that takes no arguments keeps working.
    assert any(g["kind"] == "zero" for g in got), got
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_global_handlers_receive_the_slide_and_ctx(page, tmp_path):
    """Every DeckTalk.on handler is called with the slide mounted when the cue fires."""
    page.goto(f"{custom_page(tmp_path, 'handlers.html', HANDLER_PAGE)}?scene=1&t0=0&cues=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 2")
    got = page.evaluate("() => window.__got")
    glob = next(g for g in got if g["kind"] == "global")
    assert glob["slide"] == "1.2"
    assert glob["ctx"]["id"] == "1.2a" and glob["ctx"]["slideId"] == "1.2" and glob["ctx"]["frozen"] is False
    assert glob["ctx"]["at"] >= 0.4, glob
    # The slide handler runs before the global ones, and a zero-argument global handler still runs.
    kinds = [g["kind"] for g in got]
    assert kinds.index("zero") < kinds.index("global") < kinds.index("global-zero"), kinds


def test_enter_receives_the_same_ctx(page, tmp_path):
    """A slide's enter gets the slide and a ctx whose id and slideId are the slide id, frozen or not."""
    url = custom_page(tmp_path, "handlers.html", HANDLER_PAGE)
    page.goto(f"{url}?scene=1&t0=0&cues=1.1a@0.1,1.2a@0.4")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    enter = next(g for g in page.evaluate("() => window.__got") if g["kind"] == "enter")
    assert enter["slide"] == "1.1"
    assert set(enter["ctx"]) == {"id", "at", "frozen", "slideId"}
    assert enter["ctx"]["id"] == "1.1" and enter["ctx"]["slideId"] == "1.1" and enter["ctx"]["frozen"] is False
    assert 0 <= enter["ctx"]["at"] < 0.1, enter
    page.goto(f"{url}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    frozen = [g for g in page.evaluate("() => window.__got") if g["kind"] in ("enter", "on")]
    assert [g["kind"] for g in frozen] == ["enter", "on"]
    assert all(g["ctx"]["frozen"] is True and g["ctx"]["slideId"] == "1.1" for g in frozen), frozen


def test_the_edit_scenes_show_the_parts_the_rebuild_and_the_proof_when_frozen(page, deck):
    """Frozen, scene 4 ends on the five parts, scene 6 on one part voiced again, and scene 7 on the Close's frame."""
    # The edit hides elements with visibility: hidden, which checkVisibility() counts only when asked to.
    shown = "(e) => e.checkVisibility({ visibilityProperty: true })"
    visible = f"(sel) => [...document.querySelectorAll(sel)].filter({shown}).length"
    text = "(sel) => [...document.querySelectorAll(sel)].map((e) => e.textContent.trim())"
    page.goto(f"{deck.as_uri()}?slide=4.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    page.evaluate("() => window.__decktalk.ready")
    assert page.evaluate(text, ".dt-slide .ed-chip .nm") == [
        "Open",
        "How it works",
        "How AI learns",
        "The edit",
        "Close",
    ]
    assert page.evaluate(visible, ".dt-slide .ed-chip.watched") == 3
    assert page.evaluate(visible, ".dt-slide .ed-pill.before") == 1
    # Every still the edit shows ships with the scaffold and decodes.
    stills = page.evaluate("() => [...document.querySelectorAll('.dt-slide img')].map((i) => [i.src, i.naturalWidth])")
    assert stills and all(w > 0 for _, w in stills), stills

    page.goto(f"{deck.as_uri()}?slide=6.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate(visible, ".dt-slide .ed-chip.new") == 1
    assert page.evaluate(visible, ".dt-slide .ed-chip.kept") == 4
    assert page.evaluate(visible, ".dt-slide .ed-pill.after") == 1
    assert page.evaluate(text, ".dt-slide .ed-panel .l2") == ["One. Two, three, four."]

    page.goto(f"{deck.as_uri()}?slide=7.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => !!document.querySelector('.dt-slide .ed-close.grown')")
    assert page.evaluate(visible, ".dt-slide .p-proof") == 0
    assert page.evaluate("() => window.__decktalk.warnings") == []
    assert not page.errors


def test_spoken_text_has_no_container_animation(page, tmp_path):
    """A data-text="spoken" element without data-reveal gets data-reveal="instant", so it never fades or rises."""
    page.goto(f"{custom_page(tmp_path, 'sync.html', SYNC_PAGE)}?scene=1&t0=0&cues=1.1cap@0.05")
    page.wait_for_function("() => window.__decktalk.fired.includes('1.1cap')")
    cap = "document.querySelector('[data-cue=\"1.1cap\"]')"
    assert page.evaluate(f"() => {cap}.getAttribute('data-reveal')") == "instant"
    assert page.evaluate(f"() => {cap}.classList.contains('dt-shown')")
    assert page.evaluate(f"() => getComputedStyle({cap}).animationName") == "none"
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_spoken_text_keeps_an_explicit_reveal(page, tmp_path):
    """A data-text="spoken" element that sets its own data-reveal keeps it."""
    script = (
        "DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => "
        '`<p data-delay="0" data-text="spoken" data-reveal="fade">Hello there</p>` } ] });'
    )
    page.goto(f"{custom_page(tmp_path, 'syncfx.html', script)}?scene=1")
    page.wait_for_function("() => !!document.querySelector('[data-text=spoken].dt-shown')")
    assert page.evaluate("() => document.querySelector('[data-text=spoken]').getAttribute('data-reveal')") == "fade"
    assert (
        page.evaluate("() => getComputedStyle(document.querySelector('[data-text=spoken]')).animationName")
        == "dt-fadein"
    )


def test_warns_when_a_slide_owns_no_listed_cue(page, tmp_path):
    """In cue mode a slide that owns none of the listed cues never mounts, and the page says so."""
    script = (
        "DeckTalk.scene(3, { slides: ["
        " { id: '3.1', owns: ['3.1a'], render: () => `<p data-cue=\"3.1a\">a</p>` },"
        " { id: '3.2', owns: ['3.2a'], render: () => `<p data-cue=\"3.2a\">b</p>` } ] });"
    )
    page.goto(f"{custom_page(tmp_path, 'nocue.html', script)}?scene=3&t0=0&cues=3.1a@0.1")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.warnings") == [
        'slide "3.2" owns no cue in ?cues=, so it never appears'
    ]


def test_warns_when_a_data_cue_is_not_listed(page, deck):
    """In cue mode an element waiting for a cue that ?cues= leaves out reveals on its timer, with a warning."""
    cues = ",".join(part for part in full_cues(deck, "2").split(",") if not part.startswith("2.1aloud@"))
    page.goto(f"{deck.as_uri()}?scene=2&t0=0&cues={cues}")
    page.wait_for_function("() => window.__decktalk.fired.length >= 1")
    assert page.evaluate("() => window.__decktalk.warnings") == [
        'data-cue "2.1aloud" is not in ?cues=, so it reveals at its data-delay time after the mount'
    ]
    assert page.evaluate("() => document.querySelector('[data-cue=\"2.1aloud\"]').classList.contains('dt-shown')")


def test_warns_when_a_reveal_mode_has_no_trigger(page, tmp_path):
    """A data-text mode without data-cue or data-delay never reveals, in any mode."""
    script = (
        "DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => "
        '`<p data-text="spoken">hi there</p><b data-text="count">5</b><i data-text="type 30ms">x</i>'
        '<p data-delay="0" data-text="spoken">fine</p>` } ] });'
    )
    page.goto(f"{custom_page(tmp_path, 'notrigger.html', script)}?slide=1.1")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert sorted(page.evaluate("() => window.__decktalk.warnings")) == sorted(
        f'data-text="{mode}" on an element without data-cue or data-delay never reveals, so add data-delay="0"'
        for mode in ("spoken", "count", "type 30ms")
    )
    assert not page.errors


def test_warns_when_a_preview_cue_falls_past_the_hold(page, tmp_path):
    """A preview cue at or past its slide's hold fires on the next slide, except on the last slide."""
    script = (
        "DeckTalk.scene(3, { slides: ["
        " { id: '3.1', hold: 30, preview: { '3.1go': 1, '3.1min': 32 },"
        '   render: () => `<p data-cue="3.1go">a</p><p data-cue="3.1min">b</p>` },'
        " { id: '3.2', hold: 5, preview: { '3.2late': 9 }, render: () => `<p data-cue=\"3.2late\">c</p>` } ] });"
    )
    url = custom_page(tmp_path, "pasthold.html", script)
    page.goto(f"{url}?scene=3&speed=50")
    page.wait_for_function("() => window.__decktalk.mode === 'preview'")
    warnings = page.evaluate("() => window.__decktalk.warnings")
    assert 'slide "3.1" fires "3.1min" at 32 s but holds 30 s, so it fires on the next slide' in warnings, warnings
    assert not any("3.2late" in w for w in warnings), warnings
    # Cue mode ignores holds, so the same page warns nothing about them.
    page.goto(f"{url}?scene=3&t0=0&cues=3.1go@0.1,3.1min@0.2,3.2late@0.3")
    page.wait_for_function("() => window.__decktalk.fired.length >= 3")
    assert page.evaluate("() => window.__decktalk.warnings") == []


def test_freeze_at_one_cue_stops_there(page, deck):
    """?slide=2.1&after=2.1aloud fires the slide's cues up to 2.1aloud and leaves later reveals hidden."""
    page.goto(f"{deck.as_uri()}?slide=2.1&after=2.1aloud")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.mode") == "freeze"
    cues = template_cues(deck, "2")
    assert page.evaluate("() => window.__decktalk.fired") == cues[: cues.index("2.1aloud") + 1]
    on = "(sel) => document.querySelector(sel).classList.contains('dt-shown')"
    assert page.evaluate(on, "[data-cue='2.1aloud']") is True
    assert page.evaluate(on, "[data-cue='2.1word']") is False
    assert page.evaluate(on, "[data-cue='2.1follows']") is False
    assert page.evaluate("() => window.__decktalk.warnings") == []
    # An id that is not one of the slide's cues warns and freezes the whole slide.
    page.goto(f"{deck.as_uri()}?slide=2.1&after=nope")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.warnings") == ['cue "nope" is not one of slide 2.1\'s cues']
    assert page.evaluate("() => window.__decktalk.fired") == template_cues(deck, "2")
    assert page.evaluate(on, "[data-cue='2.1follows']") is True
    assert not page.errors


def test_catalog_lists_each_slides_cues_in_preview_order(page, deck):
    page.goto(deck.as_uri())
    catalog = page.evaluate("() => window.__decktalk.catalog")
    assert all(list(c["cues"]) == c["slides"] for c in catalog)
    assert next(c for c in catalog if c["scene"] == "2")["cues"] == {"2.1": template_cues(deck, "2")}
    assert not page.errors


def test_freeze_before_one_cue_stops_just_before_it(page, deck):
    """?slide=2.1&before=2.1aloud fires the slide's cues before 2.1aloud, and 2.1aloud stays hidden."""
    cues = template_cues(deck, "2")
    on = "(sel) => document.querySelector(sel).classList.contains('dt-shown')"
    page.goto(f"{deck.as_uri()}?slide=2.1&before=2.1aloud")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.mode") == "freeze"
    assert page.evaluate("() => window.__decktalk.fired") == cues[: cues.index("2.1aloud")]
    assert page.evaluate(on, "[data-cue='2.1aloud']") is False
    assert page.evaluate(on, "[data-cue='2.1word']") is False
    assert page.evaluate("() => window.__decktalk.warnings") == []
    # Before the first cue, the slide shows with nothing fired.
    page.goto(f"{deck.as_uri()}?slide=2.1&before={cues[0]}")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == []
    # cue wins over before.
    page.goto(f"{deck.as_uri()}?slide=2.1&after=2.1aloud&before={cues[0]}")
    page.wait_for_function("() => document.body.dataset.done === '1'")
    assert page.evaluate("() => window.__decktalk.fired") == cues[: cues.index("2.1aloud") + 1]
    assert page.evaluate(on, "[data-cue='2.1aloud']") is True
    assert not page.errors


def test_record_page_stores_page_errors_in_the_recording_log(page, tmp_path):
    """A page that throws, and a page without the runtime, both leave page_errors that check turns into PAGE ERROR."""
    from decktalk.media.browser import NO_CATALOG, record_page
    from decktalk.settings import RecordConfig
    from decktalk.stages.measure import log_verdicts

    runtime = runtime_path().resolve().as_uri()
    broken = tmp_path / "broken.html"
    broken.write_text(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>\n'
        f'<script src="{runtime}"></script>\n'
        "<script>DeckTalk.scene(1, { slides: [ { id: '1.1', render: () => `<p>hi</p>` } ] });</script>\n"
        "<script>\nnotDefinedAnywhere();\n</script>\n"
        "</body></html>",
        encoding="utf-8",
    )
    bare = tmp_path / "bare.html"
    bare.write_text("<!doctype html><html><body><p>no runtime here</p></body></html>", encoding="utf-8")
    kw = dict(settle_seconds=0.1, min_cover_seconds=0.1, width=640, height=360, color_scheme="light")
    browser = page.context.browser  # the module's Playwright already owns this thread's sync loop
    recording_log = record_page(browser, f"{broken.as_uri()}?slide=1.1", 0.5, tmp_path / "01-section.webm", **kw)
    recording_log2 = record_page(browser, bare.as_uri(), 0.5, tmp_path / "02-section.webm", **kw)
    assert len(recording_log.page_errors) == 1, recording_log.page_errors
    assert recording_log.page_errors[0].startswith("ReferenceError: notDefinedAnywhere is not defined"), (
        recording_log.page_errors
    )
    assert "(broken.html:5)" in recording_log.page_errors[0], recording_log.page_errors
    assert recording_log2.page_errors == [NO_CATALOG]
    for s, name in ((recording_log, "01-section.json"), (recording_log2, "02-section.json")):
        assert "PAGE ERROR" in log_verdicts(s, RecordConfig())
        reloaded = type(s).load(tmp_path / name)
        assert reloaded is not None and reloaded.page_errors == s.page_errors


@pytest.mark.media
def test_recorder_keeps_frames_flowing_so_reveals_on_a_still_page_land_on_schedule(page, tmp_path):
    """Reveals on a page that never moves record on their scheduled frame, not one or two frames early.

    Playwright stamps a frame by when it was swapped, and an idle compositor swaps earlier in the
    frame than a busy one. The recorder's keep-alive keeps the compositor equally busy before and
    after narration t=0, so the offsets are measured against the same stamping as the trim point.
    When the motion stopped with the cover, every reveal here read -40 ms. The keep-alive is also
    invisible to verify: nothing changes at its onset diff level before the first reveal.
    """
    from decktalk.media import ffmpeg
    from decktalk.media.browser import record_page
    from decktalk.settings import RecordConfig, VerifyConfig
    from decktalk.stages.measure import measure_lead

    at = [0.8, 1.6, 2.4]
    letters = "abc"
    body = "".join(
        f'<p data-cue="1.1{c}" data-reveal="instant" style="position:absolute;left:{100 + i * 400}px;top:250px;'
        f'margin:0;font:700 200px sans-serif">{c}</p>'
        for i, c in enumerate(letters)
    )
    cues = ",".join(f"'1.1{c}'" for c in letters)
    script = f"DeckTalk.scene(1, {{ slides: [ {{ id: '1.1', owns: [{cues}], render: () => `{body}` }} ] }});"
    url = custom_page(tmp_path, "still.html", script)
    cues = ",".join(f"1.1{c}@{t}" for c, t in zip(letters, at, strict=True))
    out = tmp_path / "01.webm"
    kw = dict(settle_seconds=0.5, min_cover_seconds=0.5, width=1280, height=720, color_scheme="light")
    recording_log = record_page(page.context.browser, f"{url}?scene=1&t0=signal&cues={cues}", 3.0, out, **kw)
    assert not recording_log.page_errors and not recording_log.warnings, (
        recording_log.page_errors,
        recording_log.warnings,
    )
    trim, method = measure_lead(out, recording_log.settle_seconds, RecordConfig())
    assert method.startswith("cover"), method
    series = ffmpeg.changed_series(out, trim, trim, trim + 3.0, fps=25, level=40, width=480, height=270)
    offsets: list[int] = []
    prev = 0.0
    for t, pct in series:
        if len(offsets) < len(at) and pct - prev > 0.1:
            offsets.append(round((t - trim - at[len(offsets)]) * 1000))
        prev = pct
    assert len(offsets) == len(at), series
    # On the 25 fps grid a reveal lands on its own frame (0) or, when its timer fires late, the next (+40).
    assert all(0 <= ms <= 40 for ms in offsets), offsets
    cfg = VerifyConfig()
    still = ffmpeg.changed_series(
        out, trim, trim, trim + at[0] - 0.1, fps=25, level=cfg.onset_diff_level, width=1280, height=720
    )
    assert still and max(pct for _, pct in still) == 0.0, still
