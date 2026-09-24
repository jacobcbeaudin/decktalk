"""Stage 4: the music, the ambience bed and the effects this project describes are generated.

    build/soundscape/<name>.mp3   one file per item the `[soundscape]` table declares
    build/soundscape/ledger.json  what this project has already bought, keyed by the request

It runs after `record`, so the unpaid draft loop still stops at a recording and the two stages that
spend sit beside each other. Ambience and effects are one sound request each, billed by the service
per second of audio, and music is asked for in chunks of at most `[elevenlabs] max_music_chunk_seconds`
and joined with a crossfade, because the service will not write a long piece in one answer.

Every path this stage writes comes from `Workspace`, so no build directory is spelled here and a
project that moves its build directory moves its soundscape with it. What has already been bought
is `ledger.py`, one typed file rather than a cache beside every output, and an item whose request
still matches its row is kept rather than bought again.

Nothing is bought without `run.approve`, so a run whose voicing is `placeholder` reports the plan
and writes nothing at all. That is what the old `dry_run` parameter said, and saying it once through
the voicing is what keeps a flag from contradicting the gate beside it.

`only` names section numbers, because that is what every other stage takes, and the soundscape's own
items are named rather than numbered. An effect is wanted when a `[[mix.effects]]` row cues it in a
selected section, the ambience bed is wanted when a selected page section asks for one, and the
music is wanted whenever any section is selected, because one bed plays under the whole film.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decktalk.events import Level, Unit
from decktalk.findings import Code, Location
from decktalk.inputs import Inputs, MusicSpec, SoundSpec
from decktalk.machine import Run
from decktalk.media import audio, ffmpeg
from decktalk.pipeline import Stage
from decktalk.results import (
    SoundItem,
    SoundKind,
    SoundscapeResult,
    SoundStatus,
    Spend,
    SpendState,
    Voicing,
)
from decktalk.settings import ElevenLabsConfig
from decktalk.speech import VoiceContext
from decktalk.speech.elevenlabs import ElevenLabs, check_api_base
from decktalk.stages import clock, judge, selects, since
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

SOUND_PATH = "/sound-generation"
"""Where a sound request goes on the service, which is part of what a ledger row is keyed by."""

MUSIC_PATH = "/music"
"""Where a music request goes on the service, which is the other endpoint a row may be keyed by."""

PRICE_KEY = "voice.price_per_1000_characters"
"""The key that states what speech costs, which is the only rate this project publishes."""

CHARACTERS_PER_PRICE_UNIT = 1000
"""Truth: the price is stated per thousand characters, which is the unit the key's own name carries."""

MILLISECONDS = 1000
"""Truth: milliseconds in one second, which is the unit the music service takes its length in."""


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
    def characters(self) -> int:
        """How many characters of prompt this item would send, which is what its price is worked out on."""
        return sum(len(str(body.get("text") or body.get("prompt") or "")) for body in self.bodies)

    @property
    def request(self) -> str:
        """What was asked for, as compact JSON with its keys sorted, which the ledger keeps for a reader."""
        return json.dumps(self.bodies[0] if len(self.bodies) == 1 else list(self.bodies), sort_keys=True)


def sound_body(spec: SoundSpec, cfg: ElevenLabsConfig, *, loop: bool) -> dict[str, Any]:
    """The request one ambience bed or one effect sends, with the settings filling what the author left out."""
    body: dict[str, Any] = {
        "text": spec.text,
        "duration_seconds": spec.duration_seconds
        if spec.duration_seconds is not None
        else (cfg.ambience_seconds if loop else cfg.effect_seconds),
        "prompt_influence": spec.prompt_influence
        if spec.prompt_influence is not None
        else (cfg.ambience_prompt_influence if loop else cfg.effect_prompt_influence),
        "model_id": spec.model_id or cfg.sound_model,
    }
    if loop:
        # A bed plays under whole sections, so the service is asked for audio that meets its own end.
        body["loop"] = True
    return body


def music_bodies(spec: MusicSpec, cfg: ElevenLabsConfig) -> list[dict[str, Any]]:
    """One request per chunk of the music, each carrying the same prompt and its place in the piece.

    The service writes at most `max_music_chunk_seconds` in one answer, so a longer bed is asked for
    in equal parts and each part is told which one it is, which is what keeps the key and the tempo.
    """
    parts = max(1, math.ceil(spec.seconds / cfg.max_music_chunk_seconds))
    each = spec.seconds / parts
    bodies: list[dict[str, Any]] = []
    for index in range(parts):
        prompt = spec.prompt
        if parts > 1:
            prompt += f" (part {index + 1} of {parts}, same key and tempo, seamless mood)"
        bodies.append(
            {
                "prompt": prompt,
                "force_instrumental": spec.force_instrumental,
                "model_id": spec.model_id or cfg.music_model,
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
    asking for it, which is what prices a run before it spends.
    """
    spec = inputs.document.soundscape
    mix = inputs.document.mix
    cfg = inputs.settings.elevenlabs
    base = check_api_base(cfg.api_base, inputs.env.environ).rstrip("/")
    sound, music = base + SOUND_PATH, base + MUSIC_PATH
    items: list[Planned] = []
    if spec.ambience is not None:
        body = sound_body(spec.ambience, cfg, loop=True)
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
        body = sound_body(effect, cfg, loop=False)
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
        bodies = music_bodies(spec.music, cfg)
        whole = {"chunks": bodies, "crossfade_seconds": cfg.music_crossfade_seconds}
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


def spend_of(inputs: Inputs, items: Sequence[Planned], only: Sequence[int] | None) -> Spend:
    """What this run would cost, priced once so no caller works it out from a character count.

    The only rate this project publishes is the one speech is billed at, so the price is the prompt
    characters at that rate. A sound request is billed by the service per second of audio, so this
    is the figure the spend gate holds and never an invoice.
    """
    chosen = selects(only)
    rate = inputs.settings.voice.price_per_1000_characters
    characters = sum(item.characters for item in items)
    dollars = round(characters / CHARACTERS_PER_PRICE_UNIT * rate, 2)
    return Spend(
        state=SpendState.ESTIMATE,
        sections=tuple(section.number for section in inputs.document.sections if chosen(section.number)),
        characters=characters,
        dollars=dollars,
        ceiling_dollars=dollars,
        price_per_1000_characters=rate,
        price_layer=inputs.layers.winner(PRICE_KEY).layer,
    )


def client_for(inputs: Inputs) -> ElevenLabs:
    """The sound service this project buys from, built from its own settings and its own `.env`."""
    settings = inputs.settings
    return ElevenLabs.for_context(
        VoiceContext(
            secrets=inputs.env,
            api_base=settings.elevenlabs.api_base,
            context_chars=settings.narration.context_chars,
            speech_timeout_seconds=settings.narration.timeout_seconds,
            sound_timeout_seconds=settings.elevenlabs.timeout_seconds,
        )
    )


def _keep(ledger: Ledger, path: Path, entry: SoundEntry) -> Ledger:
    """Record one row and write the ledger, which is checkpointed after every paid call."""
    grown = ledger.updated(entry)
    grown.write(path)
    return grown


def _buy_sound(run: Run, inputs: Inputs, client: ElevenLabs, item: Planned, ledger: Ledger, path: Path) -> Ledger:
    """Buy one ambience bed or one effect, write it, and record what it was bought with."""
    item.out.parent.mkdir(parents=True, exist_ok=True)
    item.out.write_bytes(client.sound_effect(item.bodies[0], output_format=inputs.settings.narration.output_format))
    run.wrote(item.out)
    entry = SoundEntry(
        name=item.name,
        kind=item.kind,
        digest=item.digest,
        file=inputs.relative(item.out),
        seconds=ffmpeg.probe_duration(item.out),
        request=item.request,
    )
    run.wrote(path)
    return _keep(ledger, path, entry)


def _unfinished(inputs: Inputs, item: Planned, parts: Sequence[str]) -> SoundEntry:
    """The row a music item carries while its parts are still being bought, which matches no request."""
    return SoundEntry(
        name=item.name,
        kind=item.kind,
        digest=UNFINISHED_DIGEST,
        file=inputs.relative(item.out),
        seconds=None,
        parts=tuple(parts),
        request=item.request,
    )


def _buy_music(run: Run, inputs: Inputs, client: ElevenLabs, item: Planned, ledger: Ledger, path: Path) -> Ledger:
    """Buy every part of the music, join them, and record each part as soon as it is paid for.

    The ledger is written after every part, so a run that is stopped half way through a long piece
    keeps what it has already bought and asks only for the rest.
    """
    cfg = inputs.settings.elevenlabs
    known = (ledger.of(item.name) or _unfinished(inputs, item, ())).parts
    item.out.parent.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    digests: list[str] = []
    for index, body in enumerate(item.bodies):
        run.check()
        part = item.out.with_name(f"{item.out.stem}-part{index + 1}.mp3")
        digest = request_digest(item.endpoint, body)
        if index < len(known) and known[index] == digest and part.is_file():
            run.note(f"{part.name} was bought before and its request is unchanged, so this run keeps it.")
        else:
            part.write_bytes(client.music(body, output_format=inputs.settings.narration.output_format))
            run.wrote(part)
        digests.append(digest)
        parts.append(part)
        ledger = _keep(ledger, path, _unfinished(inputs, item, digests))
    audio.crossfade_join(parts, item.out, crossfade_seconds=cfg.music_crossfade_seconds, bitrate=cfg.music_bitrate)
    run.wrote(item.out)
    run.wrote(path)
    entry = SoundEntry(
        name=item.name,
        kind=item.kind,
        digest=item.digest,
        file=inputs.relative(item.out),
        seconds=ffmpeg.probe_duration(item.out),
        parts=tuple(digests),
        request=item.request,
    )
    return _keep(ledger, path, entry)


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

    A run whose voicing is `placeholder` reports what it would ask for and buys nothing, which is
    the plan an author reads before approving a spend. The one judgement this stage makes is
    `FILE_MISSING`, for an item that is still only planned and whose audio a mix would look for and
    not find. A request the service refuses raises `PROVIDER`, so a run that returns has nothing
    else to judge.
    """
    started = clock()
    keeps = wanted(inputs, only)
    planned = [item for item in plan_items(inputs) if keeps(item)]
    path = inputs.workspace.soundscape_dir / LEDGER_FILE
    ledger = Ledger.read(path) or Ledger()
    fresh = {item.name for item in planned if force or stale(ledger, item)}
    spend = spend_of(inputs, [item for item in planned if item.name in fresh], only)
    buying = bool(fresh) and run.voice is Voicing.PAID
    if buying:
        run.approve(spend)
    client = client_for(inputs) if buying else None
    rows: list[SoundItem] = []
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
        ledger = buy(run, inputs, client, item, ledger, path)
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
                    f"play without it. Generating it would send {item.characters} characters.",
                    _missing(inputs, item),
                    stage=Stage.SOUNDSCAPE,
                )
            )
    return run.result(SoundscapeResult, items=tuple(rows), spend=spend, seconds=since(started))


__all__ = [
    "AMBIENCE_NAME",
    "MUSIC_NAME",
    "Planned",
    "client_for",
    "music_bodies",
    "plan_items",
    "sound_body",
    "soundscape",
    "spend_of",
    "stale",
    "wanted",
]
