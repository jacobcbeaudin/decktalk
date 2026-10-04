"""Stage 4: the music, the ambience bed and the effects this project describes are generated.

    score/<name>.mp3          one bought file per ambience bed and effect, named by its item
    score/music-part<n>.mp3   each part of the music, bought in turn
    score/ledger.json         what this project has already bought, one row per item with its request's digest
    build/score/music.mp3     the music joined from its parts, which is a cache

It runs after `record`, so the unpaid draft loop still stops at a recording and every sound a run
buys is already bought by the time the film is cut. `narrate` is the other stage that buys, with
`cue` and `record` between the two, so a run that reaches here has nothing left to pay for.

Ambience and effects are one sound request each, and music is asked for in chunks of at most
`[score.music] chunk_max_seconds` and joined with a crossfade, because the service will not
write a long piece in one answer. Every request is priced by the seconds of audio it asks for, at
the per-minute rate its kind's own table states.

The provider is the one `[score] provider` names in the run's own sound table, which is a seam
of its own beside the voices, so this stage names no vendor and never asks what class it was given.

Every path this stage writes comes from `Workspace`, so no directory is spelled here. What is
bought lands in the score directory, `[score] dir`, which the project commits, so deleting the build
directory never buys a sound again. Each file is named by its item, so buying one again writes over
the one file the project keeps. The music joined from its parts is free to make again, so it is a
cache under the build, joined again by any run that finds its parts and not the piece.

What has already been bought is `ledger.py`, one typed file beside the audio rather than a cache
beside every output, and an item whose request still matches its row is kept rather than bought
again. Everything bought here is paid for, so this stage takes no `force`, which never spends. An
item is bought again only once its request moves or its bought audio is gone, or when
`replace_score` says to buy every item again, which a run that may not spend ignores because it has
nothing to replace a bought sound with.

Nothing is bought without `run.approve`, so a run that may not spend reports the plan and buys
nothing. The one file it may write is the music joined again from kept parts, which costs nothing.
The run's own `spend` is the only thing that says so, because a second flag beside it could be set
to contradict the gate. The sound provider is built only once something is bought, every paid
request is a `sound.charged` line the moment the provider answers, and the cost a run that bought
something reports is marked charged.

`only` names section numbers, because that is what every other stage takes, and the score's own
items are named rather than numbered. An effect is wanted when a `[[mix.effect]]` row cues it in a
selected section, the ambience bed is wanted when a selected page section asks for one, and the
music is wanted whenever any section is selected, because one piece of music plays under the whole film.
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
from decktalk.machine.run import Run
from decktalk.media import audio, ffmpeg
from decktalk.pipeline import Stage
from decktalk.results import Cost, CostState, ScoreResult, SoundItem, SoundKind, SoundOutcome
from decktalk.settings import AmbienceConfig, EffectConfig, MusicConfig
from decktalk.speech.sound import SOUND_DECLARED, SoundContext, SoundProvider, endpoint
from decktalk.stages import selects
from decktalk.stages.cost import Buy, charge_of, cost_of
from decktalk.stages.score.ledger import (
    UNFINISHED_DIGEST,
    Ledger,
    SoundEntry,
    request_digest,
)

AMBIENCE_NAME = "ambience"
"""What the ambience bed is called, which is the one item the `[score]` table does not name."""

MUSIC_NAME = "music"
"""What the music is called, which the `[score]` table does not name either."""


def requested_seconds(kind: SoundKind, body: Mapping[str, Any]) -> float:
    """How many seconds of audio one request asks for, which is what it is priced by."""
    if kind is SoundKind.MUSIC:
        return float(body["music_length_ms"]) / 1000
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
    parts: tuple[Path, ...] = ()
    """Where each bought part of the music is kept, in the order they are joined into `out`."""

    @property
    def bought(self) -> tuple[Path, ...]:
        """The files buying this item wrote, which are its parts for the music and its one file for a sound."""
        return self.parts or (self.out,)

    @property
    def seconds(self) -> float:
        """How many seconds of audio this item asks for, which is what its price is worked out on."""
        return sum(requested_seconds(self.kind, body) for body in self.bodies)

    @property
    def request(self) -> str:
        """What was asked for, as compact JSON with its keys sorted, which the ledger keeps for a reader."""
        return json.dumps(self.bodies[0] if len(self.bodies) == 1 else list(self.bodies), sort_keys=True)


def sound_body(spec: SoundSpec, cfg: AmbienceConfig | EffectConfig, *, loop: bool) -> dict[str, Any]:
    """The request one ambience bed or one effect sends, with its table's settings filling what the item left out."""
    body: dict[str, Any] = {
        "text": spec.prompt,
        "duration_seconds": spec.duration_seconds if spec.duration_seconds is not None else cfg.duration_seconds,
        "prompt_influence": spec.prompt_influence if spec.prompt_influence is not None else cfg.prompt_influence,
        "model_id": spec.model or cfg.model,
    }
    if loop:
        # An ambience bed plays under whole sections, so the service is asked for audio that meets its own end.
        body["loop"] = True
    return body


def music_bodies(spec: MusicSpec, cfg: MusicConfig) -> list[dict[str, Any]]:
    """One request per chunk of the music, each carrying the same prompt and its place in the piece.

    The service writes at most `chunk_max_seconds` in one answer, so longer music is asked for in
    equal parts and each part is told which one it is, which is what keeps the key and the tempo.
    """
    parts = max(1, math.ceil(cfg.duration_seconds / cfg.chunk_max_seconds))
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
                "music_length_ms": int(each * 1000),
            }
        )
    return bodies


def _out(inputs: Inputs, named: str | None, default: Path) -> Path:
    """Where one item is written, which is the path the project names or the workspace's own."""
    return inputs.path(named) if named else default


def plan_items(inputs: Inputs) -> list[Planned]:
    """Every item the `[score]` table declares, in the order the table declares them.

    Nothing here reads the disk or the network, so what a run would ask for can be read without
    asking for it, which is what prices a run before it spends. Each request is named in its digest
    by the endpoint its provider declares, which leaves out `base_url`, so a project that moves to
    another host of the same service keeps every sound it bought.
    """
    spec = inputs.document.score
    mix = inputs.document.mix
    cfg = inputs.settings.score
    sound, music = endpoint(cfg.provider, SoundKind.EFFECT), endpoint(cfg.provider, SoundKind.MUSIC)
    kept = inputs.workspace.score_dir
    items: list[Planned] = []
    if spec.ambience is not None:
        body = sound_body(spec.ambience, cfg.ambience, loop=True)
        items.append(
            Planned(
                name=AMBIENCE_NAME,
                kind=SoundKind.AMBIENCE,
                prompt=spec.ambience.prompt,
                out=_out(inputs, spec.ambience.out or mix.ambience, kept / f"{AMBIENCE_NAME}.mp3"),
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
                prompt=effect.prompt,
                out=_out(inputs, effect.out, kept / f"{name}.mp3"),
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
                out=_out(inputs, spec.music.out or mix.music, inputs.workspace.joined_music),
                endpoint=music,
                bodies=tuple(bodies),
                digest=request_digest(music, whole),
                parts=tuple(kept / f"{MUSIC_NAME}-part{index + 1}.mp3" for index in range(len(bodies))),
            )
        )
    return items


def wanted(inputs: Inputs, only: Sequence[int] | None) -> Callable[[Planned], bool]:
    """Whether one item belongs to this run, which a run that names no section answers yes to.

    A section is what every other stage selects on, so this is where the film's own sections are
    read back into the names the score table uses.
    """
    chosen = selects(only)
    document = inputs.document
    if not only:
        return lambda _item: True
    played = any(chosen(section.number) for section in document.sections)
    bedded = any(chosen(section.number) for section in document.page_sections if section.with_ambience)
    cued = {inputs.path(row.file) for row in document.mix.effects if chosen(row.section)}

    def keeps(item: Planned) -> bool:
        if item.kind is SoundKind.MUSIC:
            return played
        if item.kind is SoundKind.AMBIENCE:
            return bedded
        return item.out in cued

    return keeps


def stale(ledger: Ledger, item: Planned) -> bool:
    """Whether this item has to be bought, which is when its request moved or its bought audio is not there."""
    entry = ledger.of(item.name)
    return entry is None or entry.digest != item.digest or not all(path.is_file() for path in item.bought)


def unjoined(item: Planned) -> bool:
    """Whether this item's parts are all kept and the piece joined from them is not, which costs nothing to make."""
    return bool(item.parts) and not item.out.is_file() and all(part.is_file() for part in item.parts)


def ready(inputs: Inputs) -> bool:
    """Whether the score has bought something and every piece joined from bought parts is in place to mix."""
    held = inputs.workspace.score_dir
    return held.is_dir() and any(held.iterdir()) and not any(unjoined(item) for item in plan_items(inputs))


def _held(ledger: Ledger, item: Planned, *, replace: bool) -> frozenset[int]:
    """The music parts a run keeps rather than buys: bought before, under the same request, and still on disk.

    A run told to replace the score keeps no part, and a sound has no parts to keep.
    """
    previous = None if replace else ledger.of(item.name)
    known = previous.parts if previous is not None else ()
    return frozenset(
        index
        for index, (body, part) in enumerate(zip(item.bodies, item.parts, strict=False))
        if index < len(known) and known[index] == request_digest(item.endpoint, body) and part.is_file()
    )


def _buys(items: Sequence[Planned], held: Mapping[str, frozenset[int]], *, covered: tuple[int, ...]) -> list[Buy]:
    """What these items buy, one request at a time: every body but the music parts `held` names.

    Every request is a sound of its item's kind, priced on the seconds of audio it asks for, and each
    covers the sections the run selects, because the score plays under all of them.
    """
    return [
        Buy(seconds=requested_seconds(item.kind, body), sections=covered, kind=item.kind)
        for item in items
        for index, body in enumerate(item.bodies)
        if index not in held.get(item.name, frozenset())
    ]


def _covered(inputs: Inputs, only: Sequence[int] | None) -> tuple[int, ...]:
    """The sections a run of the score selects, which every sound it buys plays under."""
    chosen = selects(only)
    return tuple(section.number for section in inputs.document.sections if chosen(section.number))


def _to_buy(ledger: Ledger, item: Planned, *, replace: bool) -> float:
    """How many seconds of audio this item still asks for, which is every request but the parts held."""
    held = {item.name: _held(ledger, item, replace=replace)}
    return sum(buy.seconds for buy in _buys([item], held, covered=()))


def price(inputs: Inputs, *, only: Sequence[int] | None = None, replace_score: bool = False) -> Cost:
    """What a run of this stage with these options would buy, read from the plan and the ledger alone.

    It opens no run and builds no client, so a caller prices a score without touching the
    project's lock, its events or the service. A run told to replace the score buys every item it
    selects, so that is what it is priced at.
    """
    keeps = wanted(inputs, only)
    planned = [item for item in plan_items(inputs) if keeps(item)]
    ledger = Ledger.read(inputs.workspace.ledger_path) or Ledger()
    fresh = [item for item in planned if replace_score or stale(ledger, item)]
    held = {item.name: _held(ledger, item, replace=replace_score) for item in fresh}
    return cost_of(inputs, _buys(fresh, held, covered=_covered(inputs, only)))


def sound_context(inputs: Inputs) -> SoundContext:
    """What a sound provider is built from: its own table's base, this project's timeout and its own `.env`."""
    settings = inputs.settings
    declared = SOUND_DECLARED.get(settings.score.provider)
    base_url = str(getattr(settings, declared.table).base_url).rstrip("/") if declared is not None else ""
    return SoundContext(secrets=inputs.env, base_url=base_url, timeout_seconds=settings.score.timeout_seconds)


def client_for(run: Run, inputs: Inputs) -> SoundProvider:
    """The sound provider `[score] provider` names in the run's own sound table.

    It is looked up in the run's sounds, so a host that handed its machine another table is never
    billed through the shipped one, and the machine's switch and retries apply here too.
    """
    return run.machine.sound_providers.provider(inputs.settings.score.provider, sound_context(inputs))


def _keep(ledger: Ledger, path: Path, entry: SoundEntry) -> Ledger:
    """Record one row and write the ledger, which is checkpointed after every paid call."""
    grown = ledger.updated(entry)
    grown.write(path)
    return grown


def _charge(run: Run, inputs: Inputs, item: Planned, digest: str, buy: Buy) -> Buy:
    """Put one paid request on the stream the moment the provider answered, and give back what it bought.

    The provider is paid when it answers, so the charge is written before anything that could fail
    writes the audio, and a host that keeps its own ledger records every request it paid for. The
    charge is `buy` at the bill the cost module reads, the same buy the run was priced at.
    """
    dollars = charge_of(inputs, buy) or 0.0
    run.emit(SoundCharged, name=item.name, kind=item.kind, digest=digest, seconds=buy.seconds, dollars=dollars)
    return buy


def _buy_sound(
    run: Run, inputs: Inputs, client: SoundProvider, item: Planned, ledger: Ledger, path: Path, buy: Buy
) -> tuple[Ledger, list[Buy]]:
    """Buy one ambience bed or one effect, write it, and record what it was bought with and what it cost."""
    body = item.bodies[0]
    audio_bytes = client.effect(body, output_format=inputs.settings.score.output_format)
    bought = _charge(run, inputs, item, item.digest, buy)
    item.out.parent.mkdir(parents=True, exist_ok=True)
    item.out.write_bytes(audio_bytes)
    run.wrote(item.out)
    run.wrote(path)
    return _keep(ledger, path, _entry(inputs, item, item.digest)), [bought]


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
    run: Run,
    inputs: Inputs,
    client: SoundProvider,
    item: Planned,
    ledger: Ledger,
    path: Path,
    *,
    held: frozenset[int],
    buys: Sequence[Buy],
) -> tuple[Ledger, list[Buy], bool]:
    """Buy every part of the music the run was priced for, join them, and record each part as it is paid for.

    The parts a run keeps are decided when it is priced, as `held`, and this buys every other one, at
    the `buys` it was priced at in part order, and never asks the disk again, so it buys nothing the
    run was not approved for. The ledger is written after every part, so a run that is stopped half
    way through a long piece keeps what it has already bought and asks only for the rest. Only the
    parts bought by this run are charged.

    A held part that is gone since the run was priced is left for the next run, which prices and buys
    it. Then the piece is not joined, its ledger row stays unfinished, an older piece joined before
    is removed so the film never plays it, and a `SOUND_MISSING` finding names the part. The last of
    the three values says whether that happened.
    """
    digests: list[str] = []
    paid: list[Buy] = []
    gone: list[Path] = []
    priced = iter(buys)
    for index, (body, part) in enumerate(zip(item.bodies, item.parts, strict=True)):
        run.check()
        part.parent.mkdir(parents=True, exist_ok=True)
        digest = request_digest(item.endpoint, body)
        if index in held and part.is_file():
            run.note(f"{part.name} was bought before and its request is unchanged, so this run keeps it.")
        elif index in held:
            gone.append(part)
        else:
            audio_bytes = client.music(body, output_format=inputs.settings.score.output_format)
            paid.append(_charge(run, inputs, item, digest, next(priced)))
            part.write_bytes(audio_bytes)
            run.wrote(part)
        digests.append(digest)
        ledger = _keep(ledger, path, _entry(inputs, item, UNFINISHED_DIGEST, digests))
    if gone:
        item.out.unlink(missing_ok=True)
        run.found(
            judge(
                Code.SOUND_MISSING,
                f"{_named(item)} is not whole, because {gone[0].name} was kept when this run was priced and is gone "
                "now, so this run did not buy it unpriced. Buying it with `decktalk score --spend` asks for "
                f"{_to_buy(ledger, item, replace=False):g} seconds of audio.",
                _missing(inputs, item),
                stage=Stage.SCORE,
            )
        )
        return ledger, paid, True
    _join(run, inputs, item)
    run.wrote(path)
    return _keep(ledger, path, _entry(inputs, item, item.digest, digests)), paid, False


def _bought(
    run: Run,
    inputs: Inputs,
    client: SoundProvider,
    item: Planned,
    ledger: Ledger,
    path: Path,
    *,
    held: frozenset[int],
    covered: tuple[int, ...],
) -> tuple[Ledger, list[Buy], SoundItem]:
    """Buy one item the run was priced for, with what it paid for and its row, which is planned when not whole."""
    buys = _buys([item], {item.name: held}, covered=covered)
    if item.kind is not SoundKind.MUSIC:
        ledger, paid = _buy_sound(run, inputs, client, item, ledger, path, buys[0])
    else:
        ledger, paid, broken = _buy_music(run, inputs, client, item, ledger, path, held=held, buys=buys)
        if broken:
            return ledger, paid, _row(inputs, item, SoundOutcome.PLANNED, None)
    bought = ledger.of(item.name)
    return ledger, paid, _row(inputs, item, SoundOutcome.GENERATED, bought.seconds if bought else None)


def _join(run: Run, inputs: Inputs, item: Planned) -> None:
    """Join the kept parts of the music into the one piece the mix plays, which spends nothing."""
    cfg = inputs.settings.score.music
    item.out.parent.mkdir(parents=True, exist_ok=True)
    audio.crossfade_join(list(item.parts), item.out, crossfade_seconds=cfg.crossfade_seconds, bitrate=cfg.bitrate)
    run.wrote(item.out)


def _row(inputs: Inputs, item: Planned, outcome: SoundOutcome, seconds: float | None) -> SoundItem:
    """One item as the result reports it, whose file is named only once it is really there."""
    return SoundItem(
        name=item.name,
        kind=item.kind,
        outcome=outcome,
        prompt=item.prompt,
        seconds=seconds,
        file=inputs.relative(item.out) if item.out.is_file() else None,
    )


def _kept(run: Run, inputs: Inputs, item: Planned, ledger: Ledger) -> SoundItem:
    """One item this run keeps, with the music joined again from its kept parts when the piece is gone."""
    if unjoined(item):
        _join(run, inputs, item)
    entry = ledger.of(item.name)
    return _row(inputs, item, SoundOutcome.KEPT, entry.seconds if entry else None)


def _named(item: Planned) -> str:
    """How a sentence names one item, which is its kind alone when the kind is also its name."""
    return f"the {item.kind.value}" if item.name == item.kind.value else f"the {item.kind.value} {item.name}"


def score_files(inputs: Inputs) -> frozenset[Path]:
    """Every file this stage would write, which is what a mix that finds one absent knows it has not bought yet.

    A file the project names and this stage never writes is the author's own, so its absence is a
    `FILE_MISSING` error, while one of these is only unbought and plays silence under `SOUND_MISSING`.
    """
    return frozenset(item.out for item in plan_items(inputs))


def _missing(inputs: Inputs, item: Planned) -> Location:
    """Where a planned item that nobody has bought sits, which is the file a mix would look for."""
    return Location(where=item.name, file=inputs.relative(item.out))


def score(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    replace_score: bool = False,
) -> ScoreResult:
    """Compose the music, the ambience bed and the effects this project describes.

    A run that may not spend reports what it would ask for and buys nothing, which is the plan an
    author reads before approving a cost. `replace_score` is the one way a bought sound is bought
    again: a run that may spend buys every item it selects again, and one that may not keeps every
    item on disk, because it has nothing to replace a bought sound with.

    The one judgement this stage makes is `SOUND_MISSING`, for an item that is still only planned,
    which is a warning because the film is still made with silence where the item would play. A
    request the service refuses raises `PROVIDER`, so a run that returns has nothing else to judge.
    """
    keeps = wanted(inputs, only)
    planned = [item for item in plan_items(inputs) if keeps(item)]
    path = inputs.workspace.ledger_path
    ledger = Ledger.read(path) or Ledger()
    replace = run.spend and replace_score
    fresh = {item.name for item in planned if replace or stale(ledger, item)}
    held = {item.name: _held(ledger, item, replace=replace) for item in planned if item.name in fresh}
    covered = _covered(inputs, only)
    estimate = cost_of(inputs, _buys([item for item in planned if item.name in fresh], held, covered=covered))
    buying = bool(fresh) and run.spend
    if buying:
        run.approve(estimate)
    client = client_for(run, inputs) if buying else None
    rows: list[SoundItem] = []
    charged: list[Buy] = []
    for done, item in enumerate(planned):
        run.check()
        run.progress(Stage.SCORE, done=done, total=len(planned), unit=Unit.ASSET, label=item.name)
        if item.name not in fresh:
            rows.append(_kept(run, inputs, item, ledger))
            continue
        if client is None:
            rows.append(_row(inputs, item, SoundOutcome.PLANNED, None))
            continue
        ledger, paid, row = _bought(run, inputs, client, item, ledger, path, held=held[item.name], covered=covered)
        charged += paid
        rows.append(row)
    run.progress(Stage.SCORE, done=len(planned), total=len(planned), unit=Unit.ASSET, label="score")
    if not planned:
        run.note("The project declares no score for this run, so there is nothing to generate.", level=Level.INFO)
    for item, row in zip(planned, rows, strict=True):
        # A run that bought said why an item it could not finish is missing, so only an unbought item is said here.
        if row.outcome is SoundOutcome.PLANNED and not item.out.is_file() and not buying:
            run.found(
                judge(
                    Code.SOUND_MISSING,
                    f"{_named(item)} is not bought yet, so the film plays silence where it would be. Buying it "
                    f"with `decktalk score --spend` asks for {_to_buy(ledger, item, replace=replace):g} seconds of "
                    "audio.",
                    _missing(inputs, item),
                    stage=Stage.SCORE,
                )
            )
    cost = cost_of(inputs, charged, state=CostState.CHARGED) if buying else estimate
    return run.result(ScoreResult, spend=run.spend, items=tuple(rows), cost=cost)


__all__ = [
    "AMBIENCE_NAME",
    "MUSIC_NAME",
    "Planned",
    "client_for",
    "music_bodies",
    "plan_items",
    "price",
    "requested_seconds",
    "score_files",
    "sound_context",
    "sound_body",
    "score",
    "ready",
    "stale",
    "unjoined",
    "wanted",
]
