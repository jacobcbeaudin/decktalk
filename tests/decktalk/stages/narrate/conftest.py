"""A small project, a run that records its own stream, and the shapes the narrate tests share.

Every test here runs the real stage. What it must not run is a provider that bills or ffmpeg, so
`fake_voice` registers a voice in the table the test's runs are opened with and `fake_ffmpeg`
replaces the seam the stage imports, both in the suite's own conftest.
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
from support.fakes import FREE_VOICE_NAME
from support.projects import load_project
from support.takes import a_take

VOICE_ID = "voice-under-test"
"""The voice every project here is read in, which is one of the inputs a take's digest is over."""

ENVIRON = {"ELEVENLABS_API_KEY": "key-under-test", "DECKTALK_VOICES_NARRATOR_ID": VOICE_ID}
"""What a machine hands a project, which is the credential and the published voice name."""

TOML = """
[project]
name = "t"

[voices.narrator]
provider = "elevenlabs"

[elevenlabs]
dollars_per_1000_characters = 0.30

[narration]
lead_seconds = 0.5
tail_seconds = 0.7

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

FREE_TOML = TOML.replace('provider = "elevenlabs"', f'provider = "{FREE_VOICE_NAME}"')
"""The same project read by the voice DeckTalk ships that bills nothing."""

SCRIPT = """# Notes

Anything up here is never spoken.

## 1. Open

A bowl. [beat] A ball.

## 2. Middle

It steps down the bowl.

## 3. Close

Every picture waited for its word.
"""


NARRATOR_TABLE = '[voices.narrator]\nprovider = "elevenlabs"\n'
"""The narrator's table every project here writes, which a second voice's table is added after."""

WITH_A_GUEST = TOML.replace(NARRATOR_TABLE, f'{NARRATOR_TABLE}\n[voices.guest]\nprovider = "dtsp"\nid = "af_heart"\n')
"""The project with a free guest beside its paid narrator."""

DIALOGUE = SCRIPT.replace(
    "## 2. Middle\n\nIt steps down the bowl.\n",
    "## 2. Middle\n\n[voice narrator]\n\nIt steps down the bowl.\n\n[pause 1]\n\n[voice guest]\n\nAnd it rolls.\n\n"
    "[voice narrator]\n\nThen it stops.\n",
)
"""The script whose second section is a dialogue: the narrator, the guest after a one-second pause, the narrator."""


@pytest.fixture
def make_inputs(tmp_path: Path) -> Callable[..., Inputs]:
    """Write a project into its own directory and load it, so a rewrite reloads the same root."""

    def build(*, toml: str = TOML, script: str | None = SCRIPT, name: str = "proj") -> Inputs:
        return load_project(tmp_path / name, toml, script=script, environ=ENVIRON)

    return build


SOUND_END = 0.8
"""Where every take written here sounds till, as `quiet_sound_end` answers for its bytes."""


@pytest.fixture(autouse=True)
def quiet_sound_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where a take's sound ends is read from real bytes, which no take written here has.

    The fake encoder writes an empty file, so the scan is answered at the seam the stage reads it
    through, and every placement test measures the arithmetic rather than ffmpeg.
    """
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: SOUND_END)


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
        digest=digest,
        characters=8,
        estimated_seconds=1.0,
        speech_end_seconds=None,
        sound_end_seconds=None,
        spoken="A bowl.",
    )
