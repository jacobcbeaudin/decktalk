"""A fake Chromium a recording runs in, and the pages a real one is pointed at, shared by the media tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from playwright.sync_api import Browser, BrowserContext, Page

from decktalk.media import pagereport, pages, recording
from decktalk.media.origin import Allowed, page_url
from decktalk.settings import MotionConfig

REPORTED = {
    "version": "0.5.0",
    "mode": "cue",
    "scene": "intro",
    "slide": "1.1",
    "warnings": [{"code": "PAGE_CUE_UNKNOWN", "message": "no such cue", "cue": "1.1:nope"}],
    "catalog": [{"scene": "intro"}],
    "cues": [],
    "words": [],
    "frameGaps": [{"at": 0.4, "ms": 150}],
    "longFrames": [],
}
"""What the page answers `window.__dtprobe.report()` with, in the shape the contract names."""


@dataclass
class FakeVideo:
    """The webm Playwright writes when the recording context closes, which `Capture.place` moves."""

    path_: Path

    def path(self) -> str:
        return str(self.path_)


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
        self.presented: list[float] = []  # what this machine answers the bias measurement with
        self.video = FakeVideo(context.directory / "page.webm")

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
        if "long-animation-frame" in script:
            return list(self.presented)
        return True if pages.HAS_CATALOG_JS in script else None

    def set_content(self, html: str) -> None:
        self.html = html

    def wait_for_timeout(self, _ms: float, /) -> None:
        """A recorder waits in real time and a test does not, so this passes the time by skipping it."""

    def screenshot(self, *, path: str) -> None:
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
        """Playwright writes the video when the context closes, which is the order this stands in for."""
        if not self.closed and self.page:
            Path(self.page.video.path()).write_bytes(b"webm")
        self.closed = True


class FakeBrowser:
    def __init__(self, said: tuple[Said, ...] = ()) -> None:
        self.said = said
        self.contexts: list[FakeContext] = []
        self.asked: list[dict[str, object]] = []  # what each context was opened with

    def browser(self) -> Browser:
        """This browser as the Playwright browser it stands in for."""
        return cast("Browser", self)

    def new_context(self, **kwargs: object) -> FakeContext:
        self.asked.append(kwargs)
        context = FakeContext(Path(str(kwargs["record_video_dir"])), self.said)
        self.contexts.append(context)
        return context

    def new_page(self, **_kwargs: object) -> FakePage:
        return FakePage(FakeContext(Path(".")))


class Sink:
    """A recording log, standing in for the artifact writer, which remembers when it was called."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.moments: list[tuple[str, bool]] = []  # each call, and whether the webm was in place by then
        self.written: pagereport.Recording | None = None

    def clear(self) -> None:
        self.moments.append(("clear", self.out.exists()))

    def write(self, recording: pagereport.Recording) -> None:
        self.moments.append(("write", self.out.exists()))
        self.written = recording


def record(
    tmp_path: Path, sink: Sink, *, out: Path, fake: FakeBrowser | None = None, seconds: float = 0.5, **extra: object
) -> pagereport.Recording:
    """One fake recording of the deck's page, with any option of `record_page` a test is about in `extra`."""
    options: dict[str, Any] = {
        "settle_seconds": 0.0,
        "min_cover_seconds": 0.0,
        "width": 960,
        "height": 540,
        "color_scheme": "dark",
        "motion": MotionConfig(),
        **extra,
    }
    allowed = Allowed.of(tmp_path, ["deck"])
    return recording.record_page(
        (fake or FakeBrowser()).browser(),
        page_url("deck/index.html"),
        seconds,
        out,
        allowed=allowed,
        log_sink=sink,
        **options,
    )


def deck_of(tmp_path: Path, pages: dict[str, str]) -> Allowed:
    """A deck directory holding each named page, and the rule that serves it and nothing else."""
    deck = tmp_path / "deck"
    deck.mkdir(exist_ok=True)
    for name, body in pages.items():
        deck.joinpath(name).write_text(body, encoding="utf-8")
    return Allowed.of(tmp_path, ["deck"])


THROWS = "<!doctype html><meta charset=utf-8><title>t</title>\n<script>\nnotDefinedAnywhere();\n</script>"


BARE = "<!doctype html><meta charset=utf-8><title>t</title><p>no runtime here</p>"
