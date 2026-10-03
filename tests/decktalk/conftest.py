"""The fixtures that install the fakes in `support.fakes` at the seam a stage imports.

Each fixture replaces one module attribute through `monkeypatch`, or, for a voice, registers the fake in
the table the test's runs are opened with, so the real tool is back when the test ends, and hands back
the fake so the test reads what the stage asked of it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from decktalk.media import ffmpeg
from decktalk.speech import SpeechFactory
from decktalk.speech import http as speech_http
from support.fakes import FAKE_VOICE_NAME, FakeFfmpeg, FakeVoice, refusing_voices
from support.service import Service


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


@pytest.fixture
def voices() -> dict[str, SpeechFactory]:
    """The voice table this test's runs answer with: each shipped voice refusing to be built till a fake replaces it."""
    return refusing_voices()


@pytest.fixture
def fake_voice(voices: dict[str, SpeechFactory]) -> FakeVoice:
    """A fake registered under the shipped voice's name on the machine every run of this test is opened on."""
    voice = FakeVoice()
    voices[FAKE_VOICE_NAME] = lambda _context: voice
    return voice


@pytest.fixture
def waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every wait a retry asked for, with none of them slept, so the suite does not wait on a back-off."""
    asked: list[float] = []
    monkeypatch.setattr(speech_http, "pause", asked.append)
    return asked


@pytest.fixture
def service() -> Iterator[Service]:
    """A threaded local service, started for the test and stopped with every stalled reply let go."""
    held = Service()
    held.start()
    try:
        yield held
    finally:
        held.released.set()
        held.clear()
        held.stop()
