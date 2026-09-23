"""The homepage in a browser: what a visitor can still reach when the page's own script fails.

`tests/test_site.py` reads the committed files. This opens them. Every `.cut` on the page starts at
`opacity: 0` and is revealed by an observer on the last line of `site/app.js`, which means a script
that throws, or a `data.js` that did not load, used to leave the gallery, how it works, the edit
section and the install commands invisible — the hero and the footer, and no way to install
anywhere on the page. That is the failure this file exists to prevent.
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
# The sections a visitor needs to be able to reach, and the id each one carries.
SECTIONS = ("gallery", "how", "edit", "install")


@pytest.fixture(scope="module")
def origin() -> Iterator[str]:
    """site/ over http, because a file:// origin refuses the module and fetch rules the page relies on."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a: object, **k: object) -> None:
            super().__init__(*a, directory=str(SITE), **k)  # type: ignore[arg-type]

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
        pg.origin = origin  # type: ignore[attr-defined]
        yield pg
        browser.close()


def walk(pg: object) -> None:
    """Scroll the whole page the way a reader does, half a viewport at a time."""
    height = pg.evaluate("document.documentElement.scrollHeight")  # type: ignore[attr-defined]
    step = pg.viewport_size["height"] // 2  # type: ignore[attr-defined]
    for y in range(0, height, step):
        pg.evaluate(f"window.scrollTo(0, {y})")  # type: ignore[attr-defined]
        pg.wait_for_timeout(120)  # type: ignore[attr-defined]
    pg.wait_for_timeout(600)  # type: ignore[attr-defined]


def visible_sections(pg: object) -> dict[str, bool]:
    """Which of the page's sections a visitor can actually see, by computed opacity."""
    return pg.evaluate(  # type: ignore[attr-defined]
        """(ids) => Object.fromEntries(ids.map((id) => {
            const el = document.getElementById(id);
            if (!el) return [id, false];
            const cut = el.querySelector(".cut") ?? el;
            return [id, parseFloat(getComputedStyle(cut).opacity) > 0];
        }))""",
        list(SECTIONS),
    )


def test_every_section_is_visible_when_the_page_works(page: object) -> None:
    """The baseline, so the failure tests below cannot pass on a page that reveals nothing anyway."""
    page.goto(f"{page.origin}/index.html", wait_until="networkidle")  # type: ignore[attr-defined]
    # Walked rather than jumped: the observer reveals a section when it enters the viewport, so a
    # jump to the bottom passes over the middle ones without ever showing them.
    walk(page)
    assert all(visible_sections(page).values()), visible_sections(page)


def test_the_install_command_survives_a_data_file_that_did_not_load(page: object) -> None:
    """data.js 404s, so `window.HALFWAY` is undefined and the script returns on its eighth line."""
    page.route("**/data.js", lambda route: route.fulfill(status=404, body=""))  # type: ignore[attr-defined]
    page.goto(f"{page.origin}/index.html", wait_until="networkidle")  # type: ignore[attr-defined]
    walk(page)
    seen = visible_sections(page)
    assert seen["install"], f"a visitor could not reach the install command: {seen}"
    assert all(seen.values()), seen


def test_the_install_command_survives_a_script_that_throws(page: object) -> None:
    """The likelier failure: data.js loads but something below line eight throws, so the observer
    that reveals the page is never reached."""
    page.add_init_script("window.addEventListener('DOMContentLoaded', () => { null.boom; });")  # type: ignore[attr-defined]
    page.goto(f"{page.origin}/index.html", wait_until="networkidle")  # type: ignore[attr-defined]
    page.wait_for_timeout(600)  # type: ignore[attr-defined]
    seen = visible_sections(page)
    assert seen["install"], f"a visitor could not reach the install command: {seen}"
