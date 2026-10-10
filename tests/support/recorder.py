"""A fake Chromium a page is driven in, and the pages a real one is pointed at, shared by the media tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from playwright.sync_api import Browser, BrowserContext, Page

from decktalk.media import pages
from decktalk.media.browser import TRUSTED, Chromium, PagePolicy
from decktalk.media.origin import Allowed

REPORTED = {
    "version": "0.5.0",
    "mode": "record",
    "scene": "intro",
    "slide": "1.1",
    "warnings": [{"code": "PAGE_CUE_UNKNOWN", "message": "no such cue", "cue": "1.1:nope"}],
    "catalog": [{"scene": "intro"}],
    "cues": [],
    "words": [],
}
"""What the page answers `window.__decktalkProbe.report()` with, in the shape the contract names."""


@dataclass
class Said:
    """One line a page wrote to its console, as Playwright hands it to a `console` handler."""

    type: str
    text: str


class FakePage:
    """A page that answers the four calls the recorder makes and remembers what it was asked."""

    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.closed = False
        self.html = ""

    def page(self) -> Page:
        """This page as the Playwright page it stands in for."""
        return cast("Page", self)

    def on(self, event: str, handler: Callable[[Said], object]) -> None:
        self.context.handlers.append((event, handler))

    def goto(self, url: str, **_kwargs: object) -> None:
        self.urls.append(url)
        for event, handler in self.context.handlers:
            if event == "console":
                for line in self.context.said:
                    handler(line)

    def evaluate(self, script: str, /) -> object:
        self.scripts.append(script)
        if pages.REPORT_JS in script:
            return dict(REPORTED)
        return True if pages.HAS_CATALOG_JS in script else None

    def set_content(self, html: str) -> None:
        self.html = html

    def screenshot(self, *, path: str, **_options: object) -> None:
        Path(path).write_bytes(b"png")

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, directory: Path, said: tuple[Said, ...] = ()) -> None:
        self.directory = directory
        self.said = said  # what every page of this context writes to its console once it has loaded
        self.scripts: list[str] = []
        self.routes: list[str] = []
        self.handlers: list[tuple[str, Callable[[Said], object]]] = []
        self.page: FakePage | None = None
        self.closed = False

    def context(self) -> BrowserContext:
        """This context as the Playwright context it stands in for."""
        return cast("BrowserContext", self)

    def add_init_script(self, script: str) -> None:
        self.scripts.append(script)

    def route(self, pattern: str, _handler: object) -> None:
        self.routes.append(pattern)

    def new_page(self) -> FakePage:
        self.page = FakePage(self)
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, said: tuple[Said, ...] = ()) -> None:
        self.said = said
        self.contexts: list[FakeContext] = []
        self.asked: list[dict[str, object]] = []  # what each context was opened with

    def opened(self, policy: PagePolicy = TRUSTED) -> Chromium:
        """This browser as a Chromium launched under `policy`, which is what `chromium()` yields."""
        return Chromium(cast("Browser", self), policy)

    def new_context(self, **kwargs: object) -> FakeContext:
        self.asked.append(kwargs)
        context = FakeContext(Path("."), self.said)
        self.contexts.append(context)
        return context

    def new_page(self, **_kwargs: object) -> FakePage:
        return FakePage(FakeContext(Path(".")))


def deck_of(tmp_path: Path, pages: dict[str, str]) -> Allowed:
    """A deck directory holding each named page, and the rule that serves it and nothing else."""
    deck = tmp_path / "deck"
    deck.mkdir(exist_ok=True)
    for name, body in pages.items():
        deck.joinpath(name).write_text(body, encoding="utf-8")
    return Allowed.of(tmp_path, ["deck"])


THROWS = "<!doctype html><meta charset=utf-8><title>t</title>\n<script>\nnotDefinedAnywhere();\n</script>"


BARE = "<!doctype html><meta charset=utf-8><title>t</title><p>no runtime here</p>"
