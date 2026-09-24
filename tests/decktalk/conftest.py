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
from typing import Any

import pytest

from decktalk.artifacts import Word
from decktalk.media import browser, ffmpeg
from decktalk.speech import PROVIDERS, SpeechRequest

FAKE_VOICE_NAME = "test-voice"
"""The `[voice] provider` value a project under test names, which `fake_voice` answers for."""


@dataclass
class FakeFfmpeg:
    """Every ffmpeg call a stage made, in order, with the answers it was given."""

    calls: list[list[str]] = field(default_factory=list)
    stderr_text: str = ""
    duration_seconds: float = 1.0
    decoded_seconds: float = 1.0
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

    monkeypatch.setattr(ffmpeg, "run", run)
    monkeypatch.setattr(ffmpeg, "stderr", stderr)
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: fake.duration_seconds)
    monkeypatch.setattr(ffmpeg, "decoded_duration", lambda _path, **_kw: fake.decoded_seconds)
    monkeypatch.setattr(ffmpeg, "has_audio", lambda _path: fake.sounds)
    monkeypatch.setattr(ffmpeg, "ffmpeg_paths", lambda: ("ffmpeg", "ffprobe"))
    return fake


class FakePage:
    """A Chromium page that answers every call and remembers what was asked of it."""

    def __init__(self, answer: Callable[[str], Any] | None = None) -> None:
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.screenshots: list[Path] = []
        self._answer = answer or (lambda _script: None)

    def goto(self, url: str, **_kwargs: Any) -> None:
        self.urls.append(url)

    def evaluate(self, script: str, *_args: Any) -> Any:
        self.scripts.append(script)
        return self._answer(script)

    def wait_for_function(self, script: str, **_kwargs: Any) -> None:
        self.scripts.append(script)

    def screenshot(self, *, path: str | Path, **_kwargs: Any) -> None:
        self.screenshots.append(Path(path))
        Path(path).write_bytes(b"")

    def close(self) -> None:
        """A page a test opened is closed the way a real one is, and closing it does nothing here."""


@pytest.fixture
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> FakePage:
    """`decktalk.media.browser.chromium` yielding a page that runs nothing, so no browser is launched."""
    page = FakePage()

    class Context:
        def new_page(self) -> FakePage:
            return page

        def close(self) -> None:
            """Closing the context is what a recorder does, and there is nothing behind it here."""

    class Browser:
        def new_context(self, **_kwargs: Any) -> Context:
            return Context()

        def close(self) -> None:
            """Closing the browser is what a recorder does, and there is nothing behind it here."""

    @contextmanager
    def chromium(_browser_path: str = "") -> Iterator[Browser]:
        yield Browser()

    monkeypatch.setattr(browser, "chromium", chromium)
    monkeypatch.setattr(browser, "instrument", lambda page: page)
    return page


@dataclass
class FakeVoice:
    """The requests a provider was sent, and the audio and the words it answered with."""

    name: str = FAKE_VOICE_NAME
    requests: list[SpeechRequest] = field(default_factory=list)
    audio: bytes = b"take"
    words: list[Word] = field(default_factory=lambda: [Word("hello", 0.0, 0.5), Word("there", 0.5, 1.0)])

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
