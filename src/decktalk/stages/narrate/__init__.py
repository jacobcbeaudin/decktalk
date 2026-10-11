"""Stage one: `script.md` becomes one take per turn, indexed by input digest.

    takes/<digest>.<suffix>            one take, named by the content that produced it, under the
                                     suffix its voice declares for what it holds, such as .mp3
    takes/<digest>.words.json          a start and an end for every word in it
    build/narrate/takes.json         which turn of which section plays which take, what it cost, and
                                     where each lands once the takes are joined
    build/narrate/narration.mp3      every take joined, with each section's silence around it

A turn is the stretch of a section one voice reads, so a section with no voice direction is one turn
and one take, and a dialogue is one take per turn, each bought from its own voice's provider at its
own bill and joined on the section's clock with the gap the script writes where the voice changes.

The take a section plays is found by content alone, so inserting a section, renumbering one or
retitling one moves no file and voices nothing, and two sections with the same words share one take.
Every take a voice spoke lives in the takes directory, `[narration] takes_dir`, which is `takes/`
inside the project and which the author commits, so a fresh clone plays them with no key and
deleting `build/` costs nothing. A take is looked for there first and then in the machine's
`[narration] store_dir`, and one found in the second place is copied into the first, for every section
the narration plays whether or not the run selected it, so the narration is joined from the takes
directory alone. A placeholder, the take index and the joined track stay under `build/narrate/`,
because a build makes them again for nothing.

Every take on disk is played, voiced or placeholder, and the voice is built only when a take must be
made, so a run that makes nothing reads no key. Spend gates money and nothing else. A voice that
declares it bills nothing, once it is named, makes every missing take whether or not the run may
spend, and a run that may not spend never calls a voice that bills. Such a run plays a placeholder
for each section whose take is missing: click audio sized at `placeholder_words_per_minute` plus the
declared pauses, with evenly spaced estimated words, so the whole pipeline runs offline. Each such
section is one `TAKE_MISSING` finding, which says why its take did not play in the words the plan
found, and names the command that buys it. A takes directory holding none of the takes the project
played before is said as that, because a renamed or missing folder is not a script edit. A project
that has paid for eight sections therefore builds its film with no key, and rehearses its ninth for
nothing. A free voice that nothing answers is a server that is not running, so its sections play
placeholders too, and their findings say to start it rather than to buy anything.

A run told to voice named turns again, by number or by the take they play, voices those turns and
nothing else, even though a take of their text is held. The new take is written under the same
digest, because the digest names what the take was made from and that has not changed, and the take
it replaces is set aside in the takes directory's `rejected/` folder rather than deleted, so the take
index, `status` and every later stage read the new take as they read the old one. Each row of the
take index records the BLAKE3 of the audio it was measured on, so a section whose take on disk is not
that audio, such as a rejected take moved back under its digest, is read again by the next run that
does not select it, for nothing, rather than placed on numbers measured on other audio.

A take its voice sent no word times with is timed by the aligner, `[dtsp] base_url`'s server, after
every take is made, and its words are kept in `takes/aligned/`. A take whose aligned words are kept
plays them without asking the server, and `--force` asks again. A take with none kept and no aligner
answering plays estimated words with a `TAKE_UNALIGNED` finding.

A take whose voice or aligner placed a word after the take's sound ends, where the join cuts it,
plays that word with no length, with a `TAKE_WORD_CUT` finding.

`TAKE_MISSING`, `TAKE_UNALIGNED` and `TAKE_WORD_CUT` are the three judgements this stage makes. What a script says badly
is `check`'s to report, because a judgement belongs where an author can act on it before anything is
bought, and what the voice must not receive at all is refused here before a single request is sent.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace

from decktalk.artifacts import Take, Takes, TurnKey, is_placeholder
from decktalk.errors import HALTS, ErrorInfo, InputError, ProviderError, as_refusal
from decktalk.events import Level, Unit
from decktalk.findings import Code, Finding, Location, judge
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.inputs.script import ScriptSection
from decktalk.logs import cache_decision
from decktalk.machine.run import Run
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Cost, NarrateResult, SectionTake, TakeOutcome
from decktalk.speech import Speech, SpeechProvider
from decktalk.stages import selects
from decktalk.stages.narrate.align import Unaligned, before_narrate, time_takes
from decktalk.stages.narrate.plan import (
    id_variable,
    no_voice_note,
    placeholder_inputs,
    refuse_dropped_pauses,
    speech_provider,
    voice_id_of,
)
from decktalk.stages.narrate.script_rules import (
    ascending,
    check_script,
    script_refusals,
    shown,
    symbol_tokens,
)
from decktalk.stages.narrate.state import (
    REPLACED,
    NarratePlan,
    SectionTakeState,
    TakePlan,
    TakeStates,
    retake_turns,
    sections_off_their_audio,
    take_states,
)
from decktalk.stages.narrate.takes import (
    charge_take,
    cut_words,
    gap_after,
    join_takes,
    place,
    take_row,
    write_placeholder_take,
)
from decktalk.stages.pool import Halt, Pool, nothing_to_open

log = logging.getLogger(__name__)

UNREACHED = "its voice could not be reached"
"""Why a section a free voice would have made plays a placeholder, when nothing answered for that voice."""


def narrate(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    force: bool = False,
    replace_voiced: bool = False,
    turns: Sequence[tuple[int, int]] = (),
    takes: Sequence[str] = (),
) -> NarrateResult:
    """Speak every turn of every targeted section of the script, and time every word in it.

    Whether the run may buy is the run's own and never a parameter here, so one gate decides it for
    the library, the command line and a service alike. A turn whose voice the run may buy from, or
    whose named voice bills nothing, gets each missing take, and nothing is sent until `run.approve` has
    seen the price. A turn whose voice bills while the run may not buy plays its take on disk, or a
    placeholder with a `TAKE_MISSING` finding. `force` makes each placeholder again and never buys a
    take. `replace_voiced` makes every turn of the targeted sections again, and `turns` and `takes`
    name the turns to make again, by number and by the take they play, as `retake_turns` reads them:
    the run then targets those turns' sections, voices those turns alone and keeps every other take. A
    voiced take made again is set aside as a rejected take, never deleted. A provider is built only once
    a take of it must be made, and the index is written again after every take, so a run that is stopped
    keeps everything it has already paid for.

    A run stopped or refused once its takes are under way carries what it made as the refusal's
    `result`: every take finished so far, and the cost of those it voiced. An interrupt becomes
    `Cancelled` for that, so a caller meets a stopped narrate as it meets a cancelled one.
    """
    retake = turns_to_retake(inputs, only, replace_voiced=replace_voiced, turns=turns, takes=takes)
    targets = _targets(inputs, sorted({key.section for key in retake}) if retake else only)
    previous = inputs.takes()
    moved = sections_off_their_audio(inputs, previous, targets)
    targets = sorted([*targets, *moved], key=lambda section: section.number)
    # A take is made again only when the run was told to replace it. `force` never feeds this, because
    # it rebuilds what DeckTalk makes itself, and a voice's take is not.
    states = take_states(
        inputs,
        targets,
        replace_voiced=replace_voiced,
        retake=retake,
        reread=[section.number for section in moved],
    )
    plan = states.plan(spend=run.spend, force=force)
    _refuse_or_note_unnamed(inputs, run, plan, previous)
    missing = [
        _take_missing(inputs, take, take.reason, takes_dir_gone=_gone(states, take))
        for take in plan.takes
        if take.digest is not None and is_placeholder(take.digest)
    ]
    _keep_unselected(inputs, run, previous, targets)
    providers = _providers(inputs, run, plan)
    progress = _Progress(rows={take.turn_key: take for take in previous.sections} if previous is not None else {})
    try:
        rows, made, unreached = _write_takes(inputs, run, plan.takes, providers, progress)
        rows, unaligned = time_takes(inputs, run, rows, previous, made=set(progress.made), force=force)
        index = _index(inputs, rows)
        cut = cut_words(inputs, index, set(progress.made))
        run.wrote(index.write(inputs.workspace.takes_path))
        run.wrote(join_takes(inputs, index))
    except HALTS as failure:
        refusal = as_refusal(failure)
        so_far = [progress.made[take.key] for take in plan.takes if take.key in progress.made]
        with progress.lock:
            charged, sent = set(progress.charged), progress.sent - progress.charged
        # A request a free voice was sent costs nothing, so only a paid one can have been billed.
        possibly = {take.key for take in plan.takes if take.key in sent and not take.voice.free}
        refusal.result = run.result(
            NarrateResult,
            ok=False,
            error=ErrorInfo.of(refusal),
            spend=run.spend,
            sections=tuple(so_far),
            cost=_spent(states, plan, charged, possibly=possibly),
            takes=inputs.relative(inputs.workspace.takes_path),
        )
        if refusal is failure:
            raise
        raise refusal from failure
    _note_what_is_missing(inputs, run, index)
    _note_unreached(inputs, run, unreached)
    missing += [_take_missing(inputs, take, UNREACHED, unreached=True) for take in unreached]
    missing += unaligned + cut
    for found in sorted(missing, key=lambda found: found.location.section or 0):
        run.found(found)
    return run.result(
        NarrateResult,
        spend=run.spend,
        sections=tuple(made),
        cost=_spent(states, plan, progress.charged),
        takes=inputs.relative(inputs.workspace.takes_path),
    )


def _gone(states: TakeStates, take: TakePlan) -> bool:
    """Whether a planned turn plays a placeholder because the takes directory holding its take moved."""
    section = states.get(take.section.number)
    return section is not None and section.turns[take.turn.number - 1].takes_dir_gone


def _refuse_or_note_unnamed(inputs: Inputs, run: Run, plan: NarratePlan, previous: Takes | None) -> None:
    """Refuse a voiced take whose voice has no id, or say once per such voice why its takes on disk are not matched.

    A voiced take needs its voice named, so the refusal says where to name it. Voiced takes on disk
    cannot be matched without their voice, and a project with no voiced take has nothing to match.
    """
    sending_unnamed = [take for take in plan.takes if take.outcome is TakeOutcome.VOICED and take.digest is None]
    if sending_unnamed:
        voice_id_of(inputs, sending_unnamed[0].voice)
    if previous is not None and previous.voiced_sections:
        voices = {take.voice.name: take.voice for take in plan.takes}
        for voice in voices.values():
            if not voice.named:
                run.note(no_voice_note(voice))


def _providers(inputs: Inputs, run: Run, plan: NarratePlan) -> dict[str, SpeechProvider]:
    """Every provider this run sends a take to, built once each, after the price has been approved.

    A run that sends nothing buys nothing, so the gate is asked and a provider is built only when
    something would be bought from it, and a run that plays what is on disk reads no key at all. Each
    provider is built by the first voice of it that has a take to make.
    """
    providers: dict[str, SpeechProvider] = {}
    sending = [take for take in plan.takes if take.outcome is TakeOutcome.VOICED]
    if not sending:
        return providers
    refuse_dropped_pauses(inputs, [(take.section, take.turn) for take in sending])
    run.approve(plan.cost)
    for take in sending:
        if take.voice.provider not in providers:
            providers[take.voice.provider] = speech_provider(run, inputs, take.voice)
    return providers


def _note_unreached(inputs: Inputs, run: Run, unreached: Sequence[TakePlan]) -> None:
    """Say, once per voice, which sections play a placeholder because their free voice could not be reached."""
    down: dict[str, list[int]] = defaultdict(list)
    for take in unreached:
        if take.section.number not in down[take.voice.name]:
            down[take.voice.name].append(take.section.number)
    for name, numbers in down.items():
        run.note(
            f"The voice {name} could not be reached, so its turns in section(s) {numbers} play a placeholder. "
            f"{inputs.voices[name].start_hint}, then run decktalk narrate again.",
            level=Level.WARNING,
        )


def _spent(
    states: TakeStates, plan: NarratePlan, charged: Collection[TurnKey], *, possibly: Collection[TurnKey] = ()
) -> Cost:
    """What a run of this plan reports as its cost: the turns whose charge ran, with the turns in `possibly` in its
    ceiling, when it voices any, and its price otherwise."""
    return states.charged(plan, charged, possibly) if plan.voiced else plan.cost


def price(
    inputs: Inputs,
    *,
    only: Sequence[int] | None = None,
    replace_voiced: bool = False,
    turns: Sequence[tuple[int, int]] = (),
    takes: Sequence[str] = (),
) -> Cost:
    """What a run of this stage that may spend would buy, planned the way `narrate` plans it and sending nothing.

    No run is opened and no voice is built, so a build prices its takes before it buys anything. A
    run told to replace its voiced takes is priced at every take it targets, and one told to make
    named turns again at those turns alone.
    """
    retake = turns_to_retake(inputs, only, replace_voiced=replace_voiced, turns=turns, takes=takes)
    targets = _targets(inputs, sorted({key.section for key in retake}) if retake else only)
    return take_states(inputs, targets, replace_voiced=replace_voiced, retake=retake).plan(spend=True).cost


def turns_to_retake(
    inputs: Inputs,
    only: Sequence[int] | None,
    *,
    replace_voiced: bool,
    turns: Sequence[tuple[int, int]],
    takes: Sequence[str],
) -> tuple[TurnKey, ...]:
    """The turns this run was told to make again, refused beside a selection of sections or `replace_voiced`.

    Naming a turn names its section, so a section selection beside it would be a second answer to one
    question, and `replace_voiced` would make every other turn of those sections again as well.
    """
    if not turns and not takes:
        return ()
    if only is not None or replace_voiced:
        given = "--section" if only is not None else "--replace-voiced"
        raise InputError(
            f"--turn and --take name the turns to voice again and their sections, so {given} beside them would "
            "name a second set, and nothing was voiced.",
            hint=f"Leave out {given}, or name every turn to voice again with --turn.",
            location=at(inputs.script_path, inputs.root),
        )
    return retake_turns(inputs, turns, takes)


def _take_missing(
    inputs: Inputs, take: TakePlan, why: str, *, unreached: bool = False, takes_dir_gone: bool = False
) -> Finding:
    """The finding for one turn that plays a placeholder, saying why its take did not play and what makes it.

    What makes the take is the bill of the voice that reads it: a voice that bills is bought from with
    `--spend`, and a free one that could not be reached is started. A voice with no id is named first.
    A takes directory that is gone is pointed at first, because the takes in it are already made. A
    section of several turns names the turn and its voice.
    """
    number, voice = take.section.number, take.voice
    if unreached:
        action = f"{voice.start_hint}, then run decktalk narrate --section {number}."
    elif voice.free:
        # A free voice is called whenever it is named, so a free turn plays a placeholder only for want of a name.
        action = (
            f"Name the voice {voice.name} in [voices.{voice.name}] id, then run decktalk narrate --section {number} "
            "to make its take for nothing."
        )
    elif not voice.named:
        action = (
            f"Name the voice {voice.name} in [voices.{voice.name}] id, then run decktalk narrate --section {number} "
            "--spend to buy its take."
        )
    else:
        action = f"Run decktalk narrate --section {number} --spend to buy its take."
    if takes_dir_gone:
        action = f"Point [narration] takes_dir at the folder that holds them, or {action[0].lower()}{action[1:]}"
    turn = "" if len(take.section.turns) == 1 else f" for turn {take.turn.number}, which {voice.name} reads"
    return judge(
        Code.TAKE_MISSING,
        f"Section {number} plays a placeholder{turn}, because {why}. {action}",
        Location(where=f"section {number}", file=inputs.relative(inputs.script_path), section=number),
        stage=Stage.NARRATE,
    )


def _targets(inputs: Inputs, only: Sequence[int] | None) -> list[ScriptSection]:
    """The spoken sections this run works on, with the whole script checked before any of them."""
    sections = inputs.script()
    out_of_order = ascending(sections)
    if out_of_order is not None:
        first, second = out_of_order
        raise InputError(
            f"the script heading for section {second.number} comes after section {first.number}, "
            "so the sections do not ascend and the narration would be joined in an order nothing else agrees with.",
            hint=f"Move '## {second.number}. {second.title}' after '## {first.number}. {first.title}'.",
            location=at(inputs.script_path, inputs.root),
        )
    where = inputs.relative(inputs.script_path).as_posix()
    check_script(where, inputs.script_path.read_text(encoding="utf-8"), tuple(inputs.voices))
    wanted = selects(only)
    spoken = [section for section in inputs.spoken() if wanted(section.number)]
    if not spoken:
        every = [section.number for section in inputs.spoken()]
        raise InputError(
            f"no spoken section matches {list(only or ())}, so there is nothing to narrate.",
            hint=f"The spoken sections are {every}.",
            location=at(inputs.script_path, inputs.root),
        )
    return spoken


def _write_takes(
    inputs: Inputs,
    run: Run,
    plans: Sequence[TakePlan],
    providers: Mapping[str, SpeechProvider],
    progress: _Progress,
) -> tuple[dict[TurnKey, Take], list[SectionTake], list[TakePlan]]:
    """Make every take this run plans, one per turn, `[narration] concurrency` at a time, checkpointing after each.

    The takes a plan found on disk are indexed after the pool has finished, because a turn kept for
    sharing another turn's take reads the take that turn is still making. A free voice that nothing
    answers is a voice that is not running, so each turn it would have made plays a placeholder and is
    given back last, and no turn after the first asks that provider again. A voice that bills is not
    stood in for, because a take the author paid to buy is not optional. `providers` holds each provider
    this run built, by its name. `progress` starts from the rows of the take index this run started
    from, which it rewrites, and holds every take made so far, which is what a run stopped on the way
    reports.
    """
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    inputs.workspace.takes_path.parent.mkdir(parents=True, exist_ok=True)
    chapters = inputs.chapters()
    planned = {plan.key: plan for plan in plans}

    def one(plan: TakePlan) -> None:
        number = plan.section.number
        chapter = chapters.get(number, plan.section.title)
        with run.section(Stage.NARRATE, number, turn=plan.turn.number) as ending:
            down = progress.down[plan.voice.provider] if plan.voice.free else None
            try:
                provider = providers.get(plan.voice.provider)
                row, outcome = _one_take(inputs, run, plan, chapter, provider, progress, down=down)
            except ProviderError as failure:
                if failure.possibly_charged:
                    _possibly_charged(inputs, run, plan)
                raise
            if outcome is TakeOutcome.KEPT:
                # A take found on disk did not run, so its turn ends as kept, as its row says.
                ending.outcome = Outcome.KEPT
            made = SectionTake(
                section=number,
                key=plan.section.key,
                turn=plan.turn.number,
                voice=plan.voice.name,
                outcome=outcome,
                characters=plan.characters_sent,
                seconds=row.duration_seconds,
                file=inputs.relative(inputs.take_places.find(row.digest).audio),
                digest=row.digest,
            )
            # The count is reported under the lock that raised it, so a reader of the stream sees one,
            # two, three in that order however the pool's workers finish.
            with progress.lock:
                progress.rows[plan.key] = row
                progress.made[plan.key] = made
                _index(inputs, _placed(inputs, progress.rows, set(progress.made), planned)).write(
                    inputs.workspace.takes_path
                )
                progress.done += 1
                run.progress(
                    Stage.NARRATE,
                    done=progress.done,
                    total=len(plans),
                    unit=Unit.TAKE,
                    label=chapter,
                    section=number,
                )

    making = {plan.key: plan for plan in plans if plan.outcome is not TakeOutcome.KEPT}
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

    def take(_nothing: None, key: TurnKey, _halt: Halt) -> None:
        # A take never checks the halt once it has started, because its request is paid for once it is
        # sent, so a failure, a cancel or an interrupt starts no queued take and lets every take in
        # flight finish and be indexed.
        one(making[key])

    with Pool(list(making), workers, nothing_to_open, take, run.cancel) as pool:
        for key in making:
            pool.result(key)
    for plan in plans:
        if plan.outcome is TakeOutcome.KEPT:
            one(plan)
    made = [progress.made[plan.key] for plan in plans]
    unreached = [
        plan
        for plan in plans
        if plan.outcome is not TakeOutcome.PLACEHOLDER and progress.made[plan.key].outcome is TakeOutcome.PLACEHOLDER
    ]
    return _placed(inputs, progress.rows, set(progress.made), planned), made, unreached


def _possibly_charged(inputs: Inputs, run: Run, plan: TakePlan) -> None:
    """Put a take whose reply broke after its request was sent on the stream, flagged as possibly charged."""
    if plan.buy is None or plan.digest is None:
        return
    charge_take(inputs, run, plan.section, plan.turn, plan.digest, plan.buy, possibly=True)


@dataclass
class _Progress:
    """What the workers of one narrate share, which every one of them changes only under its lock."""

    rows: dict[TurnKey, Take]
    made: dict[TurnKey, SectionTake] = field(default_factory=dict)
    sent: set[TurnKey] = field(default_factory=set)
    """The turns whose request went to its voice and was not refused outright, which the provider may bill."""
    charged: set[TurnKey] = field(default_factory=set)
    """The turns whose voice answered and whose charge ran, which the provider was paid for."""
    done: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    down: defaultdict[str, threading.Event] = field(default_factory=lambda: defaultdict(threading.Event))
    """Set, by provider, once a free voice did not answer, so no turn after it waits on that provider again."""


def _one_take(
    inputs: Inputs,
    run: Run,
    plan: TakePlan,
    chapter: str,
    provider: SpeechProvider | None,
    progress: _Progress,
    *,
    down: threading.Event | None,
) -> tuple[Take, TakeOutcome]:
    """One turn's take, made or found, with what this run did about it.

    `down` is given for a free voice alone. Once it is set, or once this turn's request finds
    nothing answering, the turn plays its placeholder instead of failing the run.
    """
    digest = plan.digest
    if digest is None:
        raise InputError(
            f"section {plan.section.number} has no take digest, so the voice could not be set up.",
            hint=(
                f"Set [voices.{plan.voice.name}] id in decktalk.toml or export {id_variable(plan.voice.name)}, or "
                "run with --no-spend."
            ),
            location=at(inputs.workspace.takes_path, inputs.root),
        )
    kept = plan.outcome is TakeOutcome.KEPT
    hit = kept and inputs.take_places.find(digest).held
    # The plan's reason is the sentence `status` prints, and the token is what a reader filters on.
    why = "unchanged" if hit else "take-missing" if kept else "to-make"
    cache_decision(
        log, Unit.TAKE.value, hit=hit, why=why, key=digest, section=plan.section.number, reason=plan.reason or None
    )
    if hit:
        inputs.take_places.keep(digest, run, section=plan.section.number)
        return take_row(inputs, plan.section, plan.turn, chapter, digest), TakeOutcome.KEPT
    if down is not None and down.is_set() and plan.outcome is not TakeOutcome.PLACEHOLDER:
        # A take the voice would make, or one another turn was making, cannot come from a voice that is down.
        return _stand_in(inputs, run, plan, chapter, progress)
    if plan.outcome is TakeOutcome.VOICED:
        return _buy(inputs, run, plan, chapter, digest, provider, progress, down=down)
    row, files = write_placeholder_take(inputs, plan.section, plan.turn, chapter, digest)
    for path in files:
        run.wrote(path)
    return row, TakeOutcome.PLACEHOLDER


def _buy(
    inputs: Inputs,
    run: Run,
    plan: TakePlan,
    chapter: str,
    digest: str,
    provider: SpeechProvider | None,
    progress: _Progress,
    *,
    down: threading.Event | None,
) -> tuple[Take, TakeOutcome]:
    """Voice one turn's take through the take places, or play the copy another run voiced meanwhile.

    The turn is marked sent as its request goes out and charged once its charge runs, which is what a
    run stopped on the way counts its cost from. A request refused outright was not billed, so it is
    unmarked again, and one whose reply broke after sending stays sent.
    """
    request, buy = plan.request, plan.buy
    if provider is None or request is None or buy is None:
        raise InputError(
            f"section {plan.section.number} would be voiced and this run has no request for it.",
            hint="Run `decktalk narrate` again, or run with --no-spend.",
            location=at(inputs.workspace.takes_path, inputs.root),
        )
    voice, ask = provider, request
    key = plan.key

    def speak() -> Speech:
        with progress.lock:
            progress.sent.add(key)
        try:
            return voice.speak(ask)
        except ProviderError as failure:
            if not failure.possibly_charged:
                with progress.lock:
                    progress.sent.discard(key)
            raise

    def charge() -> None:
        # The voice answered, so it is paid: the take is counted before its line is written, so a stop
        # or a failure between the two never leaves a paid take in the ceiling alone.
        with progress.lock:
            progress.charged.add(key)
        charge_take(inputs, run, plan.section, plan.turn, digest, buy)

    try:
        outcome = inputs.take_places.voice(
            digest,
            speak,
            charge,
            run,
            section=plan.section.number,
            replacing=plan.reason == REPLACED,
            suffix=plan.voice.output.suffix,
        )
    except ProviderError as failure:
        if down is None or failure.reached:
            raise
        down.set()
        return _stand_in(inputs, run, plan, chapter, progress)
    return take_row(inputs, plan.section, plan.turn, chapter, digest), outcome


def _stand_in(inputs: Inputs, run: Run, plan: TakePlan, chapter: str, progress: _Progress) -> tuple[Take, TakeOutcome]:
    """The placeholder one turn plays because its free voice did not answer, found on disk or made now."""
    digest = placeholder_inputs(inputs, plan.turn).digest
    held = inputs.take_places.find(digest).held
    outcome = TakeOutcome.KEPT if held else TakeOutcome.PLACEHOLDER
    stand_in = replace(plan, outcome=outcome, digest=digest, request=None, buy=None)
    row, _kept = _one_take(inputs, run, stand_in, chapter, None, progress, down=None)
    return row, TakeOutcome.PLACEHOLDER


def _keep_unselected(inputs: Inputs, run: Run, previous: Takes | None, targets: Sequence[ScriptSection]) -> None:
    """Keep every take a section this run did not select plays into the takes directory, before anything is bought.

    The narration is joined from the takes directory alone, so a take only the take store holds is
    copied in, a take whose every copy is damaged is refused naming its section, and a take no place
    holds is left for the join to report. Keeping never buys, so a run that does not spend keeps too.
    """
    if previous is None:
        return
    selected = {section.number for section in targets}
    spoken = {section.number for section in inputs.spoken()}
    for row in previous.sections:
        if row.section in spoken and row.section not in selected:
            inputs.take_places.keep(row.digest, run, section=row.section)


def _placed(
    inputs: Inputs, rows: Mapping[TurnKey, Take], touched: Collection[TurnKey], planned: Mapping[TurnKey, TakePlan]
) -> dict[TurnKey, Take]:
    """Every row of the index this run can stand behind, each placed by the one rule.

    A section this run made a take of keeps, in turn order up to the first it cannot stand behind, each
    turn it made and each turn whose row from the index it started from plays the take `planned` for
    that turn, placed on its turn of the script. So the index never names a turn the script no longer
    has or skips one, and a run stopped while it remakes one turn keeps every other turn it holds. A
    section none of whose turns this run made keeps the rows it had, its first taking the section's
    lead, its last the section's tail and every other the gap it already held. A section the script no
    longer speaks keeps none.
    """
    spoken = {section.number for section in inputs.spoken()}
    making = {key.section for key in touched}
    out: dict[TurnKey, Take] = {}
    for number in sorted({key.section for key in rows} & spoken):
        held = sorted((row for key, row in rows.items() if key.section == number), key=lambda row: row.turn)
        if number in making:
            for row in held:
                plan = planned.get(row.turn_key)
                standing = row.turn_key in touched or (plan is not None and plan.digest == row.digest)
                if not standing or (row.turn > 1 and TurnKey(number, row.turn - 1) not in out):
                    break
                if row.turn_key not in touched and plan is not None:
                    row = place(inputs, row, gap=gap_after(inputs, plan.section, plan.turn))
                out[row.turn_key] = row
            continue
        for place_at, row in enumerate(held):
            last = place_at == len(held) - 1
            out[row.turn_key] = place(inputs, row, gap=None if last else row.tail_seconds)
    return out


def _index(inputs: Inputs, rows: Mapping[TurnKey, Take]) -> Takes:
    """The take index, in section and turn order, which is the one order the narration is joined in.

    Its model and format are the default voice's, and each row names the voice that reads it.
    """
    default = inputs.voices[inputs.default_voice]
    return Takes(
        script=inputs.relative(inputs.script_path).as_posix(),
        model=default.model,
        output_format=default.output.format,
        sections=tuple(rows[key] for key in sorted(rows)),
    )


def _note_what_is_missing(inputs: Inputs, run: Run, index: Takes) -> None:
    """Say which spoken sections still have no take, because the narration covers the rest alone."""
    have = set(index.numbers)
    missing = [section.number for section in inputs.spoken() if section.number not in have]
    if missing:
        run.note(
            f"Section(s) {missing} have no take yet, so the narration covers the rest alone.",
            level=Level.WARNING,
        )


__all__ = [
    "NarratePlan",
    "SectionTakeState",
    "TakePlan",
    "TakeStates",
    "Unaligned",
    "before_narrate",
    "narrate",
    "place",
    "price",
    "retake_turns",
    "script_refusals",
    "shown",
    "symbol_tokens",
    "take_states",
    "turns_to_retake",
]
