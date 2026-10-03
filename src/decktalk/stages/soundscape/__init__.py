"""Stage 4: the music, the ambience bed and the effects this project describes are generated.

    build/soundscape/<name>.mp3   one file per item the `[soundscape]` table declares
    build/soundscape/ledger.json  what this project has already bought, keyed by the request

It runs after `record`, so the unpaid draft loop still stops at a recording and every credit a run
spends is already spent by the time the film is cut. `narrate` is the other stage that buys, with
`cue` and `record` between the two, so a run that reaches here has nothing left to pay for.

Ambience and effects are one sound request each, and music is asked for in chunks of at most
`[soundscape.music] max_chunk_seconds` and joined with a crossfade, because the service will not
write a long piece in one answer. Every request is priced by the seconds of audio it asks for, at
the per-minute rate its kind's own table states.

The provider is the one `[soundscape] provider` names in the run's own sound table, which is a seam
of its own beside the voices, so this stage names no vendor and never asks what class it was given.

Every path this stage writes comes from `Workspace`, so no build directory is spelled here and a
project that moves its build directory moves its soundscape with it. What has already been bought
is `ledger.py`, one typed file rather than a cache beside every output, and an item whose request
still matches its row is kept rather than bought again.

Nothing is bought without `run.approve`, so a run that may not spend reports the plan and writes
nothing at all. The run's own `spend` is the only thing that says so, because a second flag beside
it could be set to contradict the gate. The sound provider is built only once something is bought,
every paid request is a `sound.charged` line the moment the provider answers, and the spend a run
that bought something reports is marked charged.

`only` names section numbers, because that is what every other stage takes, and the soundscape's own
items are named rather than numbered. An effect is wanted when a `[[mix.effects]]` row cues it in a
selected section, the ambience bed is wanted when a selected page section asks for one, and the
music is wanted whenever any section is selected, because one bed plays under the whole film.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decktalk.events import Level, SoundCharged, Unit
from decktalk.findings import Code, Location, judge
from decktalk.inputs import Inputs, MusicSpec, SoundSpec
from decktalk.machine import Run
from decktalk.media import audio, ffmpeg
from decktalk.page import MILLISECONDS
from decktalk.pipeline import Stage
from decktalk.results import (
    Billing,
    Layer,
    SoundItem,
    SoundKind,
    SoundscapeResult,
    SoundStatus,
    Spend,
    SpendState,
)
from decktalk.settings import AmbienceConfig, EffectsConfig, MusicConfig
from decktalk.speech.sound import SOUND_DECLARED, SoundContext, SoundProvider, endpoint
from decktalk.stages import DOLLAR_DIGITS, selects
from decktalk.stages.soundscape.ledger import (
    LEDGER_FILE,
    UNFINISHED_DIGEST,
    Ledger,
    SoundEntry,
    request_digest,
)

AMBIENCE_NAME = "ambience"
"""What the ambience bed is called, which is the one item the `[soundscape]` table does not name."""

MUSIC_NAME = "music"
"""What the music bed is called, which the `[soundscape]` table does not name either."""

SECONDS_PER_MINUTE = 60
"""Truth: the seconds in a minute, which is what a rate stated per minute is divided by to price a second."""

SECOND_DIGITS = 3
"""Truth: the seconds of audio a price is for are kept to the millisecond, the finest a request asks for."""

TABLES = {SoundKind.AMBIENCE: "ambience", SoundKind.EFFECT: "effects", SoundKind.MUSIC: "music"}
"""The `[soundscape]` table each kind is sized and priced by."""

PRICE_KEY = "price_per_minute"
"""The key each kind's table states its rate under, whose layer decides whether a ceiling may refuse a run."""


def requested_seconds(kind: SoundKind, body: Mapping[str, Any]) -> float:
    """How many seconds of audio one request asks for, which is what it is priced by."""
    if kind is SoundKind.MUSIC:
        return float(body["music_length_ms"]) / MILLISECONDS
    return float(body["duration_seconds"])


@dataclass(frozen=True)
class Planned:
    """One item this run would generate, worked out before anything is bought.

    A sound carries one body and music carries one per chunk, so the same row describes both and the
    ledger is keyed on the whole of it rather than on whichever half a reader looked at.
    """

    name: str
    kind: SoundKind
    prompt: str
    out: Path
    endpoint: str
    bodies: tuple[dict[str, Any], ...]
    digest: str

    @property
    def seconds(self) -> float:
        """How many seconds of audio this item asks for, which is what its price is worked out on."""
        return sum(requested_seconds(self.kind, body) for body in self.bodies)

    @property
    def request(self) -> str:
        """What was asked for, as compact JSON with its keys sorted, which the ledger keeps for a reader."""
        return json.dumps(self.bodies[0] if len(self.bodies) == 1 else list(self.bodies), sort_keys=True)


def sound_body(spec: SoundSpec, cfg: AmbienceConfig | EffectsConfig, *, loop: bool) -> dict[str, Any]:
    """The request one ambience bed or one effect sends, with its table's settings filling what the item left out."""
    body: dict[str, Any] = {
        "text": spec.text,
        "duration_seconds": spec.duration_seconds if spec.duration_seconds is not None else cfg.duration_seconds,
        "prompt_influence": spec.prompt_influence if spec.prompt_influence is not None else cfg.prompt_influence,
        "model_id": spec.model or cfg.model,
    }
    if loop:
        # A bed plays under whole sections, so the service is asked for audio that meets its own end.
        body["loop"] = True
    return body


def music_bodies(spec: MusicSpec, cfg: MusicConfig) -> list[dict[str, Any]]:
    """One request per chunk of the music, each carrying the same prompt and its place in the piece.

    The service writes at most `max_chunk_seconds` in one answer, so a longer bed is asked for in
    equal parts and each part is told which one it is, which is what keeps the key and the tempo.
    """
    parts = max(1, math.ceil(cfg.duration_seconds / cfg.max_chunk_seconds))
    each = cfg.duration_seconds / parts
    bodies: list[dict[str, Any]] = []
    for index in range(parts):
        prompt = spec.prompt
        if parts > 1:
            prompt += f" (part {index + 1} of {parts}, same key and tempo, seamless mood)"
        bodies.append(
            {
                "prompt": prompt,
                "force_instrumental": spec.force_instrumental,
                "model_id": cfg.model,
                "music_length_ms": int(each * MILLISECONDS),
            }
        )
    return bodies


def _out(inputs: Inputs, named: str | None, default: str) -> Path:
    """Where one item is written, which is the path the project names or the workspace's own."""
    return inputs.path(named) if named else inputs.workspace.soundscape_dir / default


def plan_items(inputs: Inputs) -> list[Planned]:
    """Every item the `[soundscape]` table declares, in the order the table declares them.

    Nothing here reads the disk or the network, so what a run would ask for can be read without
    asking for it, which is what prices a run before it spends. Each request is named in its digest
    by the endpoint its provider declares, which leaves out `api_base`, so a project that moves to
    another host of the same service keeps every sound it bought.
    """
    spec = inputs.document.soundscape
    mix = inputs.document.mix
    cfg = inputs.settings.soundscape
    sound, music = endpoint(cfg.provider, SoundKind.EFFECT), endpoint(cfg.provider, SoundKind.MUSIC)
    items: list[Planned] = []
    if spec.ambience is not None:
        body = sound_body(spec.ambience, cfg.ambience, loop=True)
        items.append(
            Planned(
                name=AMBIENCE_NAME,
                kind=SoundKind.AMBIENCE,
                prompt=spec.ambience.text,
                out=_out(inputs, spec.ambience.out or mix.ambience, f"{AMBIENCE_NAME}.mp3"),
                endpoint=sound,
                bodies=(body,),
                digest=request_digest(sound, body),
            )
        )
    for name, effect in spec.effects.items():
        body = sound_body(effect, cfg.effects, loop=False)
        items.append(
            Planned(
                name=name,
                kind=SoundKind.EFFECT,
                prompt=effect.text,
                out=_out(inputs, effect.out, f"{name}.mp3"),
                endpoint=sound,
                bodies=(body,),
                digest=request_digest(sound, body),
            )
        )
    if spec.music is not None:
        bodies = music_bodies(spec.music, cfg.music)
        whole = {"chunks": bodies, "crossfade_seconds": cfg.music.crossfade_seconds}
        items.append(
            Planned(
                name=MUSIC_NAME,
                kind=SoundKind.MUSIC,
                prompt=spec.music.prompt,
                out=_out(inputs, spec.music.out or mix.music, f"{MUSIC_NAME}.mp3"),
                endpoint=music,
                bodies=tuple(bodies),
                digest=request_digest(music, whole),
            )
        )
    return items


def wanted(inputs: Inputs, only: Sequence[int] | None) -> Callable[[Planned], bool]:
    """Whether one item belongs to this run, which a run that names no section answers yes to.

    A section is what every other stage selects on, so this is where the film's own sections are
    read back into the names the soundscape table uses.
    """
    chosen = selects(only)
    document = inputs.document
    if not only:
        return lambda _item: True
    played = any(chosen(section.number) for section in document.sections)
    bedded = any(chosen(section.number) for section in document.page_sections if section.ambience)
    cued = {inputs.path(row.file) for row in document.mix.effects if chosen(row.section)}

    def keeps(item: Planned) -> bool:
        if item.kind is SoundKind.MUSIC:
            return played
        if item.kind is SoundKind.AMBIENCE:
            return bedded
        return item.out in cued

    return keeps


def stale(ledger: Ledger, item: Planned) -> bool:
    """Whether this item has to be bought, which is when its request moved or its audio is not there."""
    entry = ledger.of(item.name)
    return entry is None or entry.digest != item.digest or not item.out.is_file()


def price_key_of(kind: SoundKind) -> str:
    """The dotted key that states this kind's rate per minute of audio."""
    return f"soundscape.{TABLES[kind]}.{PRICE_KEY}"


def rate_of(inputs: Inputs, kind: SoundKind) -> float:
    """What this kind of sound costs per second of audio, from the rate per minute its own table states."""
    table: AmbienceConfig | EffectsConfig | MusicConfig = getattr(inputs.settings.soundscape, TABLES[kind])
    return table.price_per_minute / SECONDS_PER_MINUTE


def dollars_of(inputs: Inputs, kind: SoundKind, seconds: float) -> float:
    """What this many seconds of this kind of sound cost at its stated rate, unrounded."""
    return seconds * rate_of(inputs, kind)


def layer_of(inputs: Inputs, kind: SoundKind) -> Layer:
    """Which layer stated this kind's rate, because a ceiling may not guard a price nobody has stated."""
    try:
        return inputs.layers.winner(price_key_of(kind)).layer
    except KeyError:
        # silent: a price no layer states is the default's.
        return Layer.DEFAULT


def spend_of(inputs: Inputs, items: Sequence[Planned], only: Sequence[int] | None) -> Spend:
    """What this run would cost, billed per second of audio, priced once so no caller works it out.

    Each item is the seconds of audio it asks for at its own kind's rate. When the kinds bought are
    priced at more than one rate, the price is still their exact sum, the rate is what that sum comes
    to per second, `averaged` says so, and `price_key` names the kind whose rate is least surely
    stated. The price is stated by the weakest layer among the kinds bought, so one rate nobody stated
    makes the whole price a default one that a ceiling refuses to guard, and the refusal names that
    rate's key. A run with nothing to buy covers no section, so its price says it buys nothing.
    """
    chosen = selects(only)
    seconds = sum(item.seconds for item in items)
    exact = sum(dollars_of(inputs, item.kind, item.seconds) for item in items)
    dollars = round(exact, DOLLAR_DIGITS)
    kinds = list(dict.fromkeys(item.kind for item in items))
    order = list(Layer)
    weakest = min(kinds, key=lambda kind: order.index(layer_of(inputs, kind)), default=None)
    rates = {rate_of(inputs, kind) for kind in kinds}
    covered = tuple(section.number for section in inputs.document.sections if chosen(section.number)) if items else ()
    return Spend(
        state=SpendState.ESTIMATE,
        sections=covered,
        characters=0,
        seconds=round(seconds, SECOND_DIGITS),
        dollars=dollars,
        ceiling_dollars=dollars,
        billing=Billing.PER_SECOND,
        price_per_1000_characters=0.0,
        price_per_second=exact / seconds if seconds else 0.0,
        price_key=price_key_of(weakest) if weakest is not None else None,
        averaged=len(rates) > 1,
        price_layer=layer_of(inputs, weakest) if weakest is not None else Layer.DEFAULT,
    )


def price(inputs: Inputs, *, only: Sequence[int] | None = None, force: bool = False) -> Spend:
    """What a run of this stage with these options would buy, read from the plan and the ledger alone.

    It opens no run and builds no client, so a caller prices a soundscape without touching the
    project's lock, its events or the service.
    """
    keeps = wanted(inputs, only)
    planned = [item for item in plan_items(inputs) if keeps(item)]
    ledger = Ledger.read(inputs.workspace.soundscape_dir / LEDGER_FILE) or Ledger()
    return spend_of(inputs, [item for item in planned if force or stale(ledger, item)], only)


def sound_context(inputs: Inputs) -> SoundContext:
    """What a sound provider is built from: its own table's base, this project's timeout and its own `.env`."""
    settings = inputs.settings
    declared = SOUND_DECLARED.get(settings.soundscape.provider)
    api_base = str(getattr(settings, declared.table).api_base) if declared is not None else ""
    return SoundContext(secrets=inputs.env, api_base=api_base, timeout_seconds=settings.soundscape.timeout_seconds)


def client_for(run: Run, inputs: Inputs) -> SoundProvider:
    """The sound provider `[soundscape] provider` names in the run's own sound table.

    It is looked up in the run's sounds, so a host that handed its machine another table is never
    billed through the shipped one, and the machine's switch and retries apply here too.
    """
    return run.sounds.provider(inputs.settings.soundscape.provider, sound_context(inputs))


def _keep(ledger: Ledger, path: Path, entry: SoundEntry) -> Ledger:
    """Record one row and write the ledger, which is checkpointed after every paid call."""
    grown = ledger.updated(entry)
    grown.write(path)
    return grown


def _charge(run: Run, inputs: Inputs, item: Planned, digest: str, body: Mapping[str, Any]) -> float:
    """Put one paid request on the stream the moment the provider answered, and give back what it cost.

    The provider is paid when it answers, so the charge is written before anything that could fail
    writes the audio, and a host that keeps its own ledger records every request it paid for.
    """
    seconds = requested_seconds(item.kind, body)
    dollars = dollars_of(inputs, item.kind, seconds)
    run.emit(SoundCharged, item=item.name, sound=item.kind, digest=digest, seconds=seconds, dollars=dollars)
    return dollars


def _buy_sound(
    run: Run, inputs: Inputs, client: SoundProvider, item: Planned, ledger: Ledger, path: Path
) -> tuple[Ledger, float]:
    """Buy one ambience bed or one effect, write it, and record what it was bought with and what it cost."""
    body = item.bodies[0]
    audio_bytes = client.effect(body, output_format=inputs.settings.soundscape.format)
    dollars = _charge(run, inputs, item, item.digest, body)
    item.out.parent.mkdir(parents=True, exist_ok=True)
    item.out.write_bytes(audio_bytes)
    run.wrote(item.out)
    run.wrote(path)
    return _keep(ledger, path, _entry(inputs, item, item.digest)), dollars


def _entry(inputs: Inputs, item: Planned, digest: str, parts: Sequence[str] = ()) -> SoundEntry:
    """One item's ledger row, measured once its audio is whole and unmeasured while its parts are still bought.

    A row whose parts are still being bought carries `UNFINISHED_DIGEST`, which matches no request, so
    a run stopped half way through buys the rest rather than keeping a piece that was never joined.
    """
    whole = digest != UNFINISHED_DIGEST
    return SoundEntry(
        name=item.name,
        kind=item.kind,
        digest=digest,
        file=inputs.relative(item.out),
        seconds=ffmpeg.probe_duration(item.out) if whole else None,
        parts=tuple(parts),
        request=item.request,
    )


def _buy_music(
    run: Run, inputs: Inputs, client: SoundProvider, item: Planned, ledger: Ledger, path: Path
) -> tuple[Ledger, float]:
    """Buy every part of the music, join them, and record each part as soon as it is paid for.

    The ledger is written after every part, so a run that is stopped half way through a long piece
    keeps what it has already bought and asks only for the rest. Only the parts bought by this run
    are charged.
    """
    cfg = inputs.settings.soundscape.music
    previous = ledger.of(item.name)
    known = previous.parts if previous is not None else ()
    item.out.parent.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    digests: list[str] = []
    dollars = 0.0
    for index, body in enumerate(item.bodies):
        run.check()
        part = item.out.with_name(f"{item.out.stem}-part{index + 1}.mp3")
        digest = request_digest(item.endpoint, body)
        if index < len(known) and known[index] == digest and part.is_file():
            run.note(f"{part.name} was bought before and its request is unchanged, so this run keeps it.")
        else:
            audio_bytes = client.music(body, output_format=inputs.settings.soundscape.format)
            dollars += _charge(run, inputs, item, digest, body)
            part.write_bytes(audio_bytes)
            run.wrote(part)
        digests.append(digest)
        parts.append(part)
        ledger = _keep(ledger, path, _entry(inputs, item, UNFINISHED_DIGEST, digests))
    audio.crossfade_join(parts, item.out, crossfade_seconds=cfg.crossfade_seconds, bitrate=cfg.bitrate)
    run.wrote(item.out)
    run.wrote(path)
    return _keep(ledger, path, _entry(inputs, item, item.digest, digests)), dollars


def _row(inputs: Inputs, item: Planned, status: SoundStatus, seconds: float | None) -> SoundItem:
    """One item as the result reports it, whose file is named only once it is really there."""
    return SoundItem(
        name=item.name,
        kind=item.kind,
        status=status,
        prompt=item.prompt,
        seconds=seconds,
        file=inputs.relative(item.out) if item.out.is_file() else None,
    )


def _missing(inputs: Inputs, item: Planned) -> Location:
    """Where a planned item that nobody has bought sits, which is the file a mix would look for."""
    return Location(where=item.name, file=inputs.relative(item.out))


def soundscape(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    force: bool = False,
) -> SoundscapeResult:
    """Generate the music, the ambience bed and the effects this project describes.

    A run that may not spend reports what it would ask for and buys nothing, which is the plan an
    author reads before approving a spend. The one judgement this stage makes is
    `FILE_MISSING`, for an item that is still only planned and whose audio a mix would look for and
    not find. A request the service refuses raises `PROVIDER`, so a run that returns has nothing
    else to judge.
    """
    keeps = wanted(inputs, only)
    planned = [item for item in plan_items(inputs) if keeps(item)]
    path = inputs.workspace.soundscape_dir / LEDGER_FILE
    ledger = Ledger.read(path) or Ledger()
    fresh = {item.name for item in planned if force or stale(ledger, item)}
    spend = spend_of(inputs, [item for item in planned if item.name in fresh], only)
    buying = bool(fresh) and run.spend
    if buying:
        run.approve(spend)
    client = client_for(run, inputs) if buying else None
    rows: list[SoundItem] = []
    charged = 0.0
    bought_any = False
    for done, item in enumerate(planned):
        run.check()
        run.progress(Stage.SOUNDSCAPE, done=done, total=len(planned), unit=Unit.ASSET, label=item.name)
        if item.name not in fresh:
            entry = ledger.of(item.name)
            rows.append(_row(inputs, item, SoundStatus.KEPT, entry.seconds if entry else None))
            continue
        if client is None:
            rows.append(_row(inputs, item, SoundStatus.PLANNED, None))
            continue
        buy = _buy_music if item.kind is SoundKind.MUSIC else _buy_sound
        ledger, dollars = buy(run, inputs, client, item, ledger, path)
        charged += dollars
        bought_any = True
        bought = ledger.of(item.name)
        rows.append(_row(inputs, item, SoundStatus.GENERATED, bought.seconds if bought else None))
    run.progress(Stage.SOUNDSCAPE, done=len(planned), total=len(planned), unit=Unit.ASSET, label="soundscape")
    if not planned:
        run.note("The project declares no soundscape for this run, so there is nothing to generate.", level=Level.INFO)
    for item, row in zip(planned, rows, strict=True):
        if row.status is SoundStatus.PLANNED and not item.out.is_file():
            run.found(
                judge(
                    Code.FILE_MISSING,
                    f"the {item.kind.value} {item.name} is planned and its audio is not on disk, so a mix would "
                    f"play without it. Generating it would ask for {item.seconds:g} seconds of audio.",
                    _missing(inputs, item),
                    stage=Stage.SOUNDSCAPE,
                )
            )
    if bought_any:
        spend = spend.model_copy(update={"state": SpendState.CHARGED, "dollars": round(charged, DOLLAR_DIGITS)})
    return run.result(SoundscapeResult, items=tuple(rows), spend=spend)


__all__ = [
    "AMBIENCE_NAME",
    "MUSIC_NAME",
    "Planned",
    "client_for",
    "music_bodies",
    "plan_items",
    "price",
    "requested_seconds",
    "sound_context",
    "sound_body",
    "soundscape",
    "spend_of",
    "stale",
    "wanted",
]
