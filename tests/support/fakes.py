"""The tools a stage reaches for, faked at the seam the stage imports.

A stage test runs the real stage. What it must not run is ffmpeg, Chromium and a provider that bills,
so ffmpeg is replaced at the one module attribute the stage reads and the voice is registered in the
table the test's runs are opened with, each fixture handing back the record of what the stage asked
for, and `FakePage` is the page a browser seam hands a stage.
`FakeChromium` stands one level lower, as the Playwright a launch is handed.
Faking the seam rather than the stage is what keeps these tests about the stage: an argument list, a
page call or a speech request that changes shape shows up here rather than passing unread.

The fixtures that install a fake are in `tests/decktalk/conftest.py`, and a test imports a class from
here to type the fixture it asked for or to build one of its own.
"""

from __future__ import annotations

import io
import os
import wave
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, Playwright

from decktalk.errors import ProviderError
from decktalk.media import pages
from decktalk.results import Word
from decktalk.speech import (
    ALIGNERS,
    DECLARED,
    Aligner,
    AlignerContext,
    AlignerIdentity,
    AlignmentRequest,
    Speech,
    SpeechContext,
    SpeechFactory,
    SpeechProvider,
    SpeechRequest,
)

BROWSERS_VARIABLE = "PLAYWRIGHT_BROWSERS_PATH"
"""The variable Playwright's driver reads for where its browsers live, spelled here as Playwright documents it."""

FAKE_VOICE_NAME = "elevenlabs"
"""The `[voice] provider` a project under test names, which `fake_voice` answers for in place of the shipped voice.

A fake stands in under the shipped name, so the project reads the shipped voice's own table, its
default model and its stated rate, and the fake is registered only on the machine of the test's runs.
"""

FREE_VOICE_NAME = "dtsp"
"""The voice DeckTalk ships that bills nothing, which a project under test names to be read by a free voice."""


def refusing_voices() -> dict[str, SpeechFactory]:
    """A voice table naming every voice DeckTalk ships, each refusing to be built, for a test to put its fakes in.

    A run on this table answers a fake registered under a name in place of the refusal, so a test that
    forgot its fake fails at the build and never reaches a real adapter or a voice served on this machine.
    """
    return {name: _refused(name) for name in DECLARED}


def _refused(name: str) -> SpeechFactory:
    """A factory that refuses to build the shipped voice `name`."""

    def build(_context: SpeechContext) -> SpeechProvider:
        raise AssertionError(f"the test built the shipped voice {name!r}, which it never faked")

    return build


def refusing_aligners() -> dict[str, Callable[[AlignerContext], Aligner]]:
    """An aligner table naming the aligner DeckTalk ships, refusing to be built, for a test to put its fake in.

    A run on this table that needs an aligner and was given no fake fails at the build, so no test
    ever reaches a local server by a table it left alone.
    """
    return {name: _refused_aligner(name) for name in ALIGNERS}


def _refused_aligner(name: str) -> Callable[[AlignerContext], Aligner]:
    def build(_context: AlignerContext) -> Aligner:
        raise AssertionError(f"the test built the shipped aligner {name!r}, which it never faked")

    return build


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
    "mode": "record",
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
        if pages.REPORT_JS in script:
            return dict(self.report)
        if pages.HAS_CATALOG_JS in script:
            return True
        return None

    def wait_for_function(self, script: str, **_kwargs: object) -> None:
        self.scripts.append(script)

    def wait_for_timeout(self, _ms: float) -> None:
        """A recorder waits in real time and a test does not, so this passes the time by skipping it."""

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
    words: list[Word] | None = field(
        default_factory=lambda: [Word(word="hello", start=0.0, end=0.5), Word(word="there", start=0.5, end=1.0)]
    )
    """The words every take comes with, or None for a voice that sends no word times."""
    model_digest: str | None = None

    def speak(self, request: SpeechRequest) -> Speech:
        self.requests.append(request)
        return Speech(self.audio, tuple(self.words) if self.words else None, self.model_digest)


class BareBrowser:
    """A launched Chromium, which opens pages that carry nothing and closes without a sound."""

    version = "0.0.0.0"

    def __init__(self) -> None:
        self.closed = False

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


FAKE_IDENTITY = AlignerIdentity(id="fake-aligner", revision="1", model_digest="sha256:0f00")
"""Who the fake aligner says it is, unless a test names another."""


@dataclass
class FakeAligner:
    """An aligner in this process: who it says it is, what it was asked, and the words it answers.

    It answers `words` when a test gives them, and otherwise spreads the words it was sent evenly over
    the WAV it was sent, or over `seconds` when the WAV holds no samples. `down` makes every request
    fail as a server that went away does, `fails` is the failure every request raises instead,
    `handshake` is the failure building it raises, and
    `on_align` runs before each answer, so a test can cancel or interrupt the run mid-pass.
    """

    identity: AlignerIdentity = FAKE_IDENTITY
    words: list[Word] | None = None
    seconds: float = 1.0
    requests: list[AlignmentRequest] = field(default_factory=list)
    contexts: list[AlignerContext] = field(default_factory=list)
    down: bool = False
    fails: ProviderError | None = None
    handshake: ProviderError | None = None
    on_align: Callable[[AlignmentRequest], None] | None = None

    def build(self, context: AlignerContext) -> FakeAligner:
        """This aligner as a factory builds it, failing as `handshake` says."""
        self.contexts.append(context)
        if self.handshake is not None:
            raise self.handshake
        return self

    def align(self, request: AlignmentRequest) -> list[Word]:
        self.requests.append(request)
        if self.on_align is not None:
            self.on_align(request)
        if self.down:
            raise ProviderError("could not reach the fake aligner: Connection refused", reached=False)
        if self.fails is not None:
            raise self.fails
        if self.words is not None:
            return list(self.words)
        seconds = _wav_seconds(request.wav) or self.seconds
        step = seconds / len(request.words)
        return [
            Word(word=word, start=round(i * step, 3), end=round((i + 1) * step, 3))
            for i, word in enumerate(request.words)
        ]


def _wav_seconds(wav: bytes) -> float:
    """How long a WAV plays, or zero when it is not one or holds no samples."""
    try:
        with wave.open(io.BytesIO(wav), "rb") as sound:
            return sound.getnframes() / sound.getframerate()
    except (wave.Error, EOFError):
        return 0.0
