"""The eight commands that move a project forward: the six stages, the whole run, and one cut of it.

Every one of them is a thin client. It opens the project, asks the session whether this run may
spend, hands the library the options it was given and returns the result the library made. The
order of the six is the order of the pipeline, which is the same list `--from`, `--to`, `--skip` and
the `stage` of an event line all read.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated

import typer
from typer import Context

from decktalk.cli import session as sessions
from decktalk.cli import watch as watching
from decktalk.cli.app import command
from decktalk.cli.options import (
    Fix,
    Force,
    Group,
    Overrides,
    Panel,
    ReplaceScore,
    ReplaceVoiced,
    Sections,
    Skip,
    one_section,
    sections_of,
)
from decktalk.pipeline import Stage
from decktalk.project import Project
from decktalk.results import (
    AssembleResult,
    BuildResult,
    ClipResult,
    Cost,
    CueResult,
    NarrateResult,
    RecordResult,
    ScoreResult,
    VerifyResult,
    counted,
)

BUILD_SHORT = "Run every stage in order, or a span of them."
"""What the command tree says about `build`, where its own help names the two flags that span it."""

FromStage = Annotated[
    Stage | None,
    typer.Option(
        "--from",
        metavar="STAGE",
        rich_help_panel=Panel.SELECTION.value,
        help="Start at this stage: narrate, cue, record, score, assemble or verify.",
    ),
]
ToStage = Annotated[
    Stage | None,
    typer.Option(
        "--to", metavar="STAGE", rich_help_panel=Panel.SELECTION.value, help="Stop after this stage, inclusive."
    ),
]
# The flags below share a name with a family in `options.py` and mean something narrower on the one
# command that declares them, so each says what it does there rather than what the family does.
SkipScore = Annotated[
    Stage | None,
    typer.Option(
        "--skip",
        metavar="STAGE",
        rich_help_panel=Panel.SELECTION.value,
        help="Mix without this stage's audio. score is the one stage assemble can leave out.",
    ),
]
OneSection = Annotated[
    str | None,
    typer.Option(
        "--section",
        metavar="N",
        rich_help_panel=Panel.SELECTION.value,
        help="The one section to cut the clip from, such as 3.",
    ),
]
SCORE_SPEND = {
    "spend": "Buy what needs it without asking first, or buy nothing: report the plan and write nothing.",
}
"""The spend flags as `score` means them, where the thing bought is sound rather than a voice."""

RECORD_AGAIN = {"force": "Record every section again, even one whose recording still matches its page."}
"""What `--force` redoes on `record`, which is the capture rather than the whole build."""

BUILD_AGAIN = {
    "force": "Build again from nothing, keeping every voiced take and every bought sound, and measure the film again."
}
"""What `--force` redoes on `build`, which also measures the film again."""

Watch = Annotated[
    bool,
    typer.Option(
        "--watch",
        rich_help_panel=Panel.THIS_RUN.value,
        help="Stay running, rebuild the changed section, never spend.",
    ),
]


@command(group=Group.STAGE)
def narrate(
    ctx: Context,
    section: Sections = None,
    force: Force = False,
    replace_voiced: ReplaceVoiced = False,
    set_: Overrides = None,
) -> NarrateResult:
    """Voice each section of script.md and time every word.

    It owns the take index and the words every later stage measures against.
    \f
    It names the transformation, which is text to a spoken take.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    only = sections_of(section)
    spend = session.spends(project, price=lambda: session.price(project, only=only), replacing=replace_voiced)
    with session.watching(project.events):
        return project.narrate(
            only=only,
            spend=spend,
            max_cost=session.max_cost,
            force=force,
            replace_voiced=_replacing(session, replace_voiced),
            cancel=session.cancel,
        )


@command(group=Group.STAGE)
def cue(ctx: Context, section: Sections = None, set_: Overrides = None) -> CueResult:
    """Turn each cue phrase into a second on its section clock.

    It reads cues.json, writes the cue times and reports the CUE findings.
    \f
    The input is cues.json, the output is the cue times, the findings are the CUE codes and every
    page moment names a cue, so the stage is called what everything around it is called.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    with session.watching(project.events):
        return project.cue(only=sections_of(section), cancel=session.cancel)


@command(
    group=Group.STAGE,
    helps=RECORD_AGAIN,
)
def record(ctx: Context, section: Sections = None, force: Force = False, set_: Overrides = None) -> RecordResult:
    """Record each page section in headless Chromium.

    The pages are played against the seconds the cues named, so the picture lands on its word before
    anything is cut.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    with session.watching(project.events):
        return project.record(only=sections_of(section), force=force, cancel=session.cancel)


@command(
    group=Group.STAGE,
    helps=SCORE_SPEND,
)
def score(
    ctx: Context, section: Sections = None, replace_score: ReplaceScore = False, set_: Overrides = None
) -> ScoreResult:
    """Compose the music, the ambience bed and the effects.

    It runs after `record` and before `assemble`, whose mix consumes what it writes.
    \f
    It runs after `record` so that the unpaid draft loop stops at a recording.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    only = sections_of(section)
    spend = session.spends(
        project,
        price=lambda: session.sound_price(project, only=only, replace_score=replace_score),
        replacing=replace_score,
        plays=sessions.SOUNDS_PLAY,
    )
    with session.watching(project.events):
        return project.score(
            only=only,
            spend=spend,
            max_cost=session.max_cost,
            replace_score=_replacing_score(session, replace_score, spend=spend),
            cancel=session.cancel,
        )


@command(group=Group.STAGE)
def assemble(ctx: Context, section: Sections = None, skip: SkipScore = None, set_: Overrides = None) -> AssembleResult:
    """Cut, mix and encode the sections into one mp4.
    \f
    It is the editing room's word for joining shots into a cut, where render, encode and mix each
    name one of the things it does.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    with session.watching(project.events):
        return project.assemble(
            only=sections_of(section),
            score=_skipped_here(skip) is not Stage.SCORE,
            cancel=session.cancel,
        )


def _skipped_here(skip: Stage | None) -> Stage | None:
    """The stage `--skip` names on a command that runs one stage, which is the score alone."""
    if skip is not None and skip is not Stage.SCORE:
        raise typer.BadParameter(
            f"assemble runs one stage, so --skip names score alone and not {skip.value}.", param_hint="--skip"
        )
    return skip


@command(
    group=Group.STAGE,
)
def verify(ctx: Context, section: Sections = None, set_: Overrides = None) -> VerifyResult:
    """Measure the finished mp4: every start, cut, seam and landing.

    It measures the film against the clock the earlier stages promised.
    \f
    That clock is the product's whole claim written as a measurement.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    with session.watching(project.events):
        return project.verify(only=sections_of(section), cancel=session.cancel)


@command(
    group=Group.WHOLE,
    epilog="Writes build/final/<name>.mp4 with its captions, chapters, transcript page and poster.",
    short_help=BUILD_SHORT,
    helps=BUILD_AGAIN,
)
def build(
    ctx: Context,
    from_stage: FromStage = None,
    to_stage: ToStage = None,
    skip: Skip = None,
    section: Sections = None,
    fix: Fix = None,
    force: Force = False,
    replace_voiced: ReplaceVoiced = False,
    replace_score: ReplaceScore = False,
    set_: Overrides = None,
    watch: Watch = False,
) -> BuildResult:
    """Run every stage in order, or a span of them with --from and --to.

    A stage whose inputs have not changed is skipped. A build with something to buy prices it and
    asks on a terminal before it buys. Without a terminal it refuses unless --spend or --no-spend is passed.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    only = sections_of(section)
    if watch:
        return watching.loop(session, project, skip=tuple(skip or ()), only=only, force=force)
    stages = _span(from_stage, to_stage)
    planned = _planned(stages, skip)
    spend = (
        session.spends(
            project,
            price=_build_price(session, project, planned, only, replace_score=replace_score),
            replacing=replace_voiced or replace_score,
            storyboard=True,
            plays=_plays(planned),
        )
        if planned & {Stage.NARRATE, Stage.SCORE}
        else bool(session.spend)
    )
    with session.watching(project.events, opening=True):
        built = project.build(
            stages=stages,
            skip=tuple(skip or ()),
            only=only,
            spend=spend,
            max_cost=session.max_cost,
            force=force,
            replace_voiced=_replacing(session, replace_voiced),
            replace_score=_replacing_score(session, replace_score, spend=spend),
            allow=session.allowed,
            stop_on=session.fail_on.stops_on,
            cancel=session.cancel,
        )
    return _offered(session, project, built, fix)


def _span(first: Stage | None, last: Stage | None) -> tuple[Stage, ...] | None:
    """The stages a run covers, which is the whole pipeline when neither end was named."""
    if first is None and last is None:
        return None
    return Stage.span(first, last)


def _planned(stages: Sequence[Stage] | None, skip: Sequence[Stage] | None) -> set[Stage]:
    """The stages a build with this span and these skips performs."""
    return set(stages or Stage) - set(skip or ())


def _plays(planned: set[Stage]) -> str:
    """What a build that does not spend plays in place of what its planned stages would buy."""
    bought = {Stage.NARRATE: sessions.TAKES_PLAY, Stage.SCORE: sessions.SOUNDS_PLAY}
    return " and ".join(plays for stage, plays in bought.items() if stage in planned)


def _build_price(
    session: sessions.Session,
    project: Project,
    planned: set[Stage],
    only: Sequence[int] | None,
    *,
    replace_score: bool = False,
) -> Callable[[], Cost | None]:
    """How a build is priced before it is asked about: every stage it performs that buys, added together.

    The price a person approves is the whole run's, so the takes and the score are summed by the
    same total the build's result reports, and a yes never lets through a stage the question left out.
    That total keeps the free-voice rules: a free voice beside a sound that buys nothing is asked
    nothing, and a free voice beside a paid sound is asked about the sound. A stage that could not be
    priced leaves the build unpriced, so it is asked about rather than called cheaper than it is. A
    stage the build does not perform is never priced, so a build that starts past `narrate` asks
    nothing about narration.
    """

    def price() -> Cost | None:
        from decktalk.stages.build import total  # noqa: PLC0415  (a stage is loaded by the call that needs it)

        priced: list[Cost | None] = []
        if Stage.NARRATE in planned:
            priced.append(session.price(project, only=only))
        if Stage.SCORE in planned:
            priced.append(session.sound_price(project, only=only, replace_score=replace_score))
        stated = [spend for spend in priced if spend is not None]
        return total(stated) if stated and len(stated) == len(priced) else None

    return price


def _offered(sessions_: sessions.Session, project: Project, built: BuildResult, fix: bool | None) -> BuildResult:
    """Apply the safe fixes a finished run offered, and say that the run has to be made again.

    A fix changes an input, so the film beside it is the film the old input made. The run is not
    repeated here, because repeating a paid run without being asked is how the same take is bought twice.
    """
    offered = sessions_.fixes_wanted(built.findings, fix)
    if not offered:
        return built
    with sessions_.watching(project.events):
        applied = project.apply(offered)
    changed = sum(1 for outcome in applied.fixes if outcome.applied)
    sessions_.say(f"Applied {counted(changed, 'fix', 'fixes')}. Run decktalk build again to make the film from them.")
    return built


@command(group=Group.WHOLE)
def clip(
    ctx: Context,
    section: OneSection = None,
    start: Annotated[float, typer.Option("--start", metavar="SECONDS", help="Where the clip starts.")] = 0.0,
    end: Annotated[float, typer.Option("--end", metavar="SECONDS", help="Where the clip ends.")] = 0.0,
    out: Annotated[
        Path | None,
        typer.Option("--out", metavar="FILE", help="Where to write the clip. Default: clip-N.mp4, for section N."),
    ] = None,
    gain_db: Annotated[float, typer.Option("--gain-db", metavar="DB", help="Lift or cut the clip's audio.")] = 0.0,
    hold_seconds: Annotated[
        float, typer.Option("--hold-seconds", metavar="SECONDS", help="Hold the last frame this long.")
    ] = 0.0,
    set_: Overrides = None,
) -> ClipResult:
    """Cut a span of a built section into its own file.
    \f
    A clip is one thing in this product, which is a short video file, so the command that makes one
    is called what the file is called.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    chosen = one_section((section,) if section else None)
    with session.watching(project.events):
        return project.clip(
            chosen,
            start=start,
            end=end,
            out=out or Path(f"clip-{chosen}.mp4"),
            gain_db=gain_db,
            hold_seconds=hold_seconds,
            cancel=session.cancel,
        )


def _replacing(session: sessions.Session, asked: bool) -> bool:
    """Whether voiced takes are set aside, confirmed once on a terminal because the answer can buy them again.

    Without a terminal the flag is the authorisation, because a run that was told to replace a take
    was told so on purpose and the safe default without the flag is to keep every take.
    """
    return asked and (not session.asks or session.confirm("This sets aside every voiced take it replaces. Carry on?"))


def _replacing_score(session: sessions.Session, asked: bool, *, spend: bool) -> bool:
    """Whether every bought sound is bought again, confirmed once on a terminal because the answer spends.

    A run that may not spend has nothing to replace a bought sound with, so it is asked nothing and
    keeps every one. Without a terminal the flag is the authorisation, as it is for a voiced take.
    """
    question = "This buys every bought sound it replaces again. Carry on?"
    return asked and spend and (not session.asks or session.confirm(question))


__all__ = ["assemble", "build", "clip", "cue", "narrate", "record", "score", "verify"]
