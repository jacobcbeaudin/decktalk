"""The speech boundary, which is anything that reads text aloud and says when each word was spoken.

DeckTalk needs exactly one thing from a voice, which is audio plus a start and an end time for
every word, because the cut is made on words. ElevenLabs is the only implementation today. A local speech
provider with word timings plugs in by implementing `SpeechProvider` and taking a name in the
registry below.

    [voice]
    provider = "elevenlabs"   # the default, which is the name a provider is registered under

A provider is built from a `VoiceContext` and never from a project, so this layer knows nothing
about `decktalk.toml`, the build directory or the stages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..artifacts import Word
from ..errors import ConfigError
from ..secret import Secret
from ..settings import Settings


@dataclass(frozen=True)
class SpeechRequest:
    """One section of narration."""

    text: str  # prose with <break time="0.7s" /> tags where pauses belong
    model: str
    voice_settings: dict[str, Any]
    output_format: str
    previous_text: str | None = None  # neighbouring sections, for prosody continuity
    next_text: str | None = None


class Secrets(Protocol):
    """Where a provider asks for its credentials. `model.env.Env` is the one implementation."""

    def require(self, *names: str) -> list[Secret]:
        """The values of these variables, or a ConfigError naming every one that is not set."""
        ...


@dataclass(frozen=True)
class VoiceContext:
    """Everything a provider needs from a project, with no project in it.

    `settings` is the tuning, including the provider's own table, and `secrets` answers for the
    values in `.env`. No value read through `secrets` is ever printed, logged or put in a payload.
    """

    settings: Settings
    secrets: Secrets


class SpeechProvider(Protocol):
    """Reads one section and returns the audio and a time for every word."""

    name: str

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]: ...

    def cache_key(self, request: SpeechRequest) -> str:
        """Everything that changes the audio besides the text: voice, model, settings."""
        ...


ProviderFactory = Callable[[VoiceContext], SpeechProvider]


def _elevenlabs(context: VoiceContext) -> SpeechProvider:
    from .elevenlabs import ElevenLabs

    return ElevenLabs.for_context(context)


# The providers a `[voice] provider` value may name, written out rather than filled by an import
# for its side effect, so the whole list is readable here.
PROVIDERS: dict[str, ProviderFactory] = {"elevenlabs": _elevenlabs}


def register_speech_provider(name: str, factory: ProviderFactory) -> None:
    """Make a provider selectable as `[voice] provider = "<name>"`.

    The factory receives a VoiceContext and returns a SpeechProvider. Registering a name again
    replaces the earlier factory.
    """
    PROVIDERS[name] = factory


def get_provider(name: str, context: VoiceContext) -> SpeechProvider:
    """The provider registered under `name`, built for this context."""
    factory = PROVIDERS.get(name)
    if factory is None:
        raise ConfigError(
            f"[voice] provider = {name!r} is not registered. The known providers are {sorted(PROVIDERS)}."
        )
    return factory(context)


__all__ = [
    "PROVIDERS",
    "ProviderFactory",
    "Secrets",
    "SpeechProvider",
    "SpeechRequest",
    "VoiceContext",
    "get_provider",
    "register_speech_provider",
]
