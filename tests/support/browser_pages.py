"""Pages written by a test, and the Chromium that opens them, shared by the two browser modules.

`tests/contract/test_runtime.py` opens these pages the way a person does, with decktalk-runtime.js and
nothing else. `tests/contract/test_probe.py` opens them the way a DeckTalk command does, with
decktalk-probe.js injected first. The pages themselves are the same either way, which is the
property the split is supposed to have.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from decktalk.toolchain.assets import katex_dir, runtime_path
from support.tools import absent

if TYPE_CHECKING:
    from playwright.sync_api import Page

RUNTIME = f'<script src="{runtime_path().resolve().as_uri()}"></script>'
KATEX = (
    f'<link rel="stylesheet" href="{(katex_dir() / "katex.min.css").resolve().as_uri()}">'
    f'<script src="{(katex_dir() / "katex.min.js").resolve().as_uri()}"></script>'
)


@dataclass
class Tab:
    """One Chromium page, and everything it threw since a test last cleared `errors`."""

    page: Page
    errors: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, page: Page) -> Tab:
        """A tab that listens on `page` for what it throws."""
        tab = cls(page)
        page.on("pageerror", lambda e: tab.errors.append(str(e)))
        return tab


def chromium_tab(instrument: Callable[[Page], object] | None = None) -> Iterator[Tab]:
    """One Chromium page for a module, with an `errors` list of everything it threw.

    `instrument` is the hook a DeckTalk command uses to add decktalk-probe.js, and a module that
    passes none gets the page a person opens.

    Only a test that carries the `browser` marker reaches this, and the marker is only collected when
    a run names it, so a Chromium that will not launch fails the test rather than skipping it.
    """
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except PlaywrightError as exc:
            pytest.fail(absent("chromium", str(exc).splitlines()[0]))
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        if instrument is not None:
            instrument(page)
        yield Tab.of(page)
        browser.close()


def write_page(tmp_path: Path, name: str, body: str, *, head: str = "") -> str:
    """A page with the runtime in its head and `body` in its body, returned as a file URL."""
    html = tmp_path / name
    html.write_text(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{name}</title>'
        f"{head}{RUNTIME}</head><body>{body}</body></html>",
        encoding="utf-8",
    )
    return html.resolve().as_uri()


def script_page(tmp_path: Path, name: str, script: str, *, head: str = "") -> str:
    """A page whose scenes come from one inline script."""
    return write_page(tmp_path, name, f"<script>{script}</script>", head=head)


def opened(page: Page, url: str) -> None:
    """Open `url` and wait until the runtime has read the page, which is before any cue plays."""
    page.goto(url)
    page.evaluate("() => window.__decktalk.ready")


def settled(page: Page, url: str) -> None:
    """Open `url` and wait until the page has drawn everything the query asked it for."""
    page.goto(url)
    page.wait_for_function("() => document.body.dataset.done === '1'")
