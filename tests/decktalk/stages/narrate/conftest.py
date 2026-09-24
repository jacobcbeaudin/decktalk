"""A small project, a run that records its own stream, and the shapes the narrate tests share.

Every test here runs the real stage. What it must not run is a paid voice or ffmpeg, and both are
faked at the seam the stage imports, by `fake_voice` and `fake_ffmpeg` in the suite's own conftest.
The run is a real `Run` on a machine with nothing on it but an event stream, because a stage reports
through the run and a test that replaced the run would measure a fake instead of the stage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from decktalk.errors import Cancel
from decktalk.events import Event
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.results import Voicing

VOICE_ID = "voice-under-test"
"""The voice every project here is read in, which is one of the inputs a take's digest is over."""

ENVIRON = {"ELEVENLABS_API_KEY": "key-under-test", "ELEVENLABS_VOICE_ID": VOICE_ID}
"""What a machine hands a project, which is the credential and the published voice name."""

TOML = """
[project]
name = "t"

[voice]
provider = "test-voice"
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


@dataclass
class Watched:
    """One run and every line it put on the stream, which is how a test reads what a stage reported."""

    run: Run
    lines: list[Event] = field(default_factory=list)

    def of(self, event: str) -> list[Event]:
        """Every line of one kind, in the order the stage emitted them."""
        return [line for line in self.lines if line.event == event]


@pytest.fixture
def make_inputs(tmp_path: Path) -> Callable[..., Inputs]:
    """Write a project into its own directory and load it, so a rewrite reloads the same root."""

    def build(*, toml: str = TOML, script: str | None = SCRIPT, name: str = "proj") -> Inputs:
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "decktalk.toml").write_text(toml, encoding="utf-8")
        if script is not None:
            (root / "script.md").write_text(script, encoding="utf-8")
        return Inputs.load(root, environ=ENVIRON)

    return build


@pytest.fixture
def inputs(make_inputs: Callable[..., Inputs]) -> Inputs:
    return make_inputs()


@pytest.fixture
def make_run(tmp_path: Path) -> Callable[..., Watched]:
    """A run on a machine that holds nothing but a stream, with every line it emits kept."""

    def build(project: Inputs, *, voice: Voicing = Voicing.PLACEHOLDER, max_cost: float | None = None) -> Watched:
        machine = Machine(
            environ=ENVIRON,
            tables={},
            config_path=tmp_path / "decktalk-machine.toml",
            cwd=project.root,
            toolchain=Toolchain(),
        )
        watched = Watched(
            run=Run(
                machine,
                id="run-under-test",
                cancel=Cancel(),
                voice=voice,
                max_cost=max_cost,
                root=project.root,
            )
        )
        machine.events.subscribe(watched.lines.append)
        return watched

    return build


@pytest.fixture
def watched(inputs: Inputs, make_run: Callable[..., Watched]) -> Watched:
    return make_run(inputs)
