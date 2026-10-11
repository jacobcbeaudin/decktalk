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
Every run carries its machine's `SoundProviders`, and the score stage asks
`run.machine.sound_providers` for its provider, so a host that handed its machine a fake table is
never billed through the shipped one.

The score asks for every sound as a `SoundRequest`, in DeckTalk's own words: its kind, its prompt,
how many seconds, the model, and for music which part of how many. The adapter renders a request
into the body its service reads, so a second vendor is a second adapter and never an edit to the
score, and a host's own provider is handed the request rather than another vendor's JSON.

What an adapter declares before it is built is in `SOUND_DECLARED`: the variable its key is read
from, the table its `base_url` is read from, the endpoint each kind of sound is bought from as the
service publishes it, how it renders a request into a body, and how it is built. That endpoint,
with the rendered body, is what a sound's ledger digest is taken over, so a project that moves to
another host of the same service buys nothing again, and a request rendered as it always was buys
nothing again either.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from types import MappingProxyType
from typing import Any, Protocol

from ...errors import InputError
from ...results import SoundKind
from .. import DECLARED, Secrets, registered


@dataclass(frozen=True)
class SoundContext:
    """Everything a sound provider needs from a project, with no project in it."""

    secrets: Secrets
    base_url: str  # its own table's base_url, such as [elevenlabs] base_url, which the machine alone sets
    timeout_seconds: int  # [score] timeout_seconds
    retries: int = 0
    """How many more times a busy or failed request is sent, which the machine alone decides."""


@dataclass(frozen=True)
class SoundRequest:
    """One sound a score item asks for, in DeckTalk's words and no vendor's.

    `seconds` is how much audio is asked for, which is what the request is priced by. `loop` asks
    for audio that meets its own end, which an ambience bed plays as. `influence` is how closely an
    effect or a bed follows its prompt, and `instrumental` keeps music free of vocals. Music longer
    than one answer is asked for in `parts` equal parts, and `part` counts from one.
    """

    kind: SoundKind
    prompt: str
    seconds: float
    model: str
    loop: bool = False
    influence: float | None = None
    instrumental: bool = False
    part: int = 1
    parts: int = 1


class SoundProvider(Protocol):
    """Writes one effect or one piece of music from the request a score item describes."""

    name: str

    def effect(self, request: SoundRequest, *, output_format: str) -> bytes:
        """One sound effect or ambience bed, as the audio bytes."""
        ...

    def music(self, request: SoundRequest, *, output_format: str) -> bytes:
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
    body: Callable[[SoundRequest], Mapping[str, Any]]
    """The body a request is sent as, which a ledger digest is taken over."""
    factory: SoundFactory
    """How it is built from a context, which imports the adapter only when one is asked for."""


def _elevenlabs_endpoint(kind: SoundKind) -> str:
    from .elevenlabs import sound_endpoint  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return sound_endpoint(kind)


def _elevenlabs_body(request: SoundRequest) -> Mapping[str, Any]:
    from .elevenlabs import sound_body  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return sound_body(request)


def _elevenlabs(context: SoundContext) -> SoundProvider:
    from .elevenlabs import ElevenLabsSound  # noqa: PLC0415  (a provider is built only when one is asked for)

    return ElevenLabsSound.for_context(context)


SOUND_DECLARED: Mapping[str, SoundDeclared] = MappingProxyType(
    {
        "elevenlabs": SoundDeclared(
            # The voice and the sound are bought on one account, so the sound reads the voice's key variable.
            key_variable=DECLARED["elevenlabs"].key_variable,
            table="elevenlabs",
            endpoint=_elevenlabs_endpoint,
            body=_elevenlabs_body,
            factory=_elevenlabs,
        )
    }
)
"""For each sound adapter DeckTalk ships, what it declares about itself, in one place.

A sound provider a host registered itself is not here, and never under a name that is. It owns no
table and needs no key DeckTalk knows of, and its requests are named by its own name and the kind.
"""


def endpoint(provider: str, kind: SoundKind) -> str:
    """The endpoint `provider` buys this kind of sound from, as a ledger digest names it."""
    declared = SOUND_DECLARED.get(provider)
    return declared.endpoint(kind) if declared is not None else f"{provider}:{kind.value}"


def request_body(provider: str, request: SoundRequest) -> Mapping[str, Any]:
    """The body `provider` sends `request` as, which a ledger digest is taken over.

    A provider DeckTalk ships renders its own service's body. A host's own provider is handed the
    request itself, so its digest is taken over the request's own fields.
    """
    declared = SOUND_DECLARED.get(provider)
    return declared.body(request) if declared is not None else {**asdict(request), "kind": request.kind.value}


SOUNDS: Mapping[str, SoundFactory] = MappingProxyType(
    {name: declared.factory for name, declared in SOUND_DECLARED.items()}
)
"""The factories of the sound adapters DeckTalk ships, the table a machine answers with unless its host gave one.

Like `SOUND_DECLARED`, it cannot be changed in place.
"""


@dataclass(frozen=True)
class SoundProviders:
    """The sound providers one machine answers with, and how often each asks again.

    A sound is priced at the rate its provider's name declares, so a name DeckTalk ships answers with
    the adapter shipped under it and nothing else, and a host's own factory under one, or under a
    spelling of one, is refused when it is registered. The table is copied as it passed.
    """

    factories: Mapping[str, SoundFactory]
    retries: int = 0

    def __post_init__(self) -> None:
        """Refuse a host's factory under a shipped sound provider's name, and keep the table as it passed."""
        table = registered(self.factories, SOUNDS, what="a sound provider", key="[score] provider")
        object.__setattr__(self, "factories", table)

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


__all__ = ["SoundContext", "SoundFactory", "SoundProvider", "SoundProviders", "SoundRequest", "request_body"]
