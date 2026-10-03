"""The whole pipeline in order, or the span of it one run asked for.

`build` runs the six stages narrate, cue, record, score, assemble and verify, in that order,
and reports each one on the event stream as it opens and closes. The run's account of itself is the
stream, its lines are appended to `build/events/<run>.jsonl` by the machine's own sink, and every
file the stages wrote is already recorded on the run. The one file a build writes itself is the
record `status` keeps of the last assemble and verify, so the next build can keep them.

Which stages a run performs is read from `PIPELINE`, and how each is called from `CALLS`, and
neither is worked out here. `Stage.span` gives the run of stages between two ends, `required` gives
the artifacts that run reads but does not write, and `Artifact.next_step` names the stage that would
have written each one, so a run that starts past a missing artifact is refused with the file and the
command named, and this module carries no "run this first" sentence of its own. Whether an artifact
is built is `status`'s rule, asked of `status`, so a build never goes ahead on a directory the
report calls unfinished.

An unchanged build keeps `assemble` and `verify` rather than repeating them. Both are pure
functions of files already on disk, so when nothing they read has moved since the last build ran
them, the film on disk is the one they would make and the measurement is the one they would take.
The stage is reported as kept, its findings are reported again, and `force` runs it anyway.

A run that may spend draws the storyboard before it narrates, because the contact sheet is the
checkpoint a person reads before any credit is bought, and a run that buys nothing has nothing to check.
A run with a ceiling is then priced whole, every stage that buys added together, so a build whose
takes and sounds together pass `--max-cost` is refused before it buys anything.

A stage whose findings reach the caller's threshold stops the run, and the run still returns its
result. A finding is a judgement and not an error, so the stages that ran, the findings they made and
the money narrate already spent reach the caller as fields it can read, and `stopped_at` names the
stage the run stopped after.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from decktalk.errors import InputError, NotBuiltError
from decktalk.events import Level, StageDone
from decktalk.findings import Code, Finding, Severity
from decktalk.inputs import Inputs
from decktalk.logs import cache_decision
from decktalk.machine.run import Run, Threshold
from decktalk.pipeline import Artifact, Outcome, Stage, downstream, required
from decktalk.results import DOLLAR_DIGITS, BillingBasis, BuildResult, Cost, CostState, Layer, Result, StageRun, counted
from decktalk.stages import narrate, storyboard
from decktalk.stages import score as score_stage
from decktalk.stages.kept import (
    BUILT,
    Kept,
    KeptStage,
    assemble_digest,
    assembled,
    holds_film,
    intact,
    outputs_of,
    read_kept,
    verify_digest,
)
from decktalk.stages.table import CALLS

log = logging.getLogger(__name__)


def build(
    inputs: Inputs,
    run: Run,
    *,
    stages: Sequence[Stage] | None = None,
    skip: Sequence[Stage] = (),
    only: Sequence[int] | None = None,
    force: bool = False,
    replace_voiced: bool = False,
    replace_score: bool = False,
    loudness: bool = True,
    strict: bool = False,
    allow: Collection[Code] = (),
    stop_on: Severity | None = Severity.ERROR,
) -> BuildResult:
    """Run every stage of the pipeline, or the span of them `stages` names, in run order.

    Whether the run may spend is the run's own rather than a parameter, so one gate decides it for
    the library, the command line and a service alike, and the storyboard is drawn first when it
    may. A stage that judges something at the `stop_on` threshold stops the run, because a cue whose
    phrase is never spoken leaves a slide that never appears and a page that threw recorded an empty
    stage, and carrying on would deliver a film that is wrong in a way the run already knows about.

    `allow` and `stop_on` are the caller's own threshold, which is what `--allow` and `--fail-on`
    set on the command line. A code in `allow` never stops the run, `Severity.ERROR` stops on a
    error, `Severity.WARNING` stops on any finding, and None lets every stage run so
    that `verify` measures what the earlier stages made. The stages after a stop are reported as
    skipped and the result names the stage in `stopped_at`. The result's `ok` is read from the same
    threshold, so it is false on a run that stopped and on one whose verify judged something the
    threshold fails on, and true on a run whose every finding was allowed or under the line.

    Whether the film carries the score is read from `skip`, because a run told to leave the
    stage out is a run that does not want its sound, and a second switch for the same decision would
    let a caller skip the stage and still be refused for the file it never asked for.
    """
    plan = _plan(stages, skip)
    threshold = Threshold(stop_on=stop_on, allow=frozenset(allow))
    score = Stage.SCORE not in skip
    _require_what_the_plan_skips(inputs, plan, score=score)
    options: dict[str, object] = {
        "only": only,
        "force": force,
        "replace_voiced": replace_voiced,
        "replace_score": replace_score,
        "score": score,
        "loudness": loudness,
        "strict": strict,
    }
    board = _storyboard(inputs, run, only=only)
    _hold_to_ceiling(inputs, run, plan, only=only, replace_voiced=replace_voiced, replace_score=replace_score)
    kept = read_kept(inputs)
    fresh: dict[Stage, KeptStage] = {}
    rows: list[StageRun] = []
    spends: list[Cost] = []
    film: Path | None = None
    stopped_at: Stage | None = None
    for stage in Stage:
        if stage not in plan or stopped_at is not None:
            rows.append(_skipped(run, stage))
            continue
        run.check()
        opened = time.monotonic()
        taken = {name: options[name] for name in CALLS[stage].options}
        digest = _digest(stage, inputs, kept, fresh, taken)
        standing = None
        if stage in KEEPS:
            standing, why = (None, FORCED) if force else _standing(stage, inputs, kept, digest)
            cache_decision(log, stage.value, hit=standing is not None, why=why, key=digest)
        if standing is not None:
            findings = _keep(stage, run, standing)
            rows.append(StageRun(stage=stage, outcome=Outcome.KEPT, elapsed_seconds=time.monotonic() - opened))
            fresh[stage] = standing
            if stage is Stage.ASSEMBLE:
                film = inputs.relative(inputs.workspace.film)
        else:
            with run.stage(stage, index=plan.index(stage) + 1, count=len(plan)):
                answer = CALLS[stage].call(inputs, run, **taken)
            rows.append(StageRun(stage=stage, outcome=Outcome.RAN, elapsed_seconds=time.monotonic() - opened))
            spends += _cost_of(answer)
            findings = [found for found in answer.findings if found.stage is stage]
            if digest is not None:
                fresh[stage] = _remember(stage, inputs, digest, taken, findings)
            if stage is Stage.ASSEMBLE:
                film = _film_of(answer)
        if stage is not Stage.VERIFY and _stopped(stage, findings, run, plan, threshold):
            stopped_at = stage
    if fresh:
        run.wrote(_kept_after(kept, fresh).write(inputs.workspace.kept_path))
    return run.result(
        BuildResult,
        threshold=threshold,
        stages=tuple(rows),
        spend=run.spend,
        cost=total(spends) if spends else narrate.cost_of([], inputs, state=CostState.ESTIMATE),
        film=film,
        storyboard=board,
        stopped_at=stopped_at,
    )


OPTIONS_JSON = TypeAdapter(dict[str, JsonValue])
"""The reader that turns a stage's options into the JSON the record holds, and refuses anything else."""

KEEPS: dict[Stage, str] = {Stage.ASSEMBLE: "assemble", Stage.VERIFY: "verify"}
"""The stages a build can keep, against the field of the record that holds each one."""


def _digest(
    stage: Stage, inputs: Inputs, kept: Kept, fresh: Mapping[Stage, KeptStage], taken: Mapping[str, object]
) -> str | None:
    """The digest this stage would be kept on, or None when it is a stage a build never keeps.

    Verify measures the film, so its digest starts from the assemble digest of the film on disk,
    which is this run's when it assembled or kept one and the last build's when it did neither, and
    a run with no film on disk has no measurement to keep.
    """
    if stage not in KEEPS:
        return None
    options = _as_json(taken)
    if stage is Stage.ASSEMBLE:
        return assemble_digest(inputs, options)
    made = fresh[Stage.ASSEMBLE].digest if Stage.ASSEMBLE in fresh else assembled(inputs, kept)
    if made is None or not inputs.workspace.film.is_file():
        return None
    return verify_digest(inputs, made, options)


FORCED = "forced"
"""Why a stage was made again when the caller asked for every stage to run."""


def _standing(stage: Stage, inputs: Inputs, kept: Kept, digest: str | None) -> tuple[KeptStage | None, str]:
    """The record of this stage's last run when it still stands, which is what lets a build keep it.

    The token beside it says why it stands or why it does not, which is the line an author reads to
    learn which input moved.
    """
    if digest is None:
        return None, "film-missing"
    record: KeptStage | None = getattr(kept, KEEPS[stage])
    if record is None:
        return None, "no-record"
    if record.digest != digest:
        return None, "key-changed"
    stands = holds_film(inputs, record) if stage is Stage.ASSEMBLE else intact(inputs, record)
    return (record, "unchanged") if stands else (None, "outputs-changed")


def _keep(stage: Stage, run: Run, record: KeptStage) -> list[Finding]:
    """Report a stage this run keeps, and report again what it found when it last ran."""
    run.note(f"{stage.value.capitalize()} kept what it made last time, because nothing it reads has changed.")
    run.emit(StageDone, stage=stage, outcome=Outcome.KEPT, elapsed_seconds=0.0)
    for found in record.findings:
        run.found(found)
    return list(record.findings)


def _remember(
    stage: Stage, inputs: Inputs, digest: str, taken: Mapping[str, object], findings: Sequence[Finding]
) -> KeptStage:
    """The record of a stage this run performed, with what it wrote, for the next build to keep."""
    wrote = inputs.workspace.deliverables().values() if stage is Stage.ASSEMBLE else ()
    return KeptStage(
        digest=digest, options=_as_json(taken), outputs=outputs_of(inputs, wrote), findings=tuple(findings)
    )


def _kept_after(kept: Kept, fresh: Mapping[Stage, KeptStage]) -> Kept:
    """The record this run leaves, which keeps a stage it did not reach as the last build left it.

    A stage this run did not reach that reads from one it did leaves no record behind, because the
    one the last build left was taken of other inputs. A run that assembled and did not verify
    therefore keeps no measurement, because the one the last build took was of another film.
    """
    update: dict[str, KeptStage | None] = {KEEPS[stage]: record for stage, record in fresh.items()}
    update |= {KEEPS[stage]: None for stage in downstream(fresh) if stage in KEEPS and stage not in fresh}
    return kept.model_copy(update=update)


def _as_json(taken: Mapping[str, object]) -> dict[str, JsonValue]:
    """A stage's options as the record holds them, where a section selection is a list or null."""
    return OPTIONS_JSON.validate_python(
        {name: list(value) if isinstance(value, list | tuple) else value for name, value in taken.items()}
    )


def _plan(stages: Sequence[Stage] | None, skip: Sequence[Stage]) -> tuple[Stage, ...]:
    """The stages this run performs, in the pipeline's own order, with the skipped ones removed."""
    wanted = set(stages) if stages is not None else set(Stage)
    plan = tuple(stage for stage in Stage if stage in wanted and stage not in set(skip))
    if not plan:
        asked = ", ".join(stage.value for stage in Stage if stage in wanted) or "no stage"
        left_out = ", ".join(stage.value for stage in skip) or "nothing"
        raise InputError(
            f"this run plans no stage at all, because it asked for {asked} and skipped {left_out}.",
            hint=f"Ask for at least one of {', '.join(stage.value for stage in Stage)}.",
        )
    return plan


def _require_what_the_plan_skips(inputs: Inputs, plan: tuple[Stage, ...], *, score: bool) -> None:
    """Refuse a run that reads an artifact no stage of it writes and nothing has written yet."""
    for artifact in required(plan):
        if artifact is Artifact.SCORE and not score:
            continue
        if BUILT[artifact](inputs):
            continue
        where = inputs.relative(_where(inputs, artifact)).as_posix()
        raise NotBuiltError(
            f"this run starts at {plan[0].value} and reads {where}, which an earlier stage writes.",
            hint=_how_to_get(artifact),
        )


def _how_to_get(artifact: Artifact) -> str:
    """The next step for an artifact nothing has written, with the span that would write it too."""
    writer = artifact.written_by
    if writer is None:
        return artifact.next_step
    return f"{artifact.next_step} A build can also start there with --from {writer.value}."


def _where(inputs: Inputs, artifact: Artifact) -> Path:
    """Where this project keeps one artifact, which for `FINAL` is the film the stage after it reads."""
    return inputs.workspace.film if artifact is Artifact.FINAL else inputs.workspace.of(artifact)


def _storyboard(inputs: Inputs, run: Run, *, only: Sequence[int] | None) -> Path | None:
    """The contact sheet a run that may spend draws before it narrates, or None when it may not."""
    if not run.spend:
        return None
    answer = storyboard.storyboard(inputs, run, only=only)
    return None if answer.storyboard is None else Path(answer.storyboard)


def _skipped(run: Run, stage: Stage) -> StageRun:
    """Close a stage this run leaves out, so a renderer meets every stage of the pipeline once."""
    run.emit(StageDone, stage=stage, outcome=Outcome.SKIPPED, elapsed_seconds=0.0)
    return StageRun(stage=stage, outcome=Outcome.SKIPPED, elapsed_seconds=0.0)


def _hold_to_ceiling(
    inputs: Inputs,
    run: Run,
    plan: tuple[Stage, ...],
    *,
    only: Sequence[int] | None,
    replace_voiced: bool,
    replace_score: bool,
) -> None:
    """Refuse a run whose every stage that buys, added together, is over its ceiling, before any of them runs.

    The cap is held against the whole run's `price` before the first purchase rather than stage by
    stage as the run goes. A run with no ceiling, or one that may not spend, is never priced here.
    """
    if not run.spend or run.max_cost is None:
        return
    whole = price(inputs, plan, only=only, replace_voiced=replace_voiced, replace_score=replace_score)
    if whole is not None:
        run.approve_whole(whole)


def price(
    inputs: Inputs,
    stages: Collection[Stage],
    *,
    only: Sequence[int] | None = None,
    replace_voiced: bool = False,
    replace_score: bool = False,
) -> Cost | None:
    """What a run of these stages that may spend would buy, or None when none of them buys anything.

    Each stage that buys is priced from its plan the way it prices itself, sending nothing, and the
    prices are added by `total`. So the price a caller asks about before a run, the ceiling the run
    is held to and the spend its result reports are one sum of the same stages.
    """
    spends: list[Cost] = []
    if Stage.NARRATE in stages:
        spends.append(narrate.price(inputs, only=only, replace_voiced=replace_voiced))
    if Stage.SCORE in stages:
        spends.append(score_stage.price(inputs, only=only, replace_score=replace_score))
    return total(spends) if spends else None


def _cost_of(answer: Result) -> list[Cost]:
    """The price one stage reported, or nothing at all from a stage that buys nothing."""
    spent = getattr(answer, "cost", None)
    return [spent] if isinstance(spent, Cost) else []


def _film_of(answer: Result) -> Path | None:
    """The film the assembling stage left behind, project-relative, or None when it made none."""
    made = getattr(answer, "film", None)
    return Path(made) if made is not None else None


def _stopped(
    stage: Stage,
    findings: Sequence[Finding],
    run: Run,
    plan: tuple[Stage, ...],
    threshold: Threshold,
) -> bool:
    """Whether the stage that just ran judged something that stops the run, said on the stream when it did."""
    stopping = [found for found in findings if threshold.reaches(found)]
    if not stopping:
        return False
    later = plan[plan.index(stage) + 1 :]
    rest = f"before {later[0].value}" if later else "there"
    run.note(
        f"{stage.value.capitalize()} made {counted(len(stopping), 'finding')} that the build stops on, so the build "
        f"stopped {rest} rather than carry {'it' if len(stopping) == 1 else 'them'} into the film.",
        level=Level.WARNING,
    )
    return True


def total(spends: Sequence[Cost]) -> Cost:
    """What the whole run costs, which is every stage that priced anything added together.

    It is the one way a run's price is summed, so the price a build asks about before it buys and
    the price its result reports after are the same sum of the same stages.

    The total is billed the way the stages that buy something at a price bill, so a free voice beside
    a paid score leaves the sound's bill and rate to the total. When those are one bill, its rate
    is the total's. When a bill per character meets a bill per second the total is `mixed`: it carries
    each rate and counts both the characters and the seconds, and the rate it names is the least
    surely stated, so a sentence never prices sound at the speech rate or speech at the sound one. A
    stage whose bill nobody declared makes the whole total undeclared, because no part of DeckTalk can
    price it. A free voice beside a sound that buys nothing leaves the total free, so it is never asked
    about, and a free voice beside a paid sound is billed as the sound and so is asked about. It sums
    at least one price, because a run that priced nothing has no bill to name.
    """
    buying = [spend for spend in spends if spend.buys] or list(spends)
    deciding = [spend for spend in buying if not spend.free] or buying
    bills = list(dict.fromkeys(spend.billing for spend in deciding))
    if BillingBasis.UNDECLARED in bills:
        bills = [BillingBasis.UNDECLARED]
    first = {bill: next(spend for spend in deciding if spend.billing is bill) for bill in bills}
    unstated = [spend for spend in deciding if spend.price_layer is Layer.DEFAULT]
    named = (unstated or deciding)[0]
    per_character, per_second = first.get(BillingBasis.PER_CHARACTER), first.get(BillingBasis.PER_SECOND)
    return Cost(
        state=CostState.CHARGED if any(s.state is CostState.CHARGED for s in spends) else CostState.ESTIMATE,
        sections=tuple(sorted({number for spend in spends for number in spend.sections})),
        characters=sum(spend.characters for spend in spends),
        seconds=sum(spend.seconds for spend in spends),
        dollars=round(sum(spend.dollars for spend in spends), DOLLAR_DIGITS),
        ceiling_dollars=round(sum(spend.ceiling_dollars for spend in spends), DOLLAR_DIGITS),
        billing=bills[0] if len(bills) == 1 else BillingBasis.MIXED,
        dollars_per_1000_characters=per_character.dollars_per_1000_characters if per_character else 0.0,
        dollars_per_minute=per_second.dollars_per_minute if per_second else 0.0,
        price_key=named.price_key,
        averaged=any(spend.averaged for spend in deciding),
        price_layer=named.price_layer,
    )


__all__ = ["build", "price", "total"]
