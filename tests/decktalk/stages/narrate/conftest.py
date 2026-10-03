"""A small project, a run that records its own stream, and the shapes the narrate tests share.

Every test here runs the real stage. What it must not run is a paid voice or ffmpeg, and both are
faked at the seam the stage imports, by `fake_voice` and `fake_ffmpeg` in the suite's own conftest.
The run is a real `Run` on a machine with nothing on it but an event stream, because a stage reports
through the run and a test that replaced the run would measure a fake instead of the stage.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from decktalk.artifacts import Take
from decktalk.inputs import Inputs
from decktalk.media import audio
from support.projects import load_project
from support.takes import a_take

VOICE_ID = "voice-under-test"
"""The voice every project here is read in, which is one of the inputs a take's digest is over."""

ENVIRON = {"ELEVENLABS_API_KEY": "key-under-test", "DECKTALK_VOICE_ID": VOICE_ID}
"""What a machine hands a project, which is the credential and the published voice name."""

TOML = """
[project]
name = "t"

[voice]
provider = "elevenlabs"

[elevenlabs]
price_per_1000_characters = 0.30

[narration]
lead_seconds = 0.5
tail_min_seconds = 0.7

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"

[[section]]
number = 3
page = "deck/index.html"
scene = "3"
"""

SCRIPT = """# Notes

Anything up here is never spoken.

## 1. Open

A bowl. [beat] A ball.

## 2. Middle

It steps down the bowl.

## 3. Close

Every picture waited for its word.
"""


@pytest.fixture
def make_inputs(tmp_path: Path) -> Callable[..., Inputs]:
    """Write a project into its own directory and load it, so a rewrite reloads the same root."""

    def build(*, toml: str = TOML, script: str | None = SCRIPT, name: str = "proj") -> Inputs:
        return load_project(tmp_path / name, toml, script=script, environ=ENVIRON)

    return build


@pytest.fixture(autouse=True)
def quiet_sound_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where a take's sound ends is read from real bytes, which no take written here has.

    The fake encoder writes an empty file, so the scan is answered at the seam the stage reads it
    through, and every placement test measures the arithmetic rather than ffmpeg.
    """
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)


@pytest.fixture
def inputs(make_inputs: Callable[..., Inputs]) -> Inputs:
    return make_inputs()


@pytest.fixture
def run_environ() -> dict[str, str]:
    """The credential and the voice name, which every narrate run finds on its machine."""
    return ENVIRON


def a_paid_take(section: int = 1, *, digest: str = "0000000000000abc", seconds: float = 1.0) -> Take:
    """A take index row a provider was paid for, whose voice nobody on this machine can name."""
    return a_take(
        section,
        seconds=seconds,
        chapter="Open",
        hash=digest,
        characters=8,
        estimated_seconds=1.0,
        speech_end_seconds=None,
        sound_end_seconds=None,
        spoken="A bowl.",
    )
