"""The sound boundary, which is anything that writes an effect or a piece of music from a prompt.

Sound is its own seam beside speech. A vendor that sells both still has two adapters, one in each
table, so the score never borrows the speech registry and never checks which class a voice is.

    [score]
    provider = "elevenlabs"   # the name a sound provider is registered under

A sound provider is built from a `SoundContext`, which carries values and never a project, so this
layer knows nothing about `decktalk.toml` or the build directory, and a test builds one from a
number and a source of secrets.

The sound adapters DeckTalk ships are a closed set, declared once in `SOUND_DECLARED`, and `SOUNDS`
is their factories, which a machine a host built by hand replaces with its own table.
Every run carries its machine's `SoundProviders`, and the score stage asks the run's `sounds.provider` for its
provider, so a host that handed its machine a fake table is never billed through the shipped one.

What an adapter declares before it is built is in `SOUND_DECLARED`: the variable its key is read
from, the table its `base_url` is read from, the endpoint each kind of sound is bought from as the
service publishes it, and how it is built. That endpoint, with the request body, is what a sound's ledger digest is
taken over, so a project that moves to another host of the same service buys nothing again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ...errors import InputError
from ...results import SoundKind
from .. import DECLARED, Secrets


@dataclass(frozen=True)
class SoundContext:
    """Everything a sound provider needs from a project, with no project in it."""

    secrets: Secrets
    base_url: str  # its own table's base_url, such as [elevenlabs] base_url, which the machine alone sets
    timeout_seconds: int  # [score] timeout_seconds
    retries: int = 0
    """How many more times a busy or failed request is sent, which the machine alone decides."""


class SoundProvider(Protocol):
    """Writes one effect or one piece of music from the request a score item describes."""

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
    """The settings table its `base_url` is read from."""
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
        # The voice and the sound are bought on one account, so the sound reads the voice's key variable.
        key_variable=DECLARED["elevenlabs"].key_variable,
        table="elevenlabs",
        endpoint=_elevenlabs_endpoint,
        factory=_elevenlabs,
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
class SoundProviders:
    """The sound providers one machine answers with, and how often each asks again."""

    factories: Mapping[str, SoundFactory]
    retries: int = 0

    def provider(self, name: str, context: SoundContext) -> SoundProvider:
        """The provider this table registers under `name`, built for this context.

        The machine's decision about how often a busy request is sent again replaces whatever the
        context says, so a stage cannot widen it.
        """
        factory = self.factories.get(name)
        if factory is None:
            known = ", ".join(sorted(self.factories)) or "none"
            raise InputError(
                f"[score] provider = {name!r} is not a sound provider this machine answers for.",
                hint=f"The sound providers it knows are {known}.",
            )
        return factory(replace(context, retries=self.retries))


__all__ = ["SoundContext", "SoundFactory", "SoundProvider", "SoundProviders"]
