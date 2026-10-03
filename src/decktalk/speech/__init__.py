"""The speech boundary, which is anything that reads text aloud and says when each word was spoken.

DeckTalk needs exactly one thing from a voice: audio, and a start and an end time for every word,
because the cut is made on words. The voices a project can name are a closed set of adapters in this
package, each declared once in `DECLARED`:

    elevenlabs   the cloud voice, over https, bought with ELEVENLABS_API_KEY
    dtsp         a voice served by a separate local server on a loopback address, free and keyless

    [voice]
    provider = "dtsp"   # the name an adapter is declared under

There are no entry points and no plugin loading. A project file names an adapter by its key and can
never name code. A host that embeds the library may hand `Machine.of` its own table of factories,
which is code the host wrote and imported itself, and it is how a test runs the real `narrate`
against a voice that spends nothing. The protocol types a host builds against are public here:
`SpeechProvider`, the `SpeechRequest` it receives and its `Piece`s, the `ProviderFactory` that builds
one from a `VoiceContext`, and the `Secrets` it reads its credential from.

A provider is built from a `VoiceContext`, which carries values and never a project, so this layer
knows nothing about `decktalk.toml`, the build directory or the stages, and a provider is built in a
test from four numbers and a source of secrets.

Every run carries its machine's `Voices`, and a stage asks the run's `voices.provider` for a voice, so
the voice is the one the machine running it answers with on whatever thread asks, and two machines in
one process cannot swap each other's voice. No module-level variable holds the table a run reads, so
nothing can fall back to a shipped paid voice. The same `Voices` carries the machine's decision about
whether a base URL may name a host its adapter does not allow, which is stamped onto every context a
provider is built from.

The voice id is a published name rather than a credential. It names which voice reads the script,
the way a model name names which model does, and it travels in the request and in the take hash.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from ..errors import InputError
from ..results import Billing, Word
from ..secret import Secret
from ..settings import ALLOW_ANY_API_BASE, DtspConfig, ElevenLabsConfig, ProviderTable, Settings

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
    text and a beat as a dash. Every paid take is named by this text byte for byte, which
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
    voice_id: str  # which voice reads it, which is a published name and part of the take hash
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
class VoiceContext:
    """Everything a provider needs from a project, with no project in it.

    `secrets` answers for the values in `.env`, and no value read through it is ever printed, logged
    or put in a payload. The rest are settings, passed as values, so a provider is never handed a
    project and a provider built in a test is built the way a run builds one.
    """

    secrets: Secrets
    api_base: str  # the base URL its own table names, such as [elevenlabs] api_base or [dtsp] url
    context_chars: int  # [narration] context_chars
    speech_timeout_seconds: int  # [narration] timeout_seconds
    retries: int = 0
    """How many more times a busy or failed request is sent, which the machine sets from `[narration] retries`."""
    allow_any_api_base: bool = False
    """Whether `api_base` may name a host its adapter does not allow, which the machine alone decides.

    It is off unless the machine that runs the call turns it on, so a context built without asking
    the machine sends a request only to the hosts its adapter declares.
    """


class SpeechProvider(Protocol):
    """Reads one section and returns the audio and a time for every word.

    What a take is named by is not the provider's to say. The digest is taken in one place above this
    boundary, from the take identity and the output format its adapter declares in `DECLARED`.
    """

    name: str

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]: ...


ProviderFactory = Callable[[VoiceContext], SpeechProvider]


@dataclass(frozen=True)
class Bill:
    """How one adapter bills a take, which every price, every spend and every cap reads.

    `by` is per character, per second of audio, or free. The rate is read from the adapter's own
    table under the key `rate` names, stated per 1,000 characters for a per-character bill and per
    second for a per-second one, so no layer above this one assumes how speech is billed.
    """

    by: Billing
    rate: str | None = None
    """The key in the adapter's own table that states its rate, or None for a bill that has no rate."""


FREE = Bill(Billing.FREE)
"""The bill of a voice that charges nothing, which never asks before it buys."""

UNDECLARED = Bill(Billing.UNDECLARED)
"""The bill of a provider a host registered itself, which declares nothing DeckTalk can price."""


@dataclass(frozen=True)
class Output:
    """The audio format one adapter asks for, and the suffix a take in that format is written under."""

    format: str
    """The format as the adapter's service spells it, which is part of every take's digest."""
    suffix: str
    """The file suffix a take in that format is named with, so a take is named by what it holds."""


HOST_OUTPUT = Output(format="mp3_44100_128", suffix=".mp3")
"""What a provider a host registered is asked for and its takes are named by, which declares no format of its own.

It is the format every provider was asked for before an adapter declared its own, so a take a host's
provider was already paid for keeps its digest and is written `.mp3`, as every audio file DeckTalk reads is.
"""


LOOPBACK = frozenset(("127.0.0.1", "localhost", "::1"))
"""Truth: the names this machine answers to, which a request to a local server may name and nothing else may."""


@dataclass(frozen=True)
class Hosts:
    """Where one adapter may send a request: the scheme it needs and the hosts it may name.

    A request carries a credential, the script or both, so each adapter declares the hosts it is
    meant for and a base URL naming any other is refused before the first request, unless the
    machine running it allows any host.
    """

    scheme: str
    names: frozenset[str]
    subdomains: bool = False
    """Whether a subdomain of a named host is allowed too, as a vendor's regional hosts are."""

    def allows(self, url: str) -> bool:
        """Whether `url` is on one of these hosts, over this scheme.

        A URL that carries a user, a password or a backslash is refused whatever its host reads as,
        because the host urllib connects to can differ from the one the URL appears to name, and so
        is one this parser cannot read at all.
        """
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
        except ValueError:
            # silent: a URL that cannot be read names no host this adapter allows.
            return False
        if "@" in parts.netloc or "\\" in url:
            return False
        named = host in self.names or (self.subdomains and any(host.endswith(f".{name}") for name in self.names))
        return parts.scheme == self.scheme and named

    @property
    def said(self) -> str:
        """These hosts in words, as a refusal names them."""
        hosts = sorted(f"[{name}]" if ":" in name else name for name in self.names)
        listed = hosts[0] if len(hosts) == 1 else f"{', '.join(hosts[:-1])} or {hosts[-1]}"
        return f"an {self.scheme} URL on {listed}"


def checked_base(url: str, hosts: Hosts, *, setting: str, allow_any: bool) -> str:
    """`url` without its trailing slash when it is on `hosts` or the machine allows any, and otherwise an error.

    `setting` is the key the URL was read from, such as `[dtsp] url`, which the refusal names. The
    value itself is not quoted, because it may be set from the environment and reaches an error that
    a --json payload carries, and only the switch that lifts this check may be printed. Whether any
    host is allowed is the machine's own field, passed in, so a project file can never lift the check.
    """
    if allow_any or hosts.allows(url):
        return url.rstrip("/")
    raise InputError(
        f"{setting} must be {hosts.said}.",
        hint=f"Set {ALLOW_ANY_API_BASE}=1 to send requests to another host on purpose.",
    )


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
    billing: Bill
    """How it bills, per character, per second or free, and the key in its own table that states the rate."""
    output: Callable[[ProviderTable], Output]
    """The format its own table asks for, and the suffix a take in that format is written under."""
    hosts: Hosts
    """The hosts its requests may go to, which its base URL is checked against before the first one."""
    base: str
    """The key in its own table that holds its base URL, such as `api_base` or `url`."""
    factory: ProviderFactory
    """How it is built from a context, which imports the adapter only when one is asked for."""


def _elevenlabs_renders_pauses(model: str) -> bool:
    from .elevenlabs import renders_pauses  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return renders_pauses(model)


def _elevenlabs_identity(table: ProviderTable, speed: float) -> dict[str, Any]:
    from .elevenlabs import voice_settings  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return voice_settings(cast("ElevenLabsConfig", table), speed)


def _elevenlabs_output(table: ProviderTable) -> Output:
    from .elevenlabs import output  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return output(cast("ElevenLabsConfig", table))


def _elevenlabs(context: VoiceContext) -> SpeechProvider:
    from .elevenlabs import ElevenLabs  # noqa: PLC0415  (a provider is built only when one is asked for)

    return ElevenLabs.for_context(context)


def _dtsp_identity(table: ProviderTable, speed: float) -> dict[str, Any]:
    from .dtsp import identity  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return identity(cast("DtspConfig", table), speed)


def _dtsp_output(_table: ProviderTable) -> Output:
    from .dtsp import OUTPUT  # noqa: PLC0415  (the adapter is imported only when it is asked about)

    return OUTPUT


def _dtsp(context: VoiceContext) -> SpeechProvider:
    from .dtsp import Dtsp  # noqa: PLC0415  (a provider is built only when one is asked for)

    return Dtsp.for_context(context)


ELEVENLABS_HOSTS = Hosts(scheme="https", names=frozenset(("elevenlabs.io",)), subdomains=True)
"""Truth: where an ElevenLabs request may go, which is https on elevenlabs.io and its regional subdomains."""

DTSP_HOSTS = Hosts(scheme="http", names=LOOPBACK)
"""Truth: where a `dtsp` request may go, which is the local server on this machine over http."""


DECLARED: dict[str, Declared] = {
    "elevenlabs": Declared(
        key_variable="ELEVENLABS_API_KEY",
        table="elevenlabs",
        renders_pauses=_elevenlabs_renders_pauses,
        identity=_elevenlabs_identity,
        billing=Bill(Billing.PER_CHARACTER, rate="price_per_1000_characters"),
        output=_elevenlabs_output,
        hosts=ELEVENLABS_HOSTS,
        base="api_base",
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
        hosts=DTSP_HOSTS,
        base="url",
        factory=_dtsp,
    ),
}
"""The closed set: every speech adapter DeckTalk ships, and what each declares about itself, in one place.

A provider a host registered itself is not here. It owns no table, needs no key DeckTalk knows of,
is handed the pieces and renders their pauses its own way, is asked for `HOST_OUTPUT`, and its takes are named
by `[voice] speed` alone among the settings. Its bill is undeclared, so DeckTalk prices it at nothing
anybody stated and a spend cap refuses to guard it.
"""


def table_of(settings: Settings, provider: str) -> ProviderTable | None:
    """The settings table `provider` owns, or None for a provider a host registered with no table of its own."""
    declared = DECLARED.get(provider)
    return cast("ProviderTable", getattr(settings, declared.table)) if declared is not None else None


def base_of(settings: Settings, provider: str) -> str:
    """The base URL `provider`'s own table names, or nothing for a provider a host registered with no table."""
    declared, table = DECLARED.get(provider), table_of(settings, provider)
    return str(getattr(table, declared.base)) if declared is not None and table is not None else ""


def check_host(settings: Settings, provider: str, voices: Voices) -> None:
    """Refuse a base URL `provider` may not send to, which `check` asks before anything is planned or bought.

    Only an adapter of the closed set is checked, and only when the machine's voices answer with it:
    a provider a host registered, under a new name or a shipped one, declares no hosts and builds its
    own requests, so nothing here can refuse it.
    """
    declared = DECLARED.get(provider)
    if declared is not None and voices.factories.get(provider) is declared.factory:
        url = base_of(settings, provider)
        setting = f"[{declared.table}] {declared.base}"
        checked_base(url, declared.hosts, setting=setting, allow_any=voices.allow_any_api_base)


def billing_of(provider: str) -> Bill:
    """How `provider` bills, which a provider a host registered leaves undeclared."""
    declared = DECLARED.get(provider)
    return declared.billing if declared is not None else UNDECLARED


def output_of(settings: Settings, provider: str) -> Output:
    """The format `provider` is asked for and the suffix its takes are written under."""
    declared, table = DECLARED.get(provider), table_of(settings, provider)
    return declared.output(table) if declared is not None and table is not None else HOST_OUTPUT


def renders_pauses(provider: str, model: str) -> bool:
    """Whether `provider` renders a timed pause on `model`, so `check` and `narrate` can refuse one it would drop.

    A provider DeckTalk does not ship is one a host registered itself. It is handed the pieces and
    renders their pauses its own way, so nothing here can say it drops one.
    """
    declared = DECLARED.get(provider)
    return declared is None or declared.renders_pauses(model)


def key_variable(provider: str) -> str | None:
    """The variable `provider` reads its credential from, or None when it declares none."""
    declared = DECLARED.get(provider)
    return declared.key_variable if declared is not None else None


PROVIDERS: dict[str, ProviderFactory] = {name: declared.factory for name, declared in DECLARED.items()}
"""The factories of the closed set, which is the table a machine answers with unless its host gave another.

It is read off `DECLARED`, so the set is written once and every adapter in it is declared whole. A
test replaces one entry to run a stage without spending anything.
"""


@dataclass(frozen=True)
class Voices:
    """The voices one machine answers with, and whether a base URL may name a host its adapter does not allow."""

    factories: Mapping[str, ProviderFactory]
    allow_any_api_base: bool = False
    retries: int = 0

    def provider(self, name: str, context: VoiceContext) -> SpeechProvider:
        """The provider these voices register under `name`, built for this context.

        The machine's own decisions about where its key may go and how often a busy request is sent
        again replace whatever the context says, so a stage cannot widen the first and a context built
        without asking the machine cannot either.
        """
        factory = self.factories.get(name)
        if factory is None:
            raise InputError(
                f"[voice] provider = {name!r} is not a voice this machine answers for.",
                hint=f"The providers it knows are {', '.join(sorted(self.factories))}.",
            )
        return factory(replace(context, allow_any_api_base=self.allow_any_api_base, retries=self.retries))


__all__ = [
    "BEAT",
    "Bill",
    "Hosts",
    "Output",
    "Piece",
    "ProviderFactory",
    "Secret",
    "Secrets",
    "SpeechProvider",
    "SpeechRequest",
    "VoiceContext",
    "Voices",
]
