"""The whole pipeline in order, or the span of it one run asked for.

`build` runs the six stages narrate, cue, record, soundscape, assemble and verify, in that order,
and reports each one on the event stream as it opens and closes. It writes no file of its own: the
run's account of itself is the stream, its lines are appended to `build/events/<run>.jsonl` by the
machine's own sink, and every file the stages wrote is already recorded on the run.

Which stages a run performs is read from `PIPELINE` and never worked out here. `Stage.span` gives
the run of stages between two ends, `required` gives the artifacts that run reads but does not
write, and `Artifact.written_by` gives the stage that would have written each one, so a run that
starts past a missing artifact is refused with the file and the command named, and this module
carries no "run this first" sentence of its own.

A voiced run draws the storyboard before it narrates, because the contact sheet is the checkpoint a
person reads before any credit is bought, and a run that writes placeholders has nothing to check.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from decktalk.errors import InputError, NotBuiltError
from decktalk.events import StageDone
from decktalk.findings import Certainty, Code, Finding
from decktalk.inputs import Inputs
from decktalk.machine import Run
from decktalk.pipeline import Artifact, Outcome, Stage, required
from decktalk.results import BuildResult, Layer, Result, Spend, SpendState, StageRun, Voicing
from decktalk.stages import assemble, clock, cue, narrate, record, since, storyboard, verify
from decktalk.stages import soundscape as soundscape_stage
from decktalk.stages.narrate.plan import PRICE_KEY

NOTHING = 0.0
"""What a stage that never opened took, which is the elapsed time a skipped row reports."""

FIRST = 1
"""Where a run's first stage sits in its own plan, because a person counts stages from one."""

DOLLAR_DIGITS = 2
"""Truth: a price is stated to the cent, which is what every spend in the product is rounded to."""

MODULES: dict[Stage, ModuleType] = {
    Stage.NARRATE: narrate,
    Stage.CUE: cue,
    Stage.RECORD: record,
    Stage.SOUNDSCAPE: soundscape_stage,
    Stage.ASSEMBLE: assemble,
    Stage.VERIFY: verify,
}
"""Each stage against the module that implements it, which is the one seam a test replaces.

The module is held rather than the function, so the function is looked up when the stage is called
and a test that replaces `decktalk.stages.record.record` is obeyed by a build exactly as it is by
the facade. One word therefore names the stage, its module, its function and its event.
"""

OPTIONS: dict[Stage, tuple[str, ...]] = {
    Stage.NARRATE: ("only", "force", "replace_voiced"),
    Stage.CUE: ("only", "allow_unknown"),
    Stage.RECORD: ("only", "force"),
    Stage.SOUNDSCAPE: ("only", "force"),
    Stage.ASSEMBLE: ("only", "soundscape", "loudness", "strict"),
    Stage.VERIFY: ("only",),
}
"""Which of a build's options each stage takes, which is the whole of what a build passes on.

The options are selected per stage rather than passed whole, because a stage handed keywords it does
not read would accept a flag that changes nothing, which is the false entry in the instruction set
the founder's thesis exists to prevent.
"""

ARTIFACTS: dict[Artifact, str] = {
    Artifact.TAKES: "takes_path",
    Artifact.CUE_TIMES: "cue_times_path",
    Artifact.RECORDINGS: "recordings_dir",
    Artifact.SOUNDSCAPE: "soundscape_dir",
    Artifact.FINAL: "film",
}
"""Each artifact against the workspace property that says where this project keeps it.

`Artifact` publishes the path a project uses by default and `[project] build` may name another, so
the workspace is asked where a file is and the pipeline is asked what it is for. `FINAL` is the film
itself rather than the directory around it, because the film is what the stage after it reads.
"""


def build(
    inputs: Inputs,
    run: Run,
    *,
    stages: Sequence[Stage] | None = None,
    skip: Sequence[Stage] = (),
    only: Sequence[int] | None = None,
    force: bool = False,
    replace_voiced: bool = False,
    soundscape: bool = True,
    loudness: bool = True,
    strict: bool = False,
    allow_unknown: bool = False,
) -> BuildResult:
    """Run every stage of the pipeline, or the span of them `stages` names, in run order.

    Whether the run spends is the run's own voicing rather than a parameter, so one gate decides it
    for the library, the command line and a service alike, and the storyboard is drawn first when it
    does. A stage that judges something certain stops the run, because a cue whose phrase is never
    spoken leaves a slide that never appears and a page that threw recorded an empty stage, and
    carrying on would deliver a film that is wrong in a way the run already knows about.
    """
    started = clock()
    plan = _plan(stages, skip)
    _require_what_the_plan_skips(inputs, plan, soundscape=soundscape)
    options: dict[str, object] = {
        "only": only,
        "force": force,
        "replace_voiced": replace_voiced,
        "soundscape": soundscape,
        "loudness": loudness,
        "strict": strict,
        "allow_unknown": allow_unknown,
    }
    board = _storyboard(inputs, run, only=only)
    rows: list[StageRun] = []
    spends: list[Spend] = []
    film: Path | None = None
    for stage in Stage:
        if stage not in plan:
            rows.append(_skipped(run, stage))
            continue
        run.check()
        opened = clock()
        with run.stage(stage, index=plan.index(stage) + FIRST, count=len(plan)):
            answer = _call(stage, inputs, run, options)
        rows.append(StageRun(stage=stage, outcome=Outcome.OK, seconds=since(opened)))
        spends += _spend_of(answer)
        if stage is Stage.ASSEMBLE:
            film = _film_of(answer)
        if stage is not Stage.VERIFY:
            _stop_on_what_is_certain(stage, answer, allow_unknown=allow_unknown)
    return run.result(
        BuildResult,
        stages=tuple(rows),
        voice=run.voice,
        spend=_total(spends, inputs),
        film=film,
        storyboard=board,
        seconds=since(started),
    )


def _plan(stages: Sequence[Stage] | None, skip: Sequence[Stage]) -> tuple[Stage, ...]:
    """The stages this run performs, in the pipeline's own order, with the skipped ones removed.

    `stages` is the span a caller asked for, which is every stage when it names none. A run left
    with no stage at all is refused, because a build that did nothing and exited clean is a false
    answer to the question the caller asked.
    """
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


def _require_what_the_plan_skips(inputs: Inputs, plan: tuple[Stage, ...], *, soundscape: bool) -> None:
    """Refuse a run that reads an artifact no stage of it writes and nothing has written yet.

    The list comes from `PIPELINE`, so the precondition, the refusal's next step and the one
    `status` reports are three readings of one table. The soundscape is the single artifact a
    project may honestly have none of, so it is asked for only when the project declares one and the
    run was not told to leave it out.
    """
    for artifact in required(plan):
        if artifact is Artifact.SOUNDSCAPE and (not soundscape or inputs.document.soundscape.empty):
            continue
        where = _where(inputs, artifact)
        if _present(where):
            continue
        raise NotBuiltError(
            f"this run starts at {plan[0].value} and reads {inputs.relative(where).as_posix()}, "
            "which an earlier stage writes.",
            hint=_how_to_get(artifact),
        )


def _how_to_get(artifact: Artifact) -> str:
    """The next step for an artifact nothing has written, read from the stage that writes it."""
    writer = artifact.written_by
    if writer is None:
        return f"Nothing in the pipeline writes {artifact.value}."
    return f"Run `decktalk {writer.value}` first, or start this build at it with --from {writer.value}."


def _where(inputs: Inputs, artifact: Artifact) -> Path:
    """Where this project keeps one artifact, asked of the workspace rather than of the pipeline."""
    return getattr(inputs.workspace, ARTIFACTS[artifact])


def _present(path: Path) -> bool:
    """Whether an artifact is really there, which for a directory means it holds something.

    A directory an earlier run made and left empty is as good as absent to the stage that reads it,
    so a run that would assemble from no recording at all is refused here rather than left to fail
    inside an encoder, where the reason would be an ffmpeg message instead of a next step.
    """
    if path.is_dir():
        return any(path.iterdir())
    return path.is_file()


def _storyboard(inputs: Inputs, run: Run, *, only: Sequence[int] | None) -> Path | None:
    """The contact sheet a voiced run draws before it narrates, or None when nothing is bought.

    The storyboard is the checkpoint before voice credits are spent, so a run that is going to spend
    draws it first and a run that writes placeholders has nothing to check and draws none.
    """
    if run.voice is not Voicing.PAID:
        return None
    answer = storyboard.storyboard(inputs, run, only=only)
    return None if answer.storyboard is None else Path(answer.storyboard)


def _skipped(run: Run, stage: Stage) -> StageRun:
    """Close a stage this run leaves out, so a renderer meets every stage of the pipeline once.

    A skipped stage never opens, so it reports no start and one end carrying the outcome that says
    why, which is the one field that replaces a second event name.
    """
    run.emit(StageDone, stage=stage, outcome=Outcome.SKIPPED, seconds=NOTHING)
    return StageRun(stage=stage, outcome=Outcome.SKIPPED, seconds=NOTHING)


def _call(stage: Stage, inputs: Inputs, run: Run, options: dict[str, object]) -> Result:
    """Hand one stage its inputs, its run and the options it declares, and nothing else."""
    taken = {name: options[name] for name in OPTIONS[stage]}
    return getattr(MODULES[stage], stage.value)(inputs, run, **taken)


def _spend_of(answer: Result) -> list[Spend]:
    """The price one stage reported, or nothing at all from a stage that buys nothing."""
    spent = getattr(answer, "spend", None)
    return [spent] if isinstance(spent, Spend) else []


def _film_of(answer: Result) -> Path | None:
    """The film the assembling stage left behind, project-relative, or None when it made none."""
    made = getattr(answer, "film", None)
    return Path(made) if made is not None else None


def _stop_on_what_is_certain(stage: Stage, answer: Result, *, allow_unknown: bool) -> None:
    """Stop the run when the stage that just ran judged something certain about its own work.

    A certain finding is a fact the run already holds, so carrying on would deliver a film that is
    wrong in a way nobody has to watch it to discover. Only the findings that stage raised are
    weighed, because one run carries every judgement made in it and an earlier stage's would
    otherwise stop the run twice. `verify` is last and measures the finished film, so its findings
    end the run rather than stop it, and they never reach here.
    """
    certain = [found for found in answer.findings if found.stage is stage and _stops(found, allow_unknown)]
    if not certain:
        return
    listed = "\n  ".join(f"{found.code.name} at {found.location.where}: {found.message}" for found in certain)
    raise InputError(
        f"{stage.value} found {len(certain)} thing(s) that are certainly wrong, so the build stopped "
        "there rather than carrying them into the film.",
        hint=f"Fix these and run the build again:\n  {listed}",
        location=certain[0].location,
    )


def _stops(found: Finding, allow_unknown: bool) -> bool:
    """Whether one finding stops the run, which every certain one does but the one a flag forgives.

    A cue row no page declares is the one certain finding an author may knowingly keep, because a
    deck under construction lists the cues of slides it has not drawn yet, and
    `--allow CUE_UNKNOWN` is what says so.
    """
    if found.certainty is not Certainty.CERTAIN:
        return False
    return not (allow_unknown and found.code is Code.CUE_UNKNOWN)


def _total(spends: Sequence[Spend], inputs: Inputs) -> Spend:
    """What the whole run cost, which is every stage that priced anything added together.

    A run where nothing was priced still reports a spend, because a reader that met a null there
    would have to know which stages price and which do not before it could say the run cost nothing.
    """
    if not spends:
        return Spend(
            state=SpendState.ESTIMATE,
            sections=(),
            characters=0,
            dollars=0.0,
            ceiling_dollars=0.0,
            price_per_1000_characters=inputs.settings.voice.price_per_1000_characters,
            price_layer=_price_layer(inputs),
        )
    sections: list[int] = []
    for spend in spends:
        sections += [number for number in spend.sections if number not in sections]
    return Spend(
        state=SpendState.CHARGED if any(s.state is SpendState.CHARGED for s in spends) else SpendState.ESTIMATE,
        sections=tuple(sorted(sections)),
        characters=sum(spend.characters for spend in spends),
        dollars=round(sum(spend.dollars for spend in spends), DOLLAR_DIGITS),
        ceiling_dollars=round(sum(spend.ceiling_dollars for spend in spends), DOLLAR_DIGITS),
        price_per_1000_characters=spends[0].price_per_1000_characters,
        price_layer=spends[0].price_layer,
    )


def _price_layer(inputs: Inputs) -> Layer:
    """Which layer stated the price, so a run that bought nothing still says where its rate came from."""
    try:
        return inputs.layers.winner(PRICE_KEY).layer
    except KeyError:
        return Layer.DEFAULT


__all__ = ["build"]
