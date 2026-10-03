"""The sound boundary, which is anything that writes an effect or a piece of music from a prompt.

Sound is its own seam beside speech. A vendor that sells both still has two adapters, one in each
table, so the soundscape never borrows the speech registry and never checks which class a voice is.

    [soundscape]
    provider = "elevenlabs"   # the name a sound provider is registered under

A sound provider is built from a `SoundContext`, which carries values and never a project, so this
layer knows nothing about `decktalk.toml` or the build directory, and a test builds one from a
number and a source of secrets.

The sound adapters DeckTalk ships are a closed set, declared once in `SOUND_DECLARED`, and `SOUNDS`
is their factories, which a machine a host built by hand replaces with its own table.
Every run carries its machine's `Sounds`, and the soundscape asks the run's `sounds.provider` for its
provider, so a host that handed its machine a fake table is never billed through the shipped one.

What an adapter declares before it is built is in `SOUND_DECLARED`: the variable its key is read
from, the table its `api_base` is read from, the endpoint each kind of sound is bought from as the
service publishes it, and how it is built. That endpoint, with the request body, is what a sound's ledger digest is
taken over, so a project that moves to another host of the same service buys nothing again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ...errors import InputError
from ...results import SoundKind
from .. import Secrets


@dataclass(frozen=True)
class SoundContext:
    """Everything a sound provider needs from a project, with no project in it."""

    secrets: Secrets
    api_base: str  # the base its own table names, such as [elevenlabs] api_base
    timeout_seconds: int  # [soundscape] timeout_seconds
    retries: int = 0
    """How many more times a busy or failed request is sent, which the machine alone decides."""
    allow_any_api_base: bool = False
    """Whether `api_base` may name a host the adapter does not allow, which the machine alone decides."""


class SoundProvider(Protocol):
    """Writes one effect or one piece of music from the request a soundscape item describes."""

    name: str

    def effect(self, body: Mapping[str, Any], *, output_format: str) -> bytes:
        """One sound effect or ambience bed, as the audio bytes."""
        ...

    def music(self, body: Mapping[str, Any], *, output_format: str) -> bytes:
        """One piece of music, or one part of a longer one, as the audio bytes."""
        ...


SoundFactory = Callable[[SoundContext], SoundProvider]


@dataclass(frozen=True)
class SoundDeclared:
    """What one sound adapter DeckTalk ships declares about itself, read before it is built."""

    key_variable: str | None
    """The variable its credential is read from, or None."""
    table: str
    """The settings table its `api_base` is read from."""
    endpoint: Callable[[SoundKind], str]
    """The endpoint each kind is bought from as the service publishes it, which a ledger digest names."""
    factory: SoundFactory
    """How it is built from a context, which imports the adapter only when one is asked for."""


def _elevenlabs_endpoint(kind: SoundKind) -> str:
    from .elevenlabs import sound_endpoint  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return sound_endpoint(kind)


def _elevenlabs(context: SoundContext) -> SoundProvider:
    from .elevenlabs import ElevenLabsSound  # noqa: PLC0415  (a provider is built only when one is asked for)

    return ElevenLabsSound.for_context(context)


SOUND_DECLARED: dict[str, SoundDeclared] = {
    "elevenlabs": SoundDeclared(
        key_variable="ELEVENLABS_API_KEY", table="elevenlabs", endpoint=_elevenlabs_endpoint, factory=_elevenlabs
    )
}
"""For each sound adapter DeckTalk ships, what it declares about itself, in one place.

A sound provider a host registered itself is not here. It owns no table and needs no key DeckTalk
knows of, and its requests are named by its own name and the kind.
"""


def endpoint(provider: str, kind: SoundKind) -> str:
    """The endpoint `provider` buys this kind of sound from, as a ledger digest names it."""
    declared = SOUND_DECLARED.get(provider)
    return declared.endpoint(kind) if declared is not None else f"{provider}:{kind.value}"


SOUNDS: dict[str, SoundFactory] = {name: declared.factory for name, declared in SOUND_DECLARED.items()}
"""The factories of the sound adapters DeckTalk ships, the table a machine answers with unless its host gave one."""


@dataclass(frozen=True)
class Sounds:
    """The sound providers one machine answers with, and the machine's own decisions about every request."""

    factories: Mapping[str, SoundFactory]
    allow_any_api_base: bool = False
    retries: int = 0

    def provider(self, name: str, context: SoundContext) -> SoundProvider:
        """The provider this table registers under `name`, built for this context.

        The machine's decisions about where a key may go and how often a busy request is sent again
        replace whatever the context says, so a stage can widen neither.
        """
        factory = self.factories.get(name)
        if factory is None:
            known = ", ".join(sorted(self.factories)) or "none"
            raise InputError(
                f"[soundscape] provider = {name!r} is not a sound provider this machine answers for.",
                hint=f"The sound providers it knows are {known}.",
            )
        return factory(replace(context, allow_any_api_base=self.allow_any_api_base, retries=self.retries))


__all__ = ["SoundContext", "SoundFactory", "SoundProvider", "Sounds"]
