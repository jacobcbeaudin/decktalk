"""The two pages in a browser: what a visitor can still reach when the page's own script fails.

`tests/test_site.py` reads the committed files. This opens them. Every `.cut` on a page starts at
`opacity: 0` and is revealed by an observer on the last line of `site/app.js`, which means a script
that throws, or a `data.js` that did not load, used to leave the films, how it works, the edit
section and the install commands invisible — the hero and the footer, and no way to install
anywhere on the page. That is the failure this file exists to prevent.

The explanation now lives on `how.html` and the install command on `index.html`, so each page is
walked for its own sections and each page is broken in both ways. `site/app.js` is still one script
across both, and a page missing a section another page owns must not throw on the way past it.
"""

from __future__ import annotations

import http.server
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.timeout(120)]

SITE = Path(__file__).resolve().parent.parent / "site"
# The sections a visitor needs to be able to reach on each page, and the id each one carries.
SECTIONS = {
    "index.html": ("films", "how-more", "install"),
    "how.html": ("how", "edit"),
}
# The one thing on each page a broken script must never be able to hide: the install command on the
# landing page, and on how.html the explanation that is the whole reason the page exists.
ESSENTIAL = {"index.html": "install", "how.html": "how"}
PAGES = list(SECTIONS)
# The one line the whole site tells a stranger to run. It is one string in one component, and the
# tests below are the reason it can never be a string a script is allowed to hide or to mangle.
COMMAND = "curl -LsSf https://decktalk.ai/install.sh | sh"


@pytest.fixture(scope="module")
def origin() -> Iterator[str]:
    """site/ over http, because a file:// origin refuses the module and fetch rules the page relies on."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a: object, **k: object) -> None:
            super().__init__(*a, directory=str(SITE), **k)  # type: ignore[arg-type]

        def translate_path(self, path: str) -> str:
            """`/how` is `site/how.html`, the way the deployment serves it.

            Cloudflare strips the extension and redirects `/how.html` to `/how`, so the pages link
            to the extensionless path and a link followed here has to land the same way.
            """
            local = super().translate_path(path)
            return f"{local}.html" if not Path(local).exists() and Path(f"{local}.html").is_file() else local

        def log_message(self, *a: object) -> None:
            return

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 0), Quiet) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        yield f"http://127.0.0.1:{srv.server_address[1]}"
        srv.shutdown()


@pytest.fixture
def page(origin: str) -> Iterator[object]:
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        exe = Path(pw.chromium.executable_path)
        if not exe.exists():
            pytest.skip("Chromium is missing: run `decktalk install` first")
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 900})
        # The hero fetches its voice from the media host. No test here listens to it, and on a
        # runner with a slow or filtered route to the internet, waiting on it is waiting on
        # something the test is not about. Refused here so the page is only ever itself.
        pg.route("https://media.decktalk.ai/**", lambda route: route.abort())
        pg.origin = origin  # type: ignore[attr-defined]
        yield pg
        browser.close()


def walk(pg: object, name: str) -> None:
    """Scroll the whole page the way a reader does, then wait for the reveal rather than guess at it.

    The observer reveals a section when it enters the viewport, so a jump to the bottom passes over
    the middle ones without ever showing them: the walk is the point. What is not the point is how
    long a runner takes to get there. A fixed sleep passed on macOS and Linux and failed every
    section on Windows, which is slower, so this waits for the condition the test is about and only
    fails when it never arrives.
    """
    height = pg.evaluate("document.documentElement.scrollHeight")  # type: ignore[attr-defined]
    step = pg.viewport_size["height"] // 2  # type: ignore[attr-defined]
    for y in range(0, height, step):
        pg.evaluate(f"window.scrollTo(0, {y})")  # type: ignore[attr-defined]
        pg.wait_for_timeout(60)  # type: ignore[attr-defined]
    try:
        pg.wait_for_function(  # type: ignore[attr-defined]
            """(ids) => ids.every((id) => {
                const el = document.getElementById(id);
                if (!el) return false;
                const cut = el.querySelector(".cut") ?? el;
                return parseFloat(getComputedStyle(cut).opacity) > 0;
            })""",
            arg=list(SECTIONS[name]),
            timeout=15000,
        )
    except Exception:  # noqa: BLE001 — the assertion that follows says which section it was
        pass


def diagnosis(pg: object, name: str) -> str:
    """What the page actually looked like, for an assertion that fails on a machine nobody is sitting at.

    A bare "all False" says the reveal did not happen and nothing about why. This says whether the
    script loaded at all, whether the page thinks it has JavaScript, and the opacity of each section
    with the element it was read from, which separates "never revealed" from "measured the wrong box".
    """
    return pg.evaluate(  # type: ignore[attr-defined]
        """(ids) => {
            const scripts = [...document.scripts].map((s) => s.src.split('/').pop() || 'inline');
            const rows = ids.map((id) => {
                const el = document.getElementById(id);
                if (!el) return `${id}: MISSING`;
                const cut = el.querySelector('.cut');
                const box = cut ?? el;
                return `${id}: opacity=${getComputedStyle(box).opacity} from=${cut ? '.cut' : 'section'}`
                     + ` in=${box.classList.contains('in')}`;
            });
            return `html.class=${document.documentElement.className}`
                 + ` scripts=[${scripts.join(',')}] height=${document.documentElement.scrollHeight}`
                 + ` reveal=${typeof window.HALFWAY} | ` + rows.join(' | ');
        }""",
        list(SECTIONS[name]),
    )


def visible_sections(pg: object, name: str) -> dict[str, bool]:
    """Which of the page's sections a visitor can actually see, by computed opacity."""
    return pg.evaluate(  # type: ignore[attr-defined]
        """(ids) => Object.fromEntries(ids.map((id) => {
            const el = document.getElementById(id);
            if (!el) return [id, false];
            const cut = el.querySelector(".cut") ?? el;
            return [id, parseFloat(getComputedStyle(cut).opacity) > 0];
        }))""",
        list(SECTIONS[name]),
    )


@pytest.mark.parametrize("name", PAGES)
def test_every_section_is_visible_when_the_page_works(page: object, name: str) -> None:
    """The baseline, so the failure tests below cannot pass on a page that reveals nothing anyway.

    It also holds the property the split depends on: one script drives both pages, each page has
    only some of its DOM, and neither page may throw on the way past what it does not have.
    """
    thrown: list[str] = []
    page.on("pageerror", lambda e: thrown.append(str(e)))  # type: ignore[attr-defined]
    page.goto(f"{page.origin}/{name}", wait_until="domcontentloaded")  # type: ignore[attr-defined]
    # Walked rather than jumped: the observer reveals a section when it enters the viewport, so a
    # jump to the bottom passes over the middle ones without ever showing them.
    walk(page, name)
    assert not thrown, f"{name} threw: {thrown}"
    assert all(visible_sections(page, name).values()), diagnosis(page, name)


@pytest.mark.parametrize("name", PAGES)
def test_the_page_survives_a_data_file_that_did_not_load(page: object, name: str) -> None:
    """data.js 404s, so `window.HALFWAY` is undefined and the script returns on its eighth line."""
    page.route("**/data.js", lambda route: route.fulfill(status=404, body=""))  # type: ignore[attr-defined]
    page.goto(f"{page.origin}/{name}", wait_until="domcontentloaded")  # type: ignore[attr-defined]
    walk(page, name)
    seen = visible_sections(page, name)
    assert seen[ESSENTIAL[name]], f"{ESSENTIAL[name]} unreachable on {name} :: {diagnosis(page, name)}"
    assert all(seen.values()), diagnosis(page, name)


@pytest.mark.parametrize("name", PAGES)
def test_the_page_survives_a_script_that_throws(page: object, name: str) -> None:
    """The likelier failure: data.js loads but something below line eight throws, so the observer
    that reveals the page is never reached."""
    page.add_init_script("window.addEventListener('DOMContentLoaded', () => { null.boom; });")  # type: ignore[attr-defined]
    page.goto(f"{page.origin}/{name}", wait_until="domcontentloaded")  # type: ignore[attr-defined]
    walk(page, name)
    seen = visible_sections(page, name)
    assert seen[ESSENTIAL[name]], f"{ESSENTIAL[name]} unreachable on {name} :: {diagnosis(page, name)}"


def read_command(pg: object) -> dict[str, object]:
    """What a visitor can see, select and press at the install section's first command."""
    return pg.evaluate(  # type: ignore[attr-defined]
        """() => {
            const pill = document.querySelector("#install .pill");
            if (!pill) return {};
            const code = pill.querySelector("code");
            getSelection().selectAllChildren(code);
            const selected = getSelection().toString();
            getSelection().removeAllRanges();
            return {
                opacity: parseFloat(getComputedStyle(pill).opacity),
                command: pill.querySelector("[data-cmd]").textContent,
                selected,
                buttons: document.querySelectorAll(".pill button").length,
            };
        }"""
    )


def test_the_install_command_survives_a_script_that_throws(page: object) -> None:
    """The command is the page's whole purpose, so it carries no `.cut` and its copy button is wired
    before anything in `site/app.js` can throw. A visitor on a broken page still has the line."""
    page.add_init_script("window.addEventListener('DOMContentLoaded', () => { null.boom; });")  # type: ignore[attr-defined]
    page.goto(f"{page.origin}/index.html", wait_until="networkidle")  # type: ignore[attr-defined]
    page.wait_for_timeout(600)  # type: ignore[attr-defined]
    seen = read_command(page)
    assert seen["opacity"] > 0, seen
    assert seen["command"] == COMMAND, seen
    assert seen["buttons"], f"the copy buttons went down with the script: {seen}"


def test_the_install_command_is_readable_and_selectable_with_no_script(origin: str) -> None:
    """No script at all: no copy button, because the button is made by one — and a command that can
    still be selected by hand into a line that runs. The `$` is written but never selected."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        if not Path(pw.chromium.executable_path).exists():
            pytest.skip("Chromium is missing: run `decktalk install` first")
        browser = pw.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 900}, java_script_enabled=False)
        pg.goto(f"{origin}/index.html", wait_until="load")
        seen = read_command(pg)
        browser.close()
    assert seen["opacity"] > 0, seen
    assert seen["command"] == COMMAND, seen
    assert seen["selected"] == COMMAND, f"a hand-selection does not yield a line that runs: {seen}"
    assert seen["buttons"] == 0, f"a button that cannot copy is on a page with no script: {seen}"


def test_every_command_the_page_shows_can_be_copied(page: object) -> None:
    """One component, every command: the hero's line and each of the four steps get a copy button.

    Counted by the copy slot rather than by `.pill`, because the film's own slides use that class
    for the two "20 min" labels on the map and they are not commands.
    """
    page.goto(f"{page.origin}/index.html", wait_until="networkidle")  # type: ignore[attr-defined]
    slots = page.locator(".pill .copy")  # type: ignore[attr-defined]
    assert slots.count() == 5, "the hero and the four steps"
    assert page.locator(".pill .copy button").count() == slots.count()  # type: ignore[attr-defined]
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])  # type: ignore[attr-defined]
    page.locator("#install .pill button").first.click()  # type: ignore[attr-defined]
    page.wait_for_timeout(200)  # type: ignore[attr-defined]
    assert page.evaluate("() => navigator.clipboard.readText()") == COMMAND  # type: ignore[attr-defined]


def test_a_reader_can_walk_from_the_landing_page_to_the_explanation_and_back(page: object) -> None:
    """The explanation moved, so the landing page has to carry a reader to it and the how page has
    to carry them back to the install command. Both routes are followed rather than read."""
    page.goto(f"{page.origin}/index.html", wait_until="domcontentloaded")  # type: ignore[attr-defined]
    page.click("#how-more a[href='/how']")  # type: ignore[attr-defined]
    page.wait_for_url("**/how")  # type: ignore[attr-defined]
    assert page.locator("#how").count() == 1  # type: ignore[attr-defined]
    page.click(".nav a[href='/#install']")  # type: ignore[attr-defined]
    page.wait_for_url("**/#install")  # type: ignore[attr-defined]
    assert page.locator("#install").count() == 1  # type: ignore[attr-defined]
