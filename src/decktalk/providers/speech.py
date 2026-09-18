"""The speech-provider boundary: anything that can read text aloud and say when each word was spoken.

DeckTalk needs exactly one thing from a voice: audio plus a start and end time for every
word, because the cut is made on words. Today ElevenLabs is the only implementation. A
local speech provider with word timings plugs in by implementing `SpeechProvider` and
registering a name.

    [voice]
    provider = "elevenlabs"   # default; the name a provider registered under
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..artifacts import Word
from ..errors import ConfigError
from ..project import Project


@dataclass(frozen=True)
class SpeechRequest:
    """One section of narration."""

    text: str  # prose with <break time="0.7s" /> tags where pauses belong
    model: str
    voice_settings: dict[str, Any]
    output_format: str
    previous_text: str | None = None  # neighbouring sections, for prosody continuity
    next_text: str | None = None


class SpeechProvider(Protocol):
    """Reads one section and returns the audio and a time for every word."""

    name: str

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]: ...

    def cache_key(self, request: SpeechRequest) -> str:
        """Everything that changes the audio besides the text: voice, model, settings."""
        ...


ProviderFactory = Callable[[Project], SpeechProvider]
_REGISTRY: dict[str, ProviderFactory] = {}


def register_speech_provider(name: str, factory: ProviderFactory) -> None:
    """Make a provider selectable as `[voice] provider = "<name>"`.

    The factory receives the loaded Project, so it can read the project's env and
    voice settings, and returns a SpeechProvider. Registering a name again replaces
    the earlier factory.
    """
    _REGISTRY[name] = factory


def get_provider(project: Project) -> SpeechProvider:
    name = project.voice.provider
    factory = _REGISTRY.get(name)
    if factory is None:
        raise ConfigError(f"[voice] provider = {name!r} is not registered; known: {sorted(_REGISTRY)}")
    return factory(project)
