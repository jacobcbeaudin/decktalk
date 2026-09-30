"""The speech boundary, which is anything that reads text aloud and says when each word was spoken.

DeckTalk needs exactly one thing from a voice: audio, and a start and an end time for every word,
because the cut is made on words. ElevenLabs is the only implementation and this seam is internal. A
provider type is not part of the published surface, no result carries one, and nothing outside the
library registers one. What the seam is worth keeping for is the test it buys: a stage test runs the
real narrate against a provider that spends nothing, at the one attribute the stage reads.

    [voice]
    provider = "elevenlabs"   # the name a provider is registered under

A provider is built from a `VoiceContext`, which carries values and never a project, so this layer
knows nothing about `decktalk.toml`, the build directory or the stages, and a provider is built in a
test from four numbers and a source of secrets.

The registry a name is looked up in belongs to the machine. `PROVIDERS` is the table DeckTalk ships,
and a machine a host built by hand may carry its own table instead. The machine binds its table for
the length of every run it opens, through `voicing`, so a stage that asks `get_provider` for a voice
gets the one the machine running it answers with, and two machines in one process cannot swap each
other's voice. The same binding carries the machine's decision about where its key may go, which is
stamped onto every context a provider is built from.

The voice id is a published name rather than a credential. It names which voice reads the script,
the way a model name names which model does, and it travels in the request and in the take hash.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ..errors import InputError
from ..results import Word
from ..secret import Secret

PUNCT = "\"'“”‘’.,;:!?()[]—–-…"
"""What is stripped from either end of a spoken word, so a voice's words and a placeholder's read alike."""


@dataclass(frozen=True)
class SpeechRequest:
    """One section of narration, and everything about it that decides what comes back."""

    text: str  # prose with <break time="0.7s" /> tags where pauses belong
    voice_id: str  # which voice reads it, which is a published name and part of the take hash
    model: str
    voice_settings: dict[str, Any] = field(default_factory=dict)
    output_format: str = "mp3_44100_128"
    previous_text: str | None = None  # neighbouring sections, for prosody continuity
    next_text: str | None = None


class Secrets(Protocol):
    """Where a provider asks for its credentials. The project's `.env` reader is the one implementation."""

    def require(self, *names: str) -> list[Secret]:
        """The values of these variables, or an error naming every one that is not set."""
        ...


@dataclass(frozen=True)
class VoiceContext:
    """Everything a provider needs from a project, with no project in it.

    `secrets` answers for the values in `.env`, and no value read through it is ever printed, logged
    or put in a payload. The rest are tuning keys, passed as values, so this layer imports no
    settings class and a provider built in a test is built the way a run builds one.
    """

    secrets: Secrets
    api_base: str  # [elevenlabs] api_base
    context_chars: int  # [narration] context_chars
    speech_timeout_seconds: int  # [narration] timeout_seconds
    sound_timeout_seconds: int  # [elevenlabs] timeout_seconds
    retries: int = 0
    """How many more times a busy or failed request is sent, which the machine sets from `[narration] retries`."""
    allow_any_api_base: bool = False
    """Whether `api_base` may name a host other than ElevenLabs, which the machine alone decides.

    It is off unless the machine that runs the call turns it on, so a context built without asking
    the machine sends the key to ElevenLabs and nowhere else.
    """


class SpeechProvider(Protocol):
    """Reads one section and returns the audio and a time for every word."""

    name: str

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]: ...

    def cache_key(self, request: SpeechRequest) -> str:
        """Everything that changes the audio besides the text: the voice, the model and the format."""
        ...


ProviderFactory = Callable[[VoiceContext], SpeechProvider]


def _elevenlabs(context: VoiceContext) -> SpeechProvider:
    from .elevenlabs import ElevenLabs  # noqa: PLC0415  (a provider is built only when one is asked for)

    return ElevenLabs.for_context(context)


PROVIDERS: dict[str, ProviderFactory] = {"elevenlabs": _elevenlabs}
"""The providers DeckTalk ships, which is the table a machine answers with unless its host gave another.

The list is written out rather than filled by an import for its side effect, so the whole of it is
readable here, and a test replaces one entry to run a stage without spending anything.
"""


@dataclass(frozen=True)
class Voices:
    """The voices one machine answers with, and whether its key may go to a host other than ElevenLabs."""

    factories: Mapping[str, ProviderFactory]
    allow_any_api_base: bool = False
    retries: int = 0


SHIPPED = Voices(factories=PROVIDERS)
"""What a caller that holds no machine is answered with: the shipped table, the key on ElevenLabs, no retry.

Both switches are at their closed values, so a caller outside a run can neither send a key to
another host nor repeat a paid request.
"""

BOUND: ContextVar[Voices] = ContextVar("decktalk_voices", default=SHIPPED)
"""The voices of the machine whose run is in progress, which `voicing` sets and `get_provider` reads."""


@contextmanager
def voicing(voices: Voices) -> Iterator[None]:
    """Answer every provider lookup from these voices while this is open, which a machine's run does."""
    token = BOUND.set(voices)
    try:
        yield
    finally:
        BOUND.reset(token)


def get_provider(name: str, context: VoiceContext) -> SpeechProvider:
    """The provider the running machine registers under `name`, built for this context.

    The machine's own decisions about where its key may go and how often a busy request is sent
    again replace whatever the context says, so a stage cannot widen the first and a context built
    without asking the machine cannot either.
    """
    voices = BOUND.get()
    factory = voices.factories.get(name)
    if factory is None:
        raise InputError(
            f"[voice] provider = {name!r} is not a voice this machine answers for.",
            hint=f"The providers it knows are {', '.join(sorted(voices.factories))}.",
        )
    return factory(replace(context, allow_any_api_base=voices.allow_any_api_base, retries=voices.retries))
