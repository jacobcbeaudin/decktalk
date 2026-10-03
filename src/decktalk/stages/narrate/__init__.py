"""Stage one: `script.md` becomes one take per section, indexed by content hash.

    build/narrate/<hash>.<suffix>    one take, named by the content that produced it, under the
                                     suffix its voice declares for what it holds, such as .mp3
    build/narrate/<hash>.words.json  a start and an end for every word in it
    build/narrate/takes.json         which section plays which take, what it cost, and where each
                                     section lands once the takes are joined
    build/narrate/narration.mp3      every take joined, with each section's silence around it

The take a section plays is found by content alone, so inserting a section, renumbering one or
retitling one moves no file and voices nothing, and two sections with the same words share one take.
`[narration] takes_dir` moves the paid takes, their words files and the index into a directory
inside the project that the author commits, so a fresh clone plays them with no key. A take is
looked for there first, then in the machine's `[narration] cache_dir`, then under `build/narrate/`,
and one this project buys or finds in the second or third place is written to the first place that
is set. A placeholder and the joined track stay under `build/narrate/`, because a build makes them
again for nothing.

Every take on disk is played, paid or placeholder, and the voice is built only when a take must be
made, so a run that makes nothing reads no key. Spend gates money and nothing else. A voice that
declares it bills nothing makes every missing take whether or not the run may spend, and a run that
may not spend never calls a voice that bills. Such a run plays a placeholder for each section whose
take is missing: a click track sized at `silent_words_per_minute` plus the declared pauses, with
evenly spaced estimated words, so the whole pipeline runs offline. Each such section is one
`TAKE_MISSING` finding, which names the command that buys its take. A project that has paid for
eight sections therefore builds its film with no key, and rehearses its ninth for nothing. A free
voice that nothing answers is a server that is not running, so its sections play placeholders too,
and their findings say to start it rather than to buy anything.

`TAKE_MISSING` is the one judgement this stage makes. What a script says badly is `check`'s to
report, because a judgement belongs where an author can act on it before any credit is spent, and
what the voice must not receive at all is refused here before a single request is sent.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from decktalk.artifacts import Take, Takes, is_placeholder
from decktalk.errors import InputError, ProviderError
from decktalk.events import Level, Unit
from decktalk.findings import Code, Finding, Location, judge
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.inputs.script import Segment
from decktalk.logs import cache_decision
from decktalk.machine import Run
from decktalk.pipeline import Stage
from decktalk.results import NarrateResult, SectionTake, Spend, SpendState, TakeStatus
from decktalk.speech import SpeechProvider, is_free, output_of, start_hint
from decktalk.stages import selects, voice_model
from decktalk.stages.narrate.plan import (
    VOICE_ID_VARIABLE,
    TakePlan,
    is_cached,
    named_voice,
    placeholder_plan,
    refuse_dropped_pauses,
    speech_provider,
    spend_of,
    voice_id_of,
    voiced_plan,
)
from decktalk.stages.narrate.script_rules import (
    ascending,
    check_script,
    script_refusals,
    shown,
    symbol_tokens,
)
from decktalk.stages.narrate.takes import (
    estimated_words,
    join_takes,
    keep_at_home,
    place,
    planned_words,
    take_row,
    write_placeholder_take,
    write_voiced_take,
)
from decktalk.stages.pool import Halt, Pool, nothing_to_open

log = logging.getLogger(__name__)


def narrate(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    force: bool = False,
    replace_voiced: bool = False,
) -> NarrateResult:
    """Speak every targeted section of the script, and time every word in it.

    Whether the run may buy is the run's own and never a parameter here, so one gate decides it for
    the library, the command line and a service alike. A run that may buy, or whose voice bills
    nothing, makes each missing take, and nothing is sent until `run.approve` has seen the price. One
    that may not buy from a voice that bills plays every take on disk, and a placeholder with a
    `TAKE_MISSING` finding for each take that is missing. `force` makes each placeholder again and
    never buys a take, and `replace_voiced` is the one way a take on disk is made again. The voice is
    built only once a take must be made, and the index is written again after every take, so a run
    that is stopped keeps everything it has already paid for.
    """
    targets = _targets(inputs, only)
    model = voice_model(inputs)
    free = is_free(inputs.settings.voice.provider)
    # A take is made again only when a run that may call the voice was told to replace it. `force`
    # never feeds this, because it rebuilds what DeckTalk makes itself, and a voice's take is not.
    plans, why = voiced_plan(
        inputs, targets, model=model, voice_id=named_voice(inputs), replace=(run.spend or free) and replace_voiced
    )
    # Spend gates money and nothing else, so a voice that bills nothing makes every missing take
    # whether or not the run may spend, once a voice is named to make them in.
    buying = run.spend or (free and why is None)
    missing: list[Finding] = []
    if not buying:
        previous = inputs.takes()
        if why and previous is not None and previous.voiced:
            # Paid takes are on disk and cannot be matched without the voice, so the run says why each
            # of them plays a placeholder. A project that never bought a take has nothing to match.
            run.note(why)
        plans, missing = _without_buying(inputs, plans, why=why, force=force, replace_voiced=replace_voiced)
    elif why:
        # A purchase needs the voice named, so this raises the refusal that says where to name it.
        voice_id_of(inputs)
    estimate = spend_of(plans, inputs, state=SpendState.ESTIMATE)
    provider: SpeechProvider | None = None
    if any(plan.status is TakeStatus.VOICED for plan in plans):
        # A run that sends nothing buys nothing, so the gate is asked and the voice is built only when
        # something would be bought, and a run that plays what is on disk reads no key at all.
        refuse_dropped_pauses(inputs, [plan.segment for plan in plans if plan.status is TakeStatus.VOICED], model=model)
        run.approve(estimate)
        provider = speech_provider(run, inputs)
    rows, made, unreached = _write_takes(inputs, run, plans, provider, model=model, free=free)
    index = _index(inputs, rows, model=model)
    run.wrote(index.write(inputs.workspace.takes_path))
    run.wrote(join_takes(inputs, index))
    _note_what_is_missing(inputs, run, index)
    if unreached:
        run.note(
            f"The voice could not be reached, so section(s) {[segment.index for segment in unreached]} play a "
            f"placeholder. {start_hint(inputs.settings.voice.provider)}, then run decktalk narrate again.",
            level=Level.WARNING,
        )
    missing += [_take_missing(inputs, segment, unreached=True) for segment in unreached]
    for found in sorted(missing, key=lambda found: found.location.section or 0):
        run.found(found)
    return run.result(
        NarrateResult,
        spending=run.spend,
        sections=tuple(made),
        spend=_charged(estimate, made) if buying else estimate,
        takes=inputs.relative(inputs.workspace.takes_path),
    )


def price(inputs: Inputs, *, only: Sequence[int] | None = None, replace_voiced: bool = False) -> Spend:
    """What a run of this stage that may spend would buy, planned the way `narrate` plans it and sending nothing.

    No run is opened and no voice is built, so a build prices its takes before it buys anything. A
    run told to replace its voiced takes is priced at every take it targets.
    """
    plans, _why = voiced_plan(
        inputs, _targets(inputs, only), model=voice_model(inputs), voice_id=named_voice(inputs), replace=replace_voiced
    )
    return spend_of(plans, inputs, state=SpendState.ESTIMATE)


def _without_buying(
    inputs: Inputs, paid: list[TakePlan], *, why: str | None, force: bool, replace_voiced: bool
) -> tuple[list[TakePlan], list[Finding]]:
    """(what a run that buys nothing does with each section, one `TAKE_MISSING` per placeholder).

    `paid` is what a run that buys would do, planned without `force`. A paid take on disk for a
    section's current text is played, and `force` remakes placeholders rather than discarding it,
    unless `replace_voiced` says to. Every other section plays a placeholder, cached by content like a
    paid take, and its finding says why its take could not be played.
    """
    held = inputs.workspace
    found = {plan.segment.index for plan in paid if plan.digest is not None and is_cached(plan.digest, held)}
    on_disk = {plan.segment.index: plan for plan in paid if plan.segment.index in found and not replace_voiced}
    missing = [plan.segment for plan in paid if plan.segment.index not in on_disk]
    stand_ins = {plan.segment.index: plan for plan in placeholder_plan(inputs, missing, force=force)} if missing else {}
    plans = [on_disk.get(plan.segment.index) or stand_ins[plan.segment.index] for plan in paid]
    previous = inputs.takes()
    unmatched = why is not None and previous is not None and bool(previous.voiced)
    return plans, [
        _take_missing(inputs, segment, replaced=segment.index in found, unmatched=unmatched) for segment in missing
    ]


def _take_missing(
    inputs: Inputs, segment: Segment, *, replaced: bool = False, unmatched: bool = False, unreached: bool = False
) -> Finding:
    """The finding for one section that plays a placeholder, saying why its take did not play and what makes it.

    What makes the take is the voice's bill: a voice that bills is bought from with `--spend`, and a
    free one that could not be reached is started.
    """
    number = segment.index
    provider = inputs.settings.voice.provider
    if unreached:
        why = "its voice could not be reached"
    elif replaced:
        why = "this run was told to replace its paid take, which stays on disk for a run without that flag"
    elif unmatched:
        why = f"no take on disk can be matched to it until [voice] id or {VOICE_ID_VARIABLE} names the voice"
    else:
        why = "it has no take of its current text on disk"
    if unreached:
        action = f"{start_hint(provider)}, then run decktalk narrate --section {number}."
    elif is_free(provider):
        # A free voice is called whenever a voice is named, so a free section plays a placeholder only for want of one.
        action = f"Name the voice, then run decktalk narrate --section {number} to make its take for nothing."
    else:
        action = f"Run decktalk narrate --section {number} --spend to buy its take."
    return judge(
        Code.TAKE_MISSING,
        f"Section {number} plays a placeholder, because {why}. {action}",
        Location(where=f"section {number}", file=inputs.relative(inputs.script_path), section=number),
        stage=Stage.NARRATE,
    )


def _targets(inputs: Inputs, only: Sequence[int] | None) -> list[Segment]:
    """The spoken sections this run works on, with the whole script checked before any of them."""
    segments = inputs.script()
    out_of_order = ascending(segments)
    if out_of_order is not None:
        first, second = out_of_order
        raise InputError(
            f"the script heading for section {second.index} comes after section {first.index}, "
            "so the sections do not ascend and the narration would be joined in an order nothing else agrees with.",
            hint=f"Move '## {second.index}. {second.title}' after '## {first.index}. {first.title}'.",
            location=at(inputs.script_path, inputs.root),
        )
    check_script(inputs.relative(inputs.script_path).as_posix(), inputs.script_path.read_text(encoding="utf-8"))
    wanted = selects(only)
    spoken = [segment for segment in inputs.spoken() if wanted(segment.index)]
    if not spoken:
        every = [segment.index for segment in inputs.spoken()]
        raise InputError(
            f"no spoken section matches {list(only or ())}, so there is nothing to narrate.",
            hint=f"The spoken sections are {every}.",
            location=at(inputs.script_path, inputs.root),
        )
    return spoken


def _write_takes(
    inputs: Inputs,
    run: Run,
    plans: list[TakePlan],
    provider: SpeechProvider | None,
    *,
    model: str,
    free: bool,
) -> tuple[dict[int, Take], list[SectionTake], list[Segment]]:
    """Make every take this run plans, `[narration] concurrency` at a time, checkpointing after each.

    The takes a plan found on disk are indexed after the pool has finished, because a section kept
    for sharing another section's words reads the take that section is still making. A `free` voice
    that nothing answers is a voice that is not running, so each section it would have made plays a
    placeholder and is given back last, and no section after the first asks it again. A voice that
    bills is not stood in for, because a take the author paid to buy is not optional.
    """
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    inputs.workspace.takes_path.parent.mkdir(parents=True, exist_ok=True)
    previous = inputs.takes()
    progress = _Progress(rows={take.section: take for take in previous.sections} if previous is not None else {})

    def one(plan: TakePlan) -> None:
        number = plan.segment.index
        with run.section(Stage.NARRATE, number):
            row, status = _one_take(inputs, run, plan, provider, progress.down if free else None)
            made = SectionTake(
                section=number,
                key=plan.segment.key,
                status=status,
                characters=plan.characters_sent,
                seconds=row.duration_seconds,
                file=inputs.relative(inputs.workspace.take_path(row.hash)),
                hash=row.hash,
            )
            # The count is reported under the lock that raised it, so a reader of the stream sees one,
            # two, three in that order however the pool's workers finish.
            with progress.lock:
                progress.rows[number] = row
                progress.made[number] = made
                _index(inputs, _placed(inputs, progress.rows, set(progress.made)), model=model).write(
                    inputs.workspace.takes_path
                )
                progress.done += 1
                run.progress(
                    Stage.NARRATE,
                    done=progress.done,
                    total=len(plans),
                    unit=Unit.TAKE,
                    label=plan.chapter or plan.segment.title,
                    section=number,
                )

    making = {plan.segment.index: plan for plan in plans if not plan.cached}
    workers = min(inputs.settings.narration.concurrency, len(making))
    if making:
        log.debug(
            "%d takes are made on %d workers.",
            len(making),
            workers,
            extra={
                "data": {"workers": workers, "jobs": len(making), "requested": inputs.settings.narration.concurrency}
            },
        )

    def take(_nothing: None, number: int, _halt: Halt) -> None:
        # A take never checks the halt once it has started, because its request is paid for once it is
        # sent, so a failure, a cancel or an interrupt starts no queued take and lets every take in
        # flight finish and be indexed.
        one(making[number])

    with Pool(list(making), workers, nothing_to_open, take, run.cancel) as pool:
        for number in making:
            pool.result(number)
    for plan in plans:
        if plan.cached:
            one(plan)
    made = [progress.made[plan.segment.index] for plan in plans]
    unreached = [
        plan.segment
        for plan in plans
        if plan.status is not TakeStatus.PLACEHOLDER
        and progress.made[plan.segment.index].status is TakeStatus.PLACEHOLDER
    ]
    return _placed(inputs, progress.rows, set(progress.made)), made, unreached


@dataclass
class _Progress:
    """What the workers of one narrate share, which every one of them changes only under its lock."""

    rows: dict[int, Take]
    made: dict[int, SectionTake] = field(default_factory=dict)
    done: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    down: threading.Event = field(default_factory=threading.Event)
    """Set once a free voice did not answer, so no section after it waits on the same voice again."""


def _one_take(
    inputs: Inputs, run: Run, plan: TakePlan, provider: SpeechProvider | None, down: threading.Event | None
) -> tuple[Take, TakeStatus]:
    """One section's take, made or found, with what this run did about it.

    `down` is given for a free voice alone. Once it is set, or once this section's request finds
    nothing answering, the section plays its placeholder instead of failing the run.
    """
    digest = plan.digest
    if digest is None:
        raise InputError(
            f"section {plan.segment.index} has no take digest, so the voice could not be set up.",
            hint=f"Set [voice] id in decktalk.toml or export {VOICE_ID_VARIABLE}, or run with --no-spend.",
            location=at(inputs.workspace.takes_path, inputs.root),
        )
    hit = plan.cached and is_cached(digest, inputs.workspace)
    # The plan's reason is the sentence `status` prints, and the token is what a reader filters on.
    why = "unchanged" if hit else "take-missing" if plan.cached else "to-make"
    cache_decision(
        log, Unit.TAKE.value, hit=hit, why=why, key=digest, section=plan.segment.index, reason=plan.reason or None
    )
    if hit:
        for path in keep_at_home(inputs, digest):
            run.wrote(path)
        voiced = not is_placeholder(digest)
        return take_row(inputs, plan.segment, plan.chapter, digest, voiced=voiced), TakeStatus.KEPT
    if down is not None and down.is_set() and plan.status is not TakeStatus.PLACEHOLDER:
        # A take the voice would make, or one another section was making, cannot come from a voice that is down.
        return _stand_in(inputs, run, plan)
    if plan.status is TakeStatus.VOICED:
        if provider is None or plan.request is None:
            raise InputError(
                f"section {plan.segment.index} would be voiced and this run has no request for it.",
                hint="Run `decktalk narrate` again, or run with --no-spend.",
                location=at(inputs.workspace.takes_path, inputs.root),
            )
        try:
            row, files = write_voiced_take(inputs, run, provider, plan.segment, plan.chapter, digest, plan.request)
        except ProviderError as failure:
            if down is None or failure.reached:
                raise
            down.set()
            return _stand_in(inputs, run, plan)
        status = TakeStatus.VOICED
    else:
        row, files = write_placeholder_take(inputs, plan.segment, plan.chapter, digest)
        status = TakeStatus.PLACEHOLDER
    for path in files:
        run.wrote(path)
    return row, status


def _stand_in(inputs: Inputs, run: Run, plan: TakePlan) -> tuple[Take, TakeStatus]:
    """The placeholder one section plays because its free voice did not answer, found on disk or made now."""
    (stand_in,) = placeholder_plan(inputs, [plan.segment])
    row, _kept = _one_take(inputs, run, replace(stand_in, chapter=plan.chapter), None, None)
    return row, TakeStatus.PLACEHOLDER


def _placed(inputs: Inputs, rows: dict[int, Take], touched: set[int]) -> dict[int, Take]:
    """Every row of the index, with the ones this run did not touch placed by the same rule."""
    spoken = {segment.index for segment in inputs.spoken()}
    return {
        number: row if number in touched else place(inputs, number, row)
        for number, row in rows.items()
        if number in spoken
    }


def _index(inputs: Inputs, rows: dict[int, Take], *, model: str) -> Takes:
    """The take index, in section order, which is the one order the narration is joined in."""
    return Takes(
        script=inputs.relative(inputs.script_path).as_posix(),
        model=model,
        output_format=output_of(inputs.settings, inputs.settings.voice.provider).format,
        sections=tuple(rows[number] for number in sorted(rows)),
    )


def _note_what_is_missing(inputs: Inputs, run: Run, index: Takes) -> None:
    """Say which spoken sections still have no take, because the narration covers the rest alone."""
    have = {take.section for take in index.sections}
    missing = [segment.index for segment in inputs.spoken() if segment.index not in have]
    if missing:
        run.note(
            f"Section(s) {missing} have no take yet, so the narration covers the rest alone.",
            level=Level.WARNING,
        )


def _charged(estimate: Spend, made: list[SectionTake]) -> Spend:
    """The price the run really paid, which is the estimate once the requests have been sent."""
    voiced = tuple(row.section for row in made if row.status is TakeStatus.VOICED)
    return estimate.model_copy(update={"state": SpendState.CHARGED, "sections": voiced})


__all__ = [
    "TakePlan",
    "estimated_words",
    "is_cached",
    "narrate",
    "place",
    "placeholder_plan",
    "planned_words",
    "price",
    "script_refusals",
    "shown",
    "spend_of",
    "symbol_tokens",
    "voiced_plan",
]
