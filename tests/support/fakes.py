"""The tools a stage reaches for, faked at the seam the stage imports.

A stage test runs the real stage. What it must not run is ffmpeg, Chromium and a paid voice, so
ffmpeg and the voice are replaced at the one module attribute the stage reads, each fixture handing
back the record of what the stage asked for, and `FakePage` is the page a browser seam hands a stage.
`FakeChromium` stands one level lower, as the Playwright a launch is handed.
Faking the seam rather than the stage is what keeps these tests about the stage: an argument list, a
page call or a speech request that changes shape shows up here rather than passing unread.

The fixtures that install a fake are in `tests/decktalk/conftest.py`, and a test imports a class from
here to type the fixture it asked for or to build one of its own.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from playwright.sync_api import Browser, Page, Playwright
from playwright.sync_api import Error as PlaywrightError

from decktalk.media import browser
from decktalk.results import Word
from decktalk.speech import SpeechRequest

BROWSERS_VARIABLE = "PLAYWRIGHT_BROWSERS_PATH"
"""The variable Playwright's driver reads for where its browsers live, spelled here as Playwright documents it."""

FAKE_VOICE_NAME = "test-voice"
"""The `[voice] provider` value a project under test names, which `fake_voice` answers for."""


@dataclass
class FakeFfmpeg:
    """Every ffmpeg call a stage made, in order, with the answers it was given."""

    calls: list[list[str]] = field(default_factory=list)
    stderr_text: str = ""
    raw_bytes: bytes = b""
    duration_seconds: float = 1.0
    sounds: bool = True

    def wrote(self, suffix: str) -> list[Path]:
        """Every output path with this suffix, the output being the last argument of an ffmpeg call."""
        return [Path(call[-1]) for call in self.calls if call[-1].endswith(suffix)]


NOTHING_REPORTED: dict[str, object] = {
    "version": "0.5.0",
    "mode": "cue",
    "scene": None,
    "slide": None,
    "warnings": [],
    "catalog": [],
    "cues": [],
    "words": [],
    "frameGaps": [],
    "longFrames": [],
}
"""What a page with the runtime on it and nothing to say answers `report()` with."""


class FakePage:
    """A Chromium page that answers every call and remembers what was asked of it."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.report: dict[str, object] = dict(NOTHING_REPORTED)

    def page(self) -> Page:
        """This page as the Playwright page it stands in for."""
        return cast("Page", self)

    def on(self, event: str, handler: object) -> None:
        """A recorder listens for the page's own exceptions, and this page throws none."""

    def goto(self, url: str, **_kwargs: object) -> None:
        self.urls.append(url)

    def evaluate(self, script: str, *_args: object) -> object:
        self.scripts.append(script)
        if browser.REPORT_JS in script:
            return dict(self.report)
        if browser.HAS_CATALOG_JS in script:
            return True
        return None

    def wait_for_function(self, script: str, **_kwargs: object) -> None:
        self.scripts.append(script)

    def wait_for_timeout(self, _ms: float) -> None:
        """A recorder waits in real time and a test does not, so this passes the time by not spending it."""

    def screenshot(self, *, path: str | Path, **_kwargs: object) -> None:
        Path(path).write_bytes(b"")

    def close(self) -> None:
        """A page a test opened is closed the way a real one is, and closing it does nothing here."""


@dataclass
class FakeVoice:
    """The requests a provider was sent, and the audio and the words it answered with."""

    name: str = FAKE_VOICE_NAME
    requests: list[SpeechRequest] = field(default_factory=list)
    audio: bytes = b"take"
    words: list[Word] = field(
        default_factory=lambda: [Word(word="hello", start=0.0, end=0.5), Word(word="there", start=0.5, end=1.0)]
    )

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        self.requests.append(request)
        return self.audio, list(self.words)

    def cache_key(self, request: SpeechRequest) -> str:  # noqa: ARG002  (the protocol names it)
        return self.name


class BareBrowser:
    """A launched Chromium, which opens pages that carry nothing and closes without a sound."""

    version = "0.0.0.0"

    def __init__(self) -> None:
        self.closed = False

    def browser(self) -> Browser:
        """This browser as the Playwright browser it stands in for."""
        return cast("Browser", self)

    def new_page(self, **_kwargs: object) -> object:
        return object()

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    """`pw.chromium` as a launch uses it: an executable path, and a launch that needs the file there.

    Each launch's options are kept in `asked`. A launch works when the file it would run is on disk,
    which is what a fetch puts there. `refusal` is the other way a launch fails: the browser is on disk
    and still will not start, for the first `refusals` launches or, when that is None, for every one.
    """

    def __init__(self, executable: Path, *, refusal: str = "", refusals: int | None = None) -> None:
        self.executable_path = str(executable)
        self.refusal, self.refusals = refusal, refusals
        self.asked: list[dict[str, object]] = []
        self.looked_in: list[str | None] = []

    def launch(self, **options: object) -> BareBrowser:
        self.asked.append(options)
        target = Path(str(options.get("executable_path") or self.executable_path))
        if not target.is_file():
            raise PlaywrightError(f"Executable doesn't exist at {target}\nPlaywright was just installed")
        if self.refusal and (self.refusals is None or len(self.asked) <= self.refusals):
            raise PlaywrightError(self.refusal)
        return BareBrowser()

    def driver(self) -> Playwright:
        """This Chromium inside the Playwright a launch is handed, typed as the one it stands in for."""
        return cast("Playwright", SimpleNamespace(chromium=self))

    def started(self) -> Callable[[], AbstractContextManager[Playwright]]:
        """`sync_playwright` as a seam that starts this Chromium's driver, for code that opens its own.

        Each start keeps, in `looked_in`, the browser directory a real driver would read from the
        environment it copies as it starts.
        """

        def start() -> AbstractContextManager[Playwright]:
            self.looked_in.append(os.environ.get(BROWSERS_VARIABLE))
            return nullcontext(self.driver())

        return start


class FakeRoute:
    """Playwright's route object, as far as a route handler uses it: the answer, or what it did instead."""

    def __init__(self) -> None:
        self.answer: dict[str, object] | None = None
        self.continued = False
        self.aborted: str | None = None

    def fulfill(self, **kwargs: object) -> None:
        self.answer = kwargs

    def continue_(self) -> None:
        self.continued = True

    def abort(self, error_code: str) -> None:
        self.aborted = error_code


class FakeRouter:
    """A Playwright page or context, as far as `route_pages` uses it: it keeps the handler it is given."""

    def __init__(self) -> None:
        self.handler: Callable[[FakeRoute, object], None] | None = None

    def route(self, _pattern: str, handler: Callable[[FakeRoute, object], None]) -> None:
        self.handler = handler

    def request(self, url: str) -> FakeRoute:
        """What the kept handler does with one request for `url`."""
        assert self.handler is not None, "nothing was routed through this page"
        route = FakeRoute()
        self.handler(route, SimpleNamespace(url=url))
        return route

    def page(self) -> Page:
        """This router as the page `route_pages` is handed, typed as the one it stands in for."""
        return cast("Page", self)
