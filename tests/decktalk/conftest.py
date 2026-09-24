"""The three tools a stage reaches for, faked at the seam the stage imports.

A stage test runs the real stage. What it must not run is ffmpeg, Chromium and a paid voice, so each
of those is replaced at the one module attribute the stage reads, and each fixture hands back the
record of what the stage asked for. Faking the seam rather than the stage is what keeps these tests
about the stage: an argument list, a page call or a speech request that changes shape shows up here
rather than passing unread.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from decktalk.media import browser, ffmpeg
from decktalk.results import Word
from decktalk.speech import PROVIDERS, SpeechRequest

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


@pytest.fixture
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> FakeFfmpeg:
    """`decktalk.media.ffmpeg` with no subprocess behind it, recording the argv of every call."""
    fake = FakeFfmpeg()

    def run(*args: str) -> None:
        fake.calls.append(list(args))
        out = Path(args[-1])
        if out.parent.is_dir():
            out.write_bytes(b"")

    def stderr(*args: str) -> str:
        fake.calls.append(list(args))
        return fake.stderr_text

    def raw(*args: str) -> bytes:
        fake.calls.append(list(args))
        return fake.raw_bytes

    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "stderr", stderr)
    monkeypatch.setattr(ffmpeg, "raw", raw)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: fake.duration_seconds)
    monkeypatch.setattr(ffmpeg, "has_audio", lambda _path: fake.sounds)
    return fake


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

    def __init__(self, video: Path | None = None, answer: Callable[[str], object] | None = None) -> None:
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.screenshots: list[Path] = []
        self.report: dict[str, object] = dict(NOTHING_REPORTED)
        self.video = FakeVideo(video) if video else None
        self._answer = answer or (lambda _script: None)

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
        return self._answer(script)

    def wait_for_function(self, script: str, **_kwargs: object) -> None:
        self.scripts.append(script)

    def wait_for_timeout(self, _ms: float) -> None:
        """A recorder waits in real time and a test does not, so this passes the time by not spending it."""

    def screenshot(self, *, path: str | Path, **_kwargs: object) -> None:
        self.screenshots.append(Path(path))
        Path(path).write_bytes(b"")

    def close(self) -> None:
        """A page a test opened is closed the way a real one is, and closing it does nothing here."""


@dataclass
class FakeVideo:
    """The webm Playwright writes when the recording context closes, which `Capture.place` moves."""

    path_: Path

    def path(self) -> str:
        return str(self.path_)


class FakeContext:
    """The browser context a recording opens, which routes, takes init scripts and leaves a webm.

    Playwright writes the video when the context closes, so this writes one too, and `Capture` then
    moves it exactly as it moves the real one.
    """

    def __init__(self, page: FakePage, directory: Path | None) -> None:
        self.page = page
        self.directory = directory
        self.closed = False

    def add_init_script(self, script: str) -> None:
        self.page.scripts.append(script)

    def route(self, _pattern: str, _handler: object) -> None:
        """Every request under the origin is answered by the router, and no page here makes one."""

    def new_page(self) -> FakePage:
        if self.directory is not None:
            self.page.video = FakeVideo(self.directory / "page.webm")
        return self.page

    def close(self) -> None:
        if not self.closed and self.page.video is not None:
            Path(self.page.video.path()).write_bytes(b"webm")
        self.closed = True


class FakeBrowser:
    """A launched Chromium that launches nothing, handing every caller the one page of the test."""

    def __init__(self, page: FakePage) -> None:
        self.page = page

    def new_context(self, **kwargs: object) -> FakeContext:
        recording = kwargs.get("record_video_dir")
        return FakeContext(self.page, Path(str(recording)) if recording else None)

    def new_page(self, **_kwargs: object) -> FakePage:
        return self.page

    def close(self) -> None:
        """Closing the browser is what a recorder does, and there is nothing behind it here."""


@pytest.fixture
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> FakePage:
    """`decktalk.media.browser.chromium` yielding a browser that records without launching one.

    The page is the same object whichever way a stage reaches it, so a test reads the URLs and the
    scripts off the fixture. The recording context is real enough for `record_page` to run whole,
    so the `Capture` the recorder opens behaves as the one Playwright hands it.
    """
    page = FakePage()

    @contextmanager
    def chromium(_browser_path: str = "") -> Iterator[FakeBrowser]:
        yield FakeBrowser(page)

    monkeypatch.setattr(browser, "chromium", chromium)
    monkeypatch.setattr(browser, "instrument", lambda page: page)
    return page


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

    def cache_key(self, _request: SpeechRequest) -> str:
        return self.name


@pytest.fixture
def fake_voice(monkeypatch: pytest.MonkeyPatch) -> FakeVoice:
    """A provider registered under `test-voice`, with the registry as it was when the test ends.

    `PROVIDERS` is a process-global mapping, so a test that registers a provider and leaves it there
    decides what the next test resolves. Setting the entry through `monkeypatch` is what keeps one
    test out of another.
    """
    voice = FakeVoice()
    monkeypatch.setitem(PROVIDERS, FAKE_VOICE_NAME, lambda _context: voice)
    return voice
