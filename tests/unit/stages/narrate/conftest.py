"""A small voiced project and a provider that never sends anything, shared by the narrate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.model import Project
from decktalk.speech import SpeechRequest, register_speech_provider

TOML = """
[project]
name = "t"

[voice]
provider = "test-voice"
price_per_1000_characters = 0.30

[narration]
opening_silence_seconds = 0.7

[[section]]
number = 1
page = "deck/index.html"

[[section]]
number = 2
page = "deck/index.html"

[[section]]
number = 3
page = "deck/index.html"
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


class ScriptedVoice:
    """A provider that reads back the text it is given, with one fixed cache key.

    A digest therefore depends on the text alone, which is what the identity tests are about, and
    every word lands at a time this file chose rather than at one a service returned.
    """

    name = "test-voice"

    def __init__(self) -> None:
        self.sent: list[SpeechRequest] = []

    def speak(self, request: SpeechRequest) -> tuple[bytes, list]:
        from decktalk.artifacts import Word

        self.sent.append(request)
        tokens = request.text.split()
        words = [Word(t.strip(".,"), round(i * 0.4, 3), round(i * 0.4 + 0.3, 3)) for i, t in enumerate(tokens)]
        return b"mp3 " + request.text.encode(), words

    def cache_key(self, request: SpeechRequest) -> str:
        return "test-voice"


@pytest.fixture
def voice() -> ScriptedVoice:
    """The provider `[voice] provider = "test-voice"` builds, registered for the whole run."""
    provider = ScriptedVoice()
    register_speech_provider("test-voice", lambda context: provider)
    return provider


@pytest.fixture
def base() -> tuple[str, str]:
    """The three-section project every test here starts from: its decktalk.toml and its script.md."""
    return TOML, SCRIPT


@pytest.fixture
def make_project(tmp_path: Path):
    """Write a project into one directory and load it, so a rewrite reloads the same root."""

    def build(*, toml: str = TOML, script: str = SCRIPT, name: str = "proj") -> Project:
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "decktalk.toml").write_text(toml, encoding="utf-8")
        (root / "script.md").write_text(script, encoding="utf-8")
        return Project.load(root, environ={})

    return build


@pytest.fixture
def project(make_project) -> Project:
    return make_project()
