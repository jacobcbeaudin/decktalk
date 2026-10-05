"""The speech boundary, which is anything that reads text aloud and says when each word was spoken.

DeckTalk needs exactly one thing from a voice: audio, and a start and an end time for every word,
because the cut is made on words. The voices a project can name are a closed set of adapters in this
package, each declared once in `DECLARED`:

    elevenlabs   the cloud voice, over https, bought with ELEVENLABS_API_KEY
    dtsp         a voice served by a separate local server, by default on a loopback address, free and keyless

    [voice]
    provider = "dtsp"   # the name an adapter is declared under

There are no entry points and no plugin loading. A project file names an adapter by its key and can
never name code. A host that embeds the library may hand `Machine.of` its own table of factories,
which is code the host wrote and imported itself, and it is how a test runs the real `narrate`
against a voice that spends nothing. The protocol types a host builds against are public here:
`SpeechProvider`, the `SpeechRequest` it receives and its `Piece`s, the `SpeechFactory` that builds
one from a `SpeechContext`, and the `Secrets` it reads its credential from.

A provider is built from a `SpeechContext`, which carries values and never a project, so this layer
knows nothing about `decktalk.toml`, the build directory or the stages, and a provider is built in a
test from a URL, three numbers and a source of secrets.

Every run carries its machine's `SpeechProviders`, and a stage asks `run.machine.speech_providers`
for a voice, so the voice is the one the machine running it answers with on whatever thread asks,
and two machines in one process cannot swap each other's voice. No module-level variable holds the
table a run reads, so nothing can fall back to a shipped voice that bills.

Where a provider sends its requests is its table's `base_url`, which is the machine's to set, so a
project someone else wrote can send neither the key nor the script anywhere the machine did not name.

The voice id is a published name rather than a credential. It names which voice reads the script,
the way a model name names which model does, and it travels in the request and in the take digest.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Protocol, cast

from ..errors import InputError
from ..results import BillingBasis, Word
from ..secret import Secret
from ..settings import DtspConfig, ElevenLabsConfig, ProviderTable, Settings

PUNCT = "\"'“”‘’.,;:!?()[]—–-…"
"""What is stripped from either end of a spoken word, so a voice's words and a placeholder's read alike."""


BEAT = 0.0
"""The pause of a beat, which asks for no measured silence and leaves the voice the short breath it takes on its own."""


@dataclass(frozen=True)
class Piece:
    """One run of text the voice reads, and the pause after it.

    A piece is a paragraph of the script. The pause is data rather than markup, so each provider
    renders it its own way: `None` is no pause, `BEAT` is a beat, and anything longer is that many
    seconds of silence.
    """

    text: str
    pause: float | None = None

    @property
    def timed(self) -> bool:
        """Whether this piece asks for a measured silence after it, which is a pause longer than a beat."""
        return self.pause is not None and self.pause > BEAT


def canonical_text(pieces: Sequence[Piece]) -> str:
    """The pieces as one text, which is what a take's digest and its bill are taken over.

    Pieces are joined by a blank line, a timed pause is written `<break time="0.7s" />` after its
    text and a beat as a dash. Every voiced take is named by this text byte for byte, which
    `tests/contract/test_take_hash.py` holds, and it is also what ElevenLabs is sent, so what a take
    is named by and what it is billed for are the same characters.
    """
    return "\n\n".join(piece.text + _written(piece.pause) for piece in pieces)


def _written(pause: float | None) -> str:
    """One pause as the canonical text writes it after its piece."""
    if pause is None:
        return ""
    return f' <break time="{pause:g}s" />' if pause > BEAT else " —"


@dataclass(frozen=True)
class SpeechRequest:
    """One section of narration, and everything about it that decides what comes back.

    The section travels as pieces with their pauses and never as markup, so the provider decides how
    a pause is said. The neighbouring sections travel as their spoken words, for prosody.
    """

    pieces: tuple[Piece, ...]
    voice_id: str  # which voice reads it, which is a published name and part of the take digest
    model: str
    voice_settings: dict[str, Any] = field(default_factory=dict)
    output_format: str = ""
    """The format its adapter declares, which `narrate` always names. Empty asks the service for its own default."""
    previous_text: str | None = None  # neighbouring sections, for prosody continuity
    next_text: str | None = None


class Secrets(Protocol):
    """Where a provider asks for its credentials. The project's `.env` reader is the one implementation."""

    def require(self, *names: str) -> list[Secret]:
        """The values of these variables, or an error naming every one that is not set."""
        ...


@dataclass(frozen=True)
class SpeechContext:
    """Everything a provider needs from a project, with no project in it.

    `secrets` answers for the values in `.env`, and no value read through it is ever printed, logged
    or put in a payload. The rest are settings, passed as values, so a provider is never handed a
    project and a provider built in a test is built the way a run builds one.
    """

    secrets: Secrets
    base_url: str  # its own table's base_url, such as [elevenlabs] base_url, which the machine alone sets
    context_characters: int  # [narration] context_characters
    speech_timeout_seconds: int  # [narration] timeout_seconds
    retries: int = 0
    """How many more times a busy or failed request is sent, which the machine sets from `[narration] retries`."""


class SpeechProvider(Protocol):
    """Reads one section and returns the audio and a time for every word.

    What a take is named by is not the provider's to say. The digest is taken in one place above this
    boundary, from the take's `TakeInputs`, which hold the output format its adapter declares in `DECLARED`.
    """

    name: str

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]: ...


SpeechFactory = Callable[[SpeechContext], SpeechProvider]


@dataclass(frozen=True)
class Billing:
    """How one adapter bills a take, which every price, the spend gate and `--max-cost` read.

    `by` is per character, per second of audio, or free. The rate is read from the adapter's own
    table under the key `rate` names, stated per 1,000 characters for a per-character bill and per
    minute of audio for a per-second one, so no layer above this one assumes how speech is billed.
    """

    by: BillingBasis
    rate: str | None = None
    """The key in the adapter's own table that states its rate, or None for a bill that has no rate."""


FREE = Billing(BillingBasis.FREE)
"""The bill of a voice that charges nothing, which never asks before it buys."""


@dataclass(frozen=True)
class Output:
    """The audio format one adapter asks for, and the suffix a take in that format is written under."""

    format: str
    """The format as the adapter's service spells it, which is part of every take's digest."""
    suffix: str
    """The file suffix a take in that format is named with, so a take is named by what it holds."""


HOST_OUTPUT = Output(format="mp3_44100_128", suffix=".mp3")
"""What a provider a host registered is asked for and its takes are named by, since it declares no format of its own.

Its takes are written `.mp3`, as every audio file DeckTalk reads is.
"""


LOOPBACK = frozenset(("127.0.0.1", "localhost", "::1"))
"""Truth: the names this machine answers to, which a request is sent to directly and never through a proxy."""


@dataclass(frozen=True)
class Declared:
    """What one adapter DeckTalk ships declares about itself, read by every layer above that would name a vendor.

    The adapter module is imported only when one of these is asked, so a run that names no voice
    imports no vendor.
    """

    key_variable: str | None
    """The variable its credential is read from, which `doctor`, the plan and every hint name, or None."""
    table: str
    """The settings table it owns, which holds its own fields, its default model and its rate."""
    renders_pauses: Callable[[str], bool]
    """Whether a model of it renders a timed pause. A beat is a dash every model reads as one."""
    identity: Callable[[ProviderTable, float], dict[str, Any]]
    """Its own table and `[voice] speed` as the settings a take's digest is taken over, under its own names."""
    billing: Billing
    """How it bills, per character, per second or free, and the key in its own table that states the rate."""
    output: Callable[[ProviderTable], Output]
    """The format its own table asks for, and the suffix a take in that format is written under."""
    factory: SpeechFactory
    """How it is built from a context, which imports the adapter only when one is asked for."""
    server: str | None = None
    """The local server that answers for it, which a run that cannot reach it tells the author to start, or None."""


def _elevenlabs_renders_pauses(model: str) -> bool:
    from .elevenlabs import renders_pauses  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return renders_pauses(model)


def _elevenlabs_identity(table: ProviderTable, speed: float) -> dict[str, Any]:
    from .elevenlabs import voice_settings  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return voice_settings(cast("ElevenLabsConfig", table), speed)


def _elevenlabs_output(table: ProviderTable) -> Output:
    from .elevenlabs import output  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return output(cast("ElevenLabsConfig", table))


def _elevenlabs(context: SpeechContext) -> SpeechProvider:
    from .elevenlabs import ElevenLabs  # noqa: PLC0415  (a provider is built only when one is asked for)

    return ElevenLabs.for_context(context)


def _dtsp_identity(table: ProviderTable, speed: float) -> dict[str, Any]:
    from .dtsp import identity  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return identity(cast("DtspConfig", table), speed)


def _dtsp_output(_table: ProviderTable) -> Output:
    from .dtsp import OUTPUT  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return OUTPUT


def _dtsp(context: SpeechContext) -> SpeechProvider:
    from .dtsp import Dtsp  # noqa: PLC0415  (a provider is built only when one is asked for)

    return Dtsp.for_context(context)


DECLARED: Mapping[str, Declared] = MappingProxyType(
    {
        "elevenlabs": Declared(
            key_variable="ELEVENLABS_API_KEY",
            table="elevenlabs",
            renders_pauses=_elevenlabs_renders_pauses,
            identity=_elevenlabs_identity,
            billing=Billing(BillingBasis.PER_CHARACTER, rate="dollars_per_1000_characters"),
            output=_elevenlabs_output,
            factory=_elevenlabs,
        ),
        "dtsp": Declared(
            key_variable=None,
            table="dtsp",
            # The pieces travel as data and the server joins them with measured silence, so every model renders a pause.
            renders_pauses=lambda _model: True,
            identity=_dtsp_identity,
            billing=FREE,
            output=_dtsp_output,
            factory=_dtsp,
            server="decktalk-voice",
        ),
    }
)
"""The closed set: every speech adapter DeckTalk ships, and what each declares about itself, in one place.

A provider a host registered itself is not here. It owns no table, needs no key DeckTalk knows of,
is handed the pieces and renders their pauses its own way, is asked for `HOST_OUTPUT`, and its takes are named
by `[voice] speed` alone among the settings. Its bill is undeclared, so DeckTalk prices it at nothing
anybody stated and `--max-cost` refuses to guard it.
"""


@dataclass(frozen=True)
class VoiceInForce:
    """The voice one project is read in: what it is called, what it declares, and what names its takes.

    It is resolved once from the settings, which already hold `[voice]`, the provider's own table and
    `DECKTALK_VOICE_ID`, and from the closed set in `DECLARED`. It holds no factory and no secret, so it
    can never build the provider or read its key. `SpeechProviders.provider` builds the provider. A
    provider DeckTalk does not declare is one a host registered: it owns no table, its takes are named
    by `[voice] speed` alone, and its bill is undeclared.
    """

    provider: str
    """`[voice] provider`, the name the machine builds the voice by and the first input of every take digest."""
    id: str
    """`[voice] id`, which `DECKTALK_VOICE_ID` overrides, or empty when neither names one."""
    model: str
    """The `model` of the provider's own table, the model asked for and never the one served.

    It is empty for a provider with no table.
    """
    output: Output
    """The format it is asked for, which every take digest is over, and the suffix its takes are written under."""
    billing: BillingBasis
    """How it bills: per character, per second, free, or undeclared for a provider a host registered."""
    price_key: str | None
    """The dotted key that states its rate, such as `elevenlabs.dollars_per_1000_characters`.

    It is None when its bill has no rate.
    """
    identity: Mapping[str, Any]
    """The settings it is sent and a take digest is over, under its own names, a pure function of the settings.

    Read-only.
    """
    renders_pauses: bool
    """Whether its model renders a timed pause. A beat is a dash every model reads, so it is never asked about."""
    base_url: str
    """Its own table's `base_url` without a trailing slash, which only the machine sets, or empty."""
    key_variable: str | None
    """The variable its credential is read from, which doctor and every hint name, or None when it needs none."""
    start_hint: str
    """The sentence that starts it when nothing answered, naming its server and the setting that says where."""

    @classmethod
    def of(cls, settings: Settings) -> VoiceInForce:
        """The voice these settings name, with a provider absent from `DECLARED` given a host's defaults."""
        name, speed = settings.voice.provider, settings.voice.speed
        declared = DECLARED.get(name)
        if declared is None:
            return cls(
                provider=name,
                id=settings.voice.id,
                model="",
                output=HOST_OUTPUT,
                billing=BillingBasis.UNDECLARED,
                price_key=None,
                identity=MappingProxyType({"speed": speed}),
                renders_pauses=True,
                base_url="",
                key_variable=None,
                start_hint=f"Start the voice [voice] provider = {name!r} answers from",
            )
        table = cast("ProviderTable", getattr(settings, declared.table))
        rate = declared.billing.rate
        return cls(
            provider=name,
            id=settings.voice.id,
            model=table.model,
            output=declared.output(table),
            billing=declared.billing.by,
            price_key=f"{declared.table}.{rate}" if rate is not None else None,
            identity=MappingProxyType(declared.identity(table, speed)),
            renders_pauses=declared.renders_pauses(table.model),
            base_url=table.base_url.rstrip("/"),
            key_variable=declared.key_variable,
            start_hint=(
                f"Start {declared.server or 'the voice server'} at the address [{declared.table}] base_url names"
            ),
        )


PROVIDERS: Mapping[str, SpeechFactory] = MappingProxyType(
    {name: declared.factory for name, declared in DECLARED.items()}
)
"""The factories of the closed set, which is the table a machine answers with unless its host gave another.

It is read off `DECLARED`, so the set is written once and every adapter in it is declared whole. Neither
table can be changed in place: a test hands its fake voice to the run's machine, as a host does through
`Machine.of`.
"""


@dataclass(frozen=True)
class SpeechProviders:
    """The voices one machine answers with, and how often each asks again."""

    factories: Mapping[str, SpeechFactory]
    retries: int = 0

    def provider(self, name: str, context: SpeechContext) -> SpeechProvider:
        """The provider these voices register under `name`, built for this context.

        The machine's own decision about how often a busy request is sent again replaces whatever the
        context says, so a stage cannot widen it.
        """
        factory = self.factories.get(name)
        if factory is None:
            raise InputError(
                f"[voice] provider = {name!r} is not a voice this machine answers for.",
                hint=f"The providers it knows are {', '.join(sorted(self.factories))}.",
            )
        return factory(replace(context, retries=self.retries))


__all__ = [
    "BEAT",
    "Billing",
    "Output",
    "Piece",
    "Secret",
    "Secrets",
    "SpeechContext",
    "SpeechFactory",
    "SpeechProvider",
    "SpeechProviders",
    "SpeechRequest",
]
