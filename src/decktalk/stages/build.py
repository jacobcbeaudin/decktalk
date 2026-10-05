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
is built is the `BUILT` rule in `kept.py`, which `status` reads too, so a build never goes ahead on
a directory the report calls unfinished.

An unchanged build keeps `assemble` and `verify` rather than repeating them. Both are pure
functions of files already on disk, so when nothing they read has moved since the last build ran
them, the film on disk is the one they would make and the measurement is the one they would take.
The stage is reported as kept, its findings are reported again, and `force` runs it anyway.

A run that may spend and would open a page under the untrusted policy is refused before its first
stage, so it buys nothing it then cannot use. A run with a ceiling is then priced whole, every stage
that buys added together, so a build whose takes and sounds together pass `--max-cost` is refused
before it buys anything. The storyboard a person reads before anything is bought is the command
line's checkpoint, drawn before the run opens, and a build draws none of its own.

A stage whose findings reach the caller's threshold stops the run, and the run still returns its
result. A finding is a judgement and not an error, so the stages that ran, the findings they made and
the money narrate and score already spent reach the caller as fields it can read, and `stopped_at` names the
stage the run stopped after.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter

from decktalk.errors import HALTS, Cancelled, DeckTalkError, ErrorInfo, InputError, NotBuiltError, as_refusal
from decktalk.events import Level, StageDone
from decktalk.findings import Finding
from decktalk.inputs import Inputs
from decktalk.logs import cache_decision
from decktalk.machine.run import Run
from decktalk.media import browser
from decktalk.pipeline import Artifact, Outcome, Stage, downstream, required
from decktalk.results import BuildResult, Cost, CostState, Result, StageRun, counted
from decktalk.stages import narrate
from decktalk.stages import score as score_stage
from decktalk.stages.cost import cost_of, total
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
) -> BuildResult:
    """Run every stage of the pipeline, or the span of them `stages` names, in run order.

    Whether the run may spend is the run's own rather than a parameter, so one gate decides it for
    the library, the command line and a service alike. A stage that judges something the run's
    threshold reaches stops the run, because a cue whose phrase is never spoken leaves a slide that
    never appears and a page that threw recorded an empty stage, and carrying on would deliver a film
    that is wrong in a way the run already knows about.

    The threshold is the caller's own, carried by the run from the project it was opened on, which
    is what `--allow` and `--fail-on` set on the command line. An allowed code never stops the run,
    `Severity.ERROR` stops on an error, `Severity.WARNING` stops on any finding, and a threshold that
    fails on nothing lets every stage run so that `verify` measures what the earlier stages made. The
    stages after a stop are reported as skipped and the result names the stage in `stopped_at`. The
    result's `ok` is read from the same threshold, so it is false on a run that stopped and on one
    whose verify judged something the threshold fails on, and true on a run whose every finding was
    allowed or under the line.

    Whether the film carries the score is read from `skip`, because a run told to leave the
    stage out is a run that does not want its sound, and a second switch for the same decision would
    let a caller skip the stage and still be refused for the file it never asked for.
    """
    plan = _plan(stages, skip)
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
    _refuse_a_key_beside_a_page(inputs, run, plan)
    _hold_to_ceiling(inputs, run, plan, only=only, replace_voiced=replace_voiced, replace_score=replace_score)
    going = _Going()
    try:
        film, stopped_at = _stages(inputs, run, plan, options, going, force=force)
    except HALTS as failure:
        refusal = as_refusal(failure)
        refusal.result = _bought_before(refusal, run, going)
        if refusal is failure:
            raise
        raise refusal from failure
    return run.result(
        BuildResult,
        stages=tuple(going.rows),
        spend=run.spend,
        cost=total(going.spends) if going.spends else cost_of(inputs),
        film=film,
        stopped_at=stopped_at,
    )


@dataclass
class _Going:
    """How far a build has got: the stage it is at, the rows of the stages behind it and what they reported."""

    at: Stage | None = None
    opened: float = 0.0
    closed: bool = False
    """Whether the stage it is at has its `stage.done` line, which a stage opened on the stream always gets."""
    rows: list[StageRun] = field(default_factory=list)
    spends: list[Cost] = field(default_factory=list)
    film: Path | None = None
    """The film an assemble this run ran or kept left on disk, which a stop after it still names."""


def _stages(
    inputs: Inputs, run: Run, plan: tuple[Stage, ...], options: Mapping[str, object], going: _Going, *, force: bool
) -> tuple[Path | None, Stage | None]:
    """Run, keep or skip every stage in pipeline order, recording each as it goes, and give back the film and the
    stage the run stopped at.

    Everything the build does stage by stage happens here, so a refusal or an interrupt anywhere in it,
    in a stage or between two, is met by the one handler in `build` with `going` as far as it got.
    """
    kept = read_kept(inputs)
    fresh: dict[Stage, KeptStage] = {}
    stopped_at: Stage | None = None
    for stage in Stage:
        if stage not in plan or stopped_at is not None:
            going.rows.append(_skipped(run, stage))
            continue
        going.at, going.opened, going.closed = stage, time.monotonic(), False
        run.check()
        taken = _taken(inputs, stage, options)
        if taken is None:
            run.note(f"{stage.value.capitalize()} has none of the selected sections to work on, so it is skipped.")
            going.rows.append(_skipped(run, stage))
            continue
        findings, made = _one_stage(inputs, run, stage, plan, taken, kept, fresh, going, force=force)
        going.film = made or going.film
        if stage is not Stage.VERIFY and _stopped(stage, findings, run, plan):
            stopped_at = stage
    if fresh:
        run.wrote(_kept_after(kept, fresh).write(inputs.workspace.kept_path))
    return going.film, stopped_at


ACTS_ON: dict[Stage, Callable[[Inputs], Collection[int]]] = {
    Stage.NARRATE: lambda inputs: {section.number for section in inputs.spoken()},
    Stage.RECORD: lambda inputs: {section.number for section in inputs.document.page_sections},
}
"""The stages that work on one kind of section only, with the section numbers of that kind: narrate speaks the
spoken sections and record records the page sections. Every other stage acts on any section."""


def _share(inputs: Inputs, stage: Stage, only: Sequence[int] | None) -> Sequence[int] | None:
    """The part of a selection this stage can act on: all of it, or the sections of its kind in a selection."""
    if only is None or stage not in ACTS_ON:
        return only
    acts = ACTS_ON[stage](inputs)
    if all(number in acts for number in only):
        return only
    return tuple(number for number in only if number in acts)


def _taken(inputs: Inputs, stage: Stage, options: Mapping[str, object]) -> dict[str, object] | None:
    """The options this stage is handed, with its share of the selection, or None when its share is empty.

    A selection of a clip alone has nothing to narrate or record, so those two are skipped and the
    stages that cut and measure the clip still run, which is how a saved clip is rebuilt alone.
    """
    taken = {name: options[name] for name in CALLS[stage].options}
    if "only" not in taken:
        return taken
    share = _share(inputs, stage, cast("Sequence[int] | None", taken["only"]))
    if share is not None and not share:
        return None
    return {**taken, "only": share}


def _one_stage(
    inputs: Inputs,
    run: Run,
    stage: Stage,
    plan: tuple[Stage, ...],
    taken: Mapping[str, object],
    kept: Kept,
    fresh: dict[Stage, KeptStage],
    going: _Going,
    *,
    force: bool,
) -> tuple[list[Finding], Path | None]:
    """Keep one stage the last build left standing, or run it, and give back what it found and the film it made."""
    digest = _digest(stage, inputs, kept, fresh, taken)
    standing = None
    if stage in KEEPS:
        standing, why = (None, FORCED) if force else _standing(stage, inputs, kept, digest)
        cache_decision(log, stage.value, hit=standing is not None, why=why, key=digest)
    if standing is not None:
        going.rows.append(StageRun(stage=stage, outcome=Outcome.KEPT, elapsed_seconds=_since(going)))
        fresh[stage] = standing
        going.closed = True
        findings = _keep(stage, run, standing)
        return findings, inputs.relative(inputs.workspace.film) if stage is Stage.ASSEMBLE else None
    going.closed = True
    with run.stage(stage, index=plan.index(stage) + 1, count=len(plan)):
        answer = CALLS[stage].call(inputs, run, **taken)
    going.rows.append(StageRun(stage=stage, outcome=Outcome.RAN, elapsed_seconds=_since(going)))
    going.spends += _reported(answer)
    findings = [found for found in answer.findings if found.stage is stage]
    if digest is not None:
        fresh[stage] = _remember(stage, inputs, digest, taken, findings)
    return findings, _film_of(answer) if stage is Stage.ASSEMBLE else None


def _since(going: _Going) -> float:
    """How long the stage the build is at has taken so far."""
    return time.monotonic() - going.opened


def _bought_before(failure: DeckTalkError, run: Run, going: _Going) -> BuildResult | None:
    """The build a refusal carries when the run bought something before it, or None when it bought nothing.

    What the stages behind it reported and what a refused stage carried of its own are added, so a
    build stopped or refused anywhere still says what it spent. The stage it was at ends as stopped
    or failed, unless its row is already written, on the stream too when the stop came before it
    opened there, and every stage after it as skipped. The film an assemble left is named. A build that
    paid nothing, and might have paid nothing, carries nothing, so its refusal reads as any other.
    """
    own = getattr(failure.result, "cost", None)
    costs = [*going.spends, *([own] if isinstance(own, Cost) else [])]
    if not any(cost.state is CostState.CHARGED and cost.ceiling_dollars > 0 for cost in costs):
        # A free voice's takes are charged nothing, so a run that paid nothing has nothing to report.
        return None
    done = {row.stage for row in going.rows}
    here = []
    if going.at is not None and going.at not in done:
        ended = Outcome.STOPPED if isinstance(failure, Cancelled) else Outcome.FAILED
        here = [StageRun(stage=going.at, outcome=ended, elapsed_seconds=_since(going))]
        if not going.closed:
            # A stop between two stages lands before this one opened on the stream, so it is closed here.
            run.emit(StageDone, stage=going.at, outcome=ended, elapsed_seconds=_since(going))
    order = list(Stage)
    after = order[order.index(going.at) + 1 :] if going.at is not None else order
    later = [_skipped(run, stage) for stage in after if stage not in done]
    return run.result(
        BuildResult,
        ok=False,
        error=ErrorInfo.of(failure),
        stages=(*going.rows, *here, *later),
        spend=run.spend,
        cost=total(costs),
        film=going.film,
        stopped_at=None,
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


def _refuse_a_key_beside_a_page(inputs: Inputs, run: Run, plan: tuple[Stage, ...]) -> None:
    """Refuse a run that may spend and would open an untrusted page, before its first stage buys anything.

    A stage that opens a page refuses the same run when it launches, which is after narrate has
    bought, so the build asks the launch's own rule first of any plan that reaches such a stage.
    """
    if any(stage.spec.opens_pages for stage in plan):
        browser.admitted(inputs.settings.record.page_policy, spend=run.spend)


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

    The cap is held against the whole run's `price` before the first buy rather than stage by
    stage as the run goes. A run with no ceiling, or one that may not spend, is never priced here.
    """
    if not run.spend or run.max_cost is None:
        return
    run.approve_whole(price(inputs, plan, only=only, replace_voiced=replace_voiced, replace_score=replace_score))


def price(
    inputs: Inputs,
    stages: Collection[Stage],
    *,
    only: Sequence[int] | None = None,
    replace_voiced: bool = False,
    replace_score: bool = False,
) -> Cost:
    """What a run of these stages that may spend would buy, or the price of nothing at the voice's rate when none
    of them buys.

    Each stage that buys is priced from its plan the way it prices itself, sending nothing, and the
    prices are added by `total`. So the price a caller asks about before a run, the ceiling the run
    is held to and the cost its result reports are one sum of the same stages.
    """
    spends: list[Cost] = []
    spoken = _share(inputs, Stage.NARRATE, only)
    if Stage.NARRATE in stages and (spoken is None or spoken):
        spends.append(narrate.price(inputs, only=spoken, replace_voiced=replace_voiced))
    if Stage.SCORE in stages:
        spends.append(score_stage.price(inputs, only=only, replace_score=replace_score))
    return total(spends) if spends else cost_of(inputs)


def _reported(answer: Result) -> list[Cost]:
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
) -> bool:
    """Whether the stage that just ran judged something that stops the run, said on the stream when it did."""
    stopping = [found for found in findings if run.threshold.reaches(found)]
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


__all__ = ["build", "price"]
