"""Stage one: `script.md` becomes one take per section, indexed by input digest.

    takes/<digest>.<suffix>            one take, named by the content that produced it, under the
                                     suffix its voice declares for what it holds, such as .mp3
    takes/<digest>.words.json          a start and an end for every word in it
    build/narrate/takes.json         which section plays which take, what it cost, and where each
                                     section lands once the takes are joined
    build/narrate/narration.mp3      every take joined, with each section's silence around it

The take a section plays is found by content alone, so inserting a section, renumbering one or
retitling one moves no file and voices nothing, and two sections with the same words share one take.
Every take a voice spoke lives in the takes directory, `[narration] takes_dir`, which is `takes/`
inside the project and which the author commits, so a fresh clone plays them with no key and
deleting `build/` costs nothing. A take is looked for there first and then in the machine's
`[narration] store_dir`, and one found in the second place is copied into the first. A placeholder,
the take index and the joined track stay under `build/narrate/`, because a build makes them again
for nothing.

Every take on disk is played, paid or placeholder, and the voice is built only when a take must be
made, so a run that makes nothing reads no key. Spend gates money and nothing else. A voice that
declares it bills nothing makes every missing take whether or not the run may spend, and a run that
may not spend never calls a voice that bills. Such a run plays a placeholder for each section whose
take is missing: a click track sized at `placeholder_words_per_minute` plus the declared pauses, with
evenly spaced estimated words, so the whole pipeline runs offline. Each such section is one
`TAKE_MISSING` finding, which says why its take did not play in the words the plan found, and names
the command that buys it. A takes directory holding none of the takes the project played before is
said as that, because a renamed or missing folder is not a script edit. A project that has paid for
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
from decktalk.inputs.script import ScriptSection
from decktalk.logs import cache_decision
from decktalk.machine.run import Run
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Cost, CostState, NarrateResult, SectionTake, TakeOutcome
from decktalk.speech import SpeechProvider, is_free, output_of, start_hint
from decktalk.stages import selects, voice_model
from decktalk.stages.narrate.plan import (
    NO_VOICE_NOTE,
    VOICE_ID_VARIABLE,
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
from decktalk.stages.narrate.state import NarratePlan, SectionTakeState, TakePlan, TakeStates, take_states
from decktalk.stages.narrate.takes import (
    buying_alone,
    copy_from_store,
    estimated_words,
    join_takes,
    place,
    take_row,
    write_placeholder_take,
    write_voiced_take,
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
    # A take is made again only when the run was told to replace it. `force` never feeds this, because
    # it rebuilds what DeckTalk makes itself, and a voice's take is not.
    states = take_states(inputs, targets, replace_voiced=replace_voiced)
    plan = states.plan(spend=run.spend, force=force)
    previous = inputs.takes()
    if not plan.voiced and not inputs.settings.voice.id and previous is not None and previous.voiced_sections:
        # Voiced takes are on disk and cannot be matched without the voice, so the run says why once. A
        # project that never bought a take has nothing to match.
        run.note(NO_VOICE_NOTE)
    if any(take.outcome is TakeOutcome.VOICED and take.digest is None for take in plan.takes):
        # A purchase needs the voice named, so this raises the refusal that says where to name it.
        voice_id_of(inputs)
    missing = [
        _take_missing(inputs, take.section, take.reason, takes_dir_gone=states[take.section.number].takes_dir_gone)
        for take in plan.takes
        if take.digest is not None and is_placeholder(take.digest)
    ]
    provider: SpeechProvider | None = None
    sending = [take.section for take in plan.takes if take.outcome is TakeOutcome.VOICED]
    if sending:
        # A run that sends nothing buys nothing, so the gate is asked and the voice is built only when
        # something would be bought, and a run that plays what is on disk reads no key at all.
        refuse_dropped_pauses(inputs, sending, model=model)
        run.approve(plan.cost)
        provider = speech_provider(run, inputs)
    rows, made, unreached = _write_takes(inputs, run, plan.takes, provider, previous, model=model, free=free)
    index = _index(inputs, rows, model=model)
    run.wrote(index.write(inputs.workspace.takes_path))
    run.wrote(join_takes(inputs, index))
    _note_what_is_missing(inputs, run, index)
    if unreached:
        run.note(
            f"The voice could not be reached, so section(s) {[section.number for section in unreached]} play a "
            f"placeholder. {start_hint(inputs.settings.voice.provider)}, then run decktalk narrate again.",
            level=Level.WARNING,
        )
    missing += [_take_missing(inputs, section, UNREACHED, unreached=True) for section in unreached]
    for found in sorted(missing, key=lambda found: found.location.section or 0):
        run.found(found)
    return run.result(
        NarrateResult,
        spend=run.spend,
        sections=tuple(made),
        cost=_charged(plan.cost, made) if plan.voiced else plan.cost,
        takes=inputs.relative(inputs.workspace.takes_path),
    )


def price(inputs: Inputs, *, only: Sequence[int] | None = None, replace_voiced: bool = False) -> Cost:
    """What a run of this stage that may spend would buy, planned the way `narrate` plans it and sending nothing.

    No run is opened and no voice is built, so a build prices its takes before it buys anything. A
    run told to replace its voiced takes is priced at every take it targets.
    """
    return take_states(inputs, _targets(inputs, only), replace_voiced=replace_voiced).plan(spend=True).cost


def _take_missing(
    inputs: Inputs, section: ScriptSection, why: str, *, unreached: bool = False, takes_dir_gone: bool = False
) -> Finding:
    """The finding for one section that plays a placeholder, saying why its take did not play and what makes it.

    What makes the take is the voice's bill: a voice that bills is bought from with `--spend`, and a
    free one that could not be reached is started. A takes directory that is gone is pointed at first,
    because the takes in it are already made.
    """
    number = section.number
    provider = inputs.settings.voice.provider
    if unreached:
        action = f"{start_hint(provider)}, then run decktalk narrate --section {number}."
    elif is_free(provider):
        # A free voice is called whenever a voice is named, so a free section plays a placeholder only for want of one.
        action = f"Name the voice, then run decktalk narrate --section {number} to make its take for nothing."
    else:
        action = f"Run decktalk narrate --section {number} --spend to buy its take."
    if takes_dir_gone:
        action = f"Point [narration] takes_dir at the folder that holds them, or {action[0].lower()}{action[1:]}"
    return judge(
        Code.TAKE_MISSING,
        f"Section {number} plays a placeholder, because {why}. {action}",
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
    check_script(inputs.relative(inputs.script_path).as_posix(), inputs.script_path.read_text(encoding="utf-8"))
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
    provider: SpeechProvider | None,
    previous: Takes | None,
    *,
    model: str,
    free: bool,
) -> tuple[dict[int, Take], list[SectionTake], list[ScriptSection]]:
    """Make every take this run plans, `[narration] concurrency` at a time, checkpointing after each.

    The takes a plan found on disk are indexed after the pool has finished, because a section kept
    for sharing another section's words reads the take that section is still making. A `free` voice
    that nothing answers is a voice that is not running, so each section it would have made plays a
    placeholder and is given back last, and no section after the first asks it again. A voice that
    bills is not stood in for, because a take the author paid to buy is not optional. `previous` is the
    take index this run started from, whose rows it rewrites.
    """
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    inputs.workspace.takes_path.parent.mkdir(parents=True, exist_ok=True)
    chapters = inputs.chapters()
    progress = _Progress(rows={take.section: take for take in previous.sections} if previous is not None else {})

    def one(plan: TakePlan) -> None:
        number = plan.section.number
        chapter = chapters.get(number, plan.section.title)
        with run.section(Stage.NARRATE, number) as ending:
            down = progress.down if free else None
            row, outcome = _one_take(inputs, run, plan, chapter, provider, down, progress.checked)
            if outcome is TakeOutcome.KEPT:
                # A take found on disk did not run, so its section ends as kept, as its row says.
                ending.outcome = Outcome.KEPT
            made = SectionTake(
                section=number,
                key=plan.section.key,
                outcome=outcome,
                characters=plan.characters_sent,
                seconds=row.duration_seconds,
                file=inputs.relative(inputs.workspace.take_path(row.digest)),
                digest=row.digest,
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
                    label=chapter,
                    section=number,
                )

    making = {plan.section.number: plan for plan in plans if plan.outcome is not TakeOutcome.KEPT}
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
        if plan.outcome is TakeOutcome.KEPT:
            one(plan)
    made = [progress.made[plan.section.number] for plan in plans]
    unreached = [
        plan.section
        for plan in plans
        if plan.outcome is not TakeOutcome.PLACEHOLDER
        and progress.made[plan.section.number].outcome is TakeOutcome.PLACEHOLDER
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
    checked: set[str] = field(default_factory=set)
    """Every take whose copies this run has verified, so each is hashed whole once however many sections play it."""


def _one_take(
    inputs: Inputs,
    run: Run,
    plan: TakePlan,
    chapter: str,
    provider: SpeechProvider | None,
    down: threading.Event | None,
    checked: set[str],
) -> tuple[Take, TakeOutcome]:
    """One section's take, made or found, with what this run did about it.

    `down` is given for a free voice alone. Once it is set, or once this section's request finds
    nothing answering, the section plays its placeholder instead of failing the run.
    """
    digest = plan.digest
    if digest is None:
        raise InputError(
            f"section {plan.section.number} has no take digest, so the voice could not be set up.",
            hint=f"Set [voice] id in decktalk.toml or export {VOICE_ID_VARIABLE}, or run with --no-spend.",
            location=at(inputs.workspace.takes_path, inputs.root),
        )
    kept = plan.outcome is TakeOutcome.KEPT
    hit = kept and inputs.workspace.holding(digest) is not None
    # The plan's reason is the sentence `status` prints, and the token is what a reader filters on.
    why = "unchanged" if hit else "take-missing" if kept else "to-make"
    cache_decision(
        log, Unit.TAKE.value, hit=hit, why=why, key=digest, section=plan.section.number, reason=plan.reason or None
    )
    if hit:
        for path in copy_from_store(inputs, run, plan.section.number, digest, checked):
            run.wrote(path)
        voiced = not is_placeholder(digest)
        return take_row(inputs, plan.section, chapter, digest, voiced=voiced), TakeOutcome.KEPT
    if down is not None and down.is_set() and plan.outcome is not TakeOutcome.PLACEHOLDER:
        # A take the voice would make, or one another section was making, cannot come from a voice that is down.
        return _stand_in(inputs, run, plan, chapter)
    if plan.outcome is TakeOutcome.VOICED:
        return _buy(inputs, run, plan, chapter, digest, provider, down, checked)
    row, files = write_placeholder_take(inputs, plan.section, chapter, digest)
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
    down: threading.Event | None,
    checked: set[str],
) -> tuple[Take, TakeOutcome]:
    """Buy one section's take while holding the store's lock on it, or play the copy another run bought meanwhile."""
    request = plan.request
    if provider is None or request is None:
        raise InputError(
            f"section {plan.section.number} would be voiced and this run has no request for it.",
            hint="Run `decktalk narrate` again, or run with --no-spend.",
            location=at(inputs.workspace.takes_path, inputs.root),
        )
    with buying_alone(inputs, run, digest, wait_seconds=inputs.settings.narration.timeout_seconds) as bought:
        if bought:
            # Another project bought this take while this run waited, so it plays that copy.
            for path in copy_from_store(inputs, run, plan.section.number, digest, checked):
                run.wrote(path)
            return take_row(inputs, plan.section, chapter, digest, voiced=True), TakeOutcome.KEPT
        try:
            row, files = write_voiced_take(inputs, run, provider, plan.section, chapter, digest, request)
        except ProviderError as failure:
            if down is None or failure.reached:
                raise
            down.set()
            return _stand_in(inputs, run, plan, chapter)
    for path in files:
        run.wrote(path)
    return row, TakeOutcome.VOICED


def _stand_in(inputs: Inputs, run: Run, plan: TakePlan, chapter: str) -> tuple[Take, TakeOutcome]:
    """The placeholder one section plays because its free voice did not answer, found on disk or made now."""
    digest = placeholder_inputs(inputs, plan.section).digest
    held = inputs.workspace.holding(digest) is not None
    outcome = TakeOutcome.KEPT if held else TakeOutcome.PLACEHOLDER
    stand_in = replace(plan, outcome=outcome, digest=digest, request=None)
    row, _kept = _one_take(inputs, run, stand_in, chapter, None, None, set())
    return row, TakeOutcome.PLACEHOLDER


def _placed(inputs: Inputs, rows: dict[int, Take], touched: set[int]) -> dict[int, Take]:
    """Every row of the index, with the ones this run did not touch placed by the same rule."""
    spoken = {section.number for section in inputs.spoken()}
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
    missing = [section.number for section in inputs.spoken() if section.number not in have]
    if missing:
        run.note(
            f"Section(s) {missing} have no take yet, so the narration covers the rest alone.",
            level=Level.WARNING,
        )


def _charged(estimate: Cost, made: list[SectionTake]) -> Cost:
    """The price the run really paid, which is the estimate once the requests have been sent."""
    voiced = tuple(row.section for row in made if row.outcome is TakeOutcome.VOICED)
    return estimate.model_copy(update={"state": CostState.CHARGED, "sections": voiced})


__all__ = [
    "NarratePlan",
    "SectionTakeState",
    "TakePlan",
    "TakeStates",
    "estimated_words",
    "narrate",
    "place",
    "price",
    "script_refusals",
    "shown",
    "symbol_tokens",
    "take_states",
]
