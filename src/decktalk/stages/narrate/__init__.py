"""Stage one: `script.md` becomes one take per section, indexed by content hash.

    build/narrate/<hash>.mp3         one take, named by the content that produced it
    build/narrate/<hash>.words.json  a start and an end for every word in it
    build/narrate/takes.json         which section plays which take, what it cost, and where each
                                     section lands once the takes are joined
    build/narrate/narration.mp3      every take joined, with each section's silence around it

The take a section plays is found by content alone, so inserting a section, renumbering one or
retitling one moves no file and voices nothing, and two sections with the same words share one take.
`[narration] cache_dir` puts the take files alone outside `build/`, where a fresh clone and a second
worktree find them again. A hash names each one, so many projects may share one such directory,
while the index and the joined track stay under `build/narrate/` because they are one project's.

A run whose voicing is `placeholder` needs no credential: click tracks sized at
`silent_words_per_minute` plus the declared pauses, with evenly spaced estimated words, so the whole
pipeline runs offline. It refuses only the targeted sections that already hold a paid take, so a
project that has paid for eight sections still rehearses its ninth for nothing.

This stage judges nothing. What a script says badly is `check`'s to report, because a judgement
belongs where an author can act on it before any credit is spent, and what the voice must not
receive at all is refused here before a single request is sent.
"""

from __future__ import annotations

from collections.abc import Sequence

from decktalk.artifacts import Take, Takes, is_placeholder
from decktalk.errors import InputError
from decktalk.events import Level, Unit
from decktalk.findings import Location
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.inputs.script import Segment
from decktalk.machine import Run
from decktalk.pipeline import Stage
from decktalk.results import NarrateResult, SectionTake, Spend, SpendState, TakeStatus, Voicing
from decktalk.speech import SpeechProvider
from decktalk.stages import clock, selects, since
from decktalk.stages.narrate.plan import (
    TakePlan,
    is_cached,
    placeholder_plan,
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
    index_cached_take,
    join_takes,
    place,
    planned_words,
    write_placeholder_take,
    write_voiced_take,
)


def narrate(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    force: bool = False,
    replace_voiced: bool = False,
) -> NarrateResult:
    """Speak every targeted section of the script, and time every word in it.

    Whether the run spends is the run's own voicing and never a parameter here, so one gate decides
    it for the library, the command line and a service alike. Nothing is bought until `run.approve`
    has seen the price, and the index is written again after every take, so a run that is stopped
    keeps everything it has already paid for.
    """
    started = clock()
    cfg = inputs.settings.narration
    targets = _targets(inputs, only)
    model = inputs.document.voice.model or cfg.model
    paid = run.voice is Voicing.PAID
    previous = inputs.takes()
    provider: SpeechProvider | None = None
    if paid:
        provider = speech_provider(inputs)
        plans, _why = voiced_plan(inputs, targets, model=model, voice_id=voice_id_of(inputs), force=force)
    else:
        _refuse_over_paid(inputs, previous, targets, replace_voiced=replace_voiced)
        plans = placeholder_plan(inputs, targets, force=force)
    estimate = spend_of(plans, inputs, state=SpendState.ESTIMATE)
    sending = [plan for plan in plans if plan.status is TakeStatus.VOICED]
    if paid and sending:
        # A run that sends nothing buys nothing, so the gate is asked only when something would be
        # bought and no ceiling can refuse a run that costs nothing.
        run.approve(estimate)
    rows, made = _write_takes(inputs, run, plans, provider, model=model, paid=paid)
    index = _index(inputs, rows, model=model)
    run.wrote(index.write(inputs.workspace.takes_path))
    run.wrote(join_takes(inputs, index))
    _note_what_is_missing(inputs, run, index)
    return run.result(
        NarrateResult,
        voice=run.voice,
        sections=tuple(made),
        spend=_charged(estimate, made) if paid else estimate,
        takes=inputs.relative(inputs.workspace.takes_path),
        seconds=since(started),
    )


def _targets(inputs: Inputs, only: Sequence[int] | None) -> list[Segment]:
    """The spoken sections this run works on, with the whole script checked before any of them.

    The script is read against three rules before a plan exists: every heading has a section, the
    headings ascend, and the spoken text holds nothing the voice would read out. The second is here
    because the take index is the one order the narration is joined in, so a script that counts
    backwards would place its takes in an order no other reading of the project agrees with.
    """
    segments = inputs.script()
    out_of_order = ascending(segments)
    if out_of_order is not None:
        first, second = out_of_order
        raise InputError(
            f"the script heading for section {second.index} comes after section {first.index}, "
            "so the sections do not ascend and the narration would be joined in an order nothing else agrees with.",
            hint=f"Move '## {second.index}. {second.title}' after '## {first.index}. {first.title}'.",
            location=_at_script(inputs),
        )
    check_script(inputs.relative(inputs.script_path).as_posix(), inputs.script_path.read_text(encoding="utf-8"))
    wanted = selects(only)
    spoken = [segment for segment in inputs.spoken() if wanted(segment.index)]
    if not spoken:
        every = [segment.index for segment in inputs.spoken()]
        raise InputError(
            f"no spoken section matches {list(only or ())}, so there is nothing to narrate.",
            hint=f"The spoken sections are {every}.",
            location=_at_script(inputs),
        )
    return spoken


def _at_script(inputs: Inputs) -> Location:
    """Where a refusal about the script points, which is the script itself."""
    return at(inputs.script_path, inputs.root)


def _refuse_over_paid(inputs: Inputs, previous: Takes | None, targets: list[Segment], *, replace_voiced: bool) -> None:
    """Refuse a run without voice that would drop a paid take from the index, unless it was asked to.

    Only the targeted sections are weighed, so a run over the sections nobody has paid for is the
    cheap rehearsal, and a project that has paid for everything is the only one that stops. The
    advice names the free sections, because a run aimed at one paid section must still be told what
    it could have rehearsed instead.
    """
    if replace_voiced or previous is None:
        return
    numbers = {segment.index for segment in targets}
    paid = sorted(take.section for take in previous.sections if take.voiced and take.section in numbers)
    if not paid:
        return
    voiced = set(previous.voiced)
    free = sorted(segment.index for segment in inputs.spoken() if segment.index not in voiced)
    advice = (
        f"Rehearse the sections nobody has paid for: {' '.join(f'--section {number}' for number in free)}."
        if free
        else "Every section of this project is voiced already, so rehearse in a copy of it."
    )
    raise InputError(
        f"the take index holds paid takes for section(s) {', '.join(str(number) for number in paid)}, and a run "
        "without voice would replace them, so the next voiced build would buy all of them again.",
        hint=f"{advice} Pass --replace-voiced to replace them anyway.",
        location=_at_takes(inputs),
    )


def _at_takes(inputs: Inputs) -> Location:
    """Where a refusal about the take index points, which is the index itself."""
    return at(inputs.workspace.takes_path, inputs.root)


def _write_takes(
    inputs: Inputs,
    run: Run,
    plans: list[TakePlan],
    provider: SpeechProvider | None,
    *,
    model: str,
    paid: bool,
) -> tuple[dict[int, Take], list[SectionTake]]:
    """Make every take this run plans, checkpointing the index after each one.

    The index is written again after every take, paid or not, so a run that is stopped halfway keeps
    every take it has already bought and the next run finds them in the cache rather than buying
    them twice.
    """
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    previous = inputs.takes()
    rows: dict[int, Take] = {take.section: take for take in previous.sections} if previous is not None else {}
    touched: set[int] = set()
    made: list[SectionTake] = []
    for done, plan in enumerate(plans, start=1):
        number = plan.segment.index
        with run.section(Stage.NARRATE, number):
            row, status = _one_take(inputs, run, plan, provider, paid=paid)
            rows[number] = row
            touched.add(number)
            made.append(
                SectionTake(
                    section=number,
                    key=plan.segment.key,
                    status=status,
                    characters=plan.characters_sent,
                    seconds=row.duration_seconds,
                    file=inputs.relative(inputs.workspace.takes_dir / row.file),
                    hash=row.hash,
                )
            )
            _index(inputs, _placed(inputs, rows, touched), model=model).write(inputs.workspace.takes_path)
        run.progress(
            Stage.NARRATE,
            done=done,
            total=len(plans),
            unit=Unit.TAKE,
            label=plan.chapter or plan.segment.title,
            section=number,
        )
    return _placed(inputs, rows, touched), made


def _one_take(
    inputs: Inputs, run: Run, plan: TakePlan, provider: SpeechProvider | None, *, paid: bool
) -> tuple[Take, TakeStatus]:
    """One section's take, made or found, with what this run did about it."""
    digest = plan.digest
    if digest is None:
        raise InputError(
            f"section {plan.segment.index} has no take digest, so the voice could not be set up.",
            hint="Set ELEVENLABS_API_KEY in .env, or run without voice.",
            location=_at_takes(inputs),
        )
    if plan.cached and is_cached(digest, inputs.workspace.takes_dir):
        voiced = not is_placeholder(digest)
        return index_cached_take(inputs, plan.segment, plan.chapter, digest, voiced=voiced), TakeStatus.KEPT
    if paid:
        if provider is None or plan.request is None:
            raise InputError(
                f"section {plan.segment.index} would be voiced and this run has no request for it.",
                hint="Run `decktalk narrate` again, or run without voice.",
                location=_at_takes(inputs),
            )
        row, files = write_voiced_take(inputs, provider, plan.segment, plan.chapter, digest, plan.request)
        status = TakeStatus.VOICED
    else:
        row, files = write_placeholder_take(inputs, plan.segment, plan.chapter, digest)
        status = TakeStatus.PLACEHOLDER
    for path in files:
        run.wrote(path)
    return row, status


def _placed(inputs: Inputs, rows: dict[int, Take], touched: set[int]) -> dict[int, Take]:
    """Every row of the index, with the ones this run did not touch placed by the same rule.

    A section a selection left out keeps the take it already has, and is placed by its own settings
    like every other, so its lead and its tail follow the project whichever sections this run made.
    """
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
        output_format=inputs.settings.narration.output_format,
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
    "script_refusals",
    "shown",
    "spend_of",
    "symbol_tokens",
    "voiced_plan",
]
