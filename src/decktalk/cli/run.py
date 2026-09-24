"""The eight commands that move a project forward: the six stages, the whole run, and one cut of it.

Every one of them is a thin client. It opens the project, asks the session what this run does about
the voice, hands the library the options it was given and returns the result the library made. The
order of the six is the order of the pipeline, which is the same list `--from`, `--to`, `--skip` and
the `stage` of an event line all read.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from typer._click import Context

from decktalk.cli import session as sessions
from decktalk.cli import watch as watching
from decktalk.cli.app import command, docs_for
from decktalk.cli.options import (
    Fix,
    Force,
    Group,
    Overrides,
    Panel,
    ReplaceVoiced,
    Sections,
    Skip,
    one_section,
    pairs,
    sections_of,
)
from decktalk.findings import Code
from decktalk.pipeline import Stage
from decktalk.project import Project
from decktalk.results import (
    AssembleResult,
    BuildResult,
    ClipResult,
    CueResult,
    NarrateResult,
    RecordResult,
    SoundscapeResult,
    VerifyResult,
)

BUILD_EPILOG = f"""\
Writes build/final/<name>.mp4 with its captions, chapters, transcript page
and poster. The JSON object carries run, stages, seconds, written, findings
and error. Docs: {docs_for("build")}"""

BUILD_SHORT = "Run every stage in order, or a span of them."
"""What the command tree says about `build`, where its own help names the two flags that span it."""

FromStage = Annotated[
    Stage | None,
    typer.Option(
        "--from",
        metavar="STAGE",
        rich_help_panel=Panel.SCOPE.value,
        help="Start at this stage: narrate, cue, record, soundscape, assemble or verify.",
    ),
]
ToStage = Annotated[
    Stage | None,
    typer.Option("--to", metavar="STAGE", rich_help_panel=Panel.SCOPE.value, help="Stop after this stage, inclusive."),
]
Watch = Annotated[
    bool,
    typer.Option(
        "--watch",
        rich_help_panel=Panel.THIS_RUN.value,
        help="Stay running, rebuild the changed section, never spend.",
    ),
]


@command(
    group=Group.STAGE, epilog=f"The JSON object carries run, sections, spend and takes. Docs: {docs_for('narrate')}"
)
def narrate(
    ctx: Context,
    section: Sections = None,
    force: Force = False,
    replace_voiced: ReplaceVoiced = False,
    set_: Overrides = None,
) -> NarrateResult:
    """Voice each section of script.md and time every word.

    It names the transformation, which is text to a spoken take, and it owns the take index and the
    word clock every later stage measures against.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    voice = session.voicing(project)
    with session.watching(project.events):
        return project.narrate(
            only=sections_of(section),
            voice=voice,
            max_cost=session.max_cost,
            force=force,
            replace_voiced=_replacing(session, replace_voiced),
            cancel=session.cancel,
        )


@command(group=Group.STAGE, epilog=f"The JSON object carries run, sections and file. Docs: {docs_for('cue')}")
def cue(ctx: Context, section: Sections = None, set_: Overrides = None) -> CueResult:
    """Turn each cue phrase into a second on its section clock.

    The input is cues.json, the output is the cue times, the findings are the CUE codes and every
    page moment names a cue, so the stage is called what everything around it is called.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    with session.watching(project.events):
        return project.cue(
            only=sections_of(section),
            allow_unknown=Code.CUE_UNKNOWN in session.allowed,
            cancel=session.cancel,
        )


@command(group=Group.STAGE, epilog=f"The JSON object carries run, sections and written. Docs: {docs_for('record')}")
def record(ctx: Context, section: Sections = None, force: Force = False, set_: Overrides = None) -> RecordResult:
    """Record each page section in headless Chromium.

    The pages are played against the seconds the cues named, so the picture lands on its word before
    anything is cut.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    with session.watching(project.events):
        return project.record(only=sections_of(section), force=force, cancel=session.cancel)


@command(group=Group.STAGE, epilog=f"The JSON object carries run, items and spend. Docs: {docs_for('soundscape')}")
def soundscape(
    ctx: Context, section: Sections = None, force: Force = False, set_: Overrides = None
) -> SoundscapeResult:
    """Generate the music, the ambience bed and the effects.

    It runs after `record` so that the unpaid draft loop stops at a recording, and before `assemble`
    because the mix consumes what it writes.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    voice = session.voicing(project)
    with session.watching(project.events):
        return project.soundscape(
            only=sections_of(section),
            voice=voice,
            max_cost=session.max_cost,
            force=force,
            cancel=session.cancel,
        )


@command(
    group=Group.STAGE, epilog=f"The JSON object carries run, film, sections and loudness. Docs: {docs_for('assemble')}"
)
def assemble(ctx: Context, section: Sections = None, skip: Skip = None, set_: Overrides = None) -> AssembleResult:
    """Cut, mix and encode the sections into one mp4.

    It is the editing room's word for joining shots into a cut, where render, encode and mix each
    name one of the things it does.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    with session.watching(project.events):
        return project.assemble(
            only=sections_of(section),
            soundscape=Stage.SOUNDSCAPE not in _skipped_here(skip),
            cancel=session.cancel,
        )


def _skipped_here(skip: Sequence[Stage] | None) -> tuple[Stage, ...]:
    """The stages `--skip` names on a command that runs one stage, which is the soundscape alone."""
    named = tuple(skip or ())
    wrong = [stage.value for stage in named if stage is not Stage.SOUNDSCAPE]
    if wrong:
        raise typer.BadParameter(
            f"assemble runs one stage, so --skip names soundscape alone and not {wrong[0]}.", param_hint="--skip"
        )
    return named


@command(
    group=Group.STAGE,
    epilog=f"The JSON object carries run, film, starts, cuts, seams and cues. Docs: {docs_for('verify')}",
)
def verify(ctx: Context, section: Sections = None, set_: Overrides = None) -> VerifyResult:
    """Measure the finished mp4: every start, cut, seam and landing.

    It measures the film against the clock the earlier stages promised, which is the product's whole
    claim written as a measurement.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    with session.watching(project.events):
        return project.verify(only=sections_of(section), cancel=session.cancel)


@command(group=Group.WHOLE, epilog=BUILD_EPILOG, short_help=BUILD_SHORT)
def build(
    ctx: Context,
    from_stage: FromStage = None,
    to_stage: ToStage = None,
    skip: Skip = None,
    section: Sections = None,
    fix: Fix = None,
    force: Force = False,
    replace_voiced: ReplaceVoiced = False,
    set_: Overrides = None,
    watch: Watch = False,
) -> BuildResult:
    """Run every stage in order, or a span of them with --from and --to.

    A stage whose inputs have not changed is skipped. On a terminal a voiced build prices the spend
    and asks before it buys. Without a terminal it refuses unless --spend or --no-voice is passed.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    only = sections_of(section)
    if watch:
        return watching.loop(session, project, skip=tuple(skip or ()), only=only, force=force)
    voice = session.voicing(project, storyboard=True)
    with session.watching(project.events, opening=True):
        built = project.build(
            stages=_span(from_stage, to_stage),
            skip=tuple(skip or ()),
            only=only,
            voice=voice,
            max_cost=session.max_cost,
            force=force,
            replace_voiced=_replacing(session, replace_voiced),
            soundscape=Stage.SOUNDSCAPE not in (skip or ()),
            allow_unknown=Code.CUE_UNKNOWN in session.allowed,
            cancel=session.cancel,
        )
    return _offered(session, project, built, fix)


def _span(first: Stage | None, last: Stage | None) -> tuple[Stage, ...] | None:
    """The stages a run covers, which is the whole pipeline when neither end was named."""
    if first is None and last is None:
        return None
    return Stage.span(first, last)


def _offered(sessions_: sessions.Session, project: Project, built: BuildResult, fix: bool | None) -> BuildResult:
    """Apply the safe fixes a finished run offered, and say that the run has to be made again.

    A fix changes an input, so the film beside it is the film the old input made. The run is not
    repeated here, because repeating a paid run without being asked is how credits are spent twice.
    """
    offered = [found for found in built.findings if found.fix is not None]
    if not offered:
        return built
    wanted = fix if fix is not None else sessions_.asks and sessions_.confirm(f"Apply {len(offered)} fixes?")
    if not wanted:
        return built
    applied = project.apply(offered)
    changed = sum(1 for outcome in applied.fixes if outcome.applied)
    sessions_.say(f"Applied {changed} fixes. Run decktalk build again to make the film from them.")
    return built


@command(group=Group.WHOLE, epilog=f"The JSON object carries run, film, words and written. Docs: {docs_for('clip')}")
def clip(
    ctx: Context,
    section: Sections = None,
    start: Annotated[float, typer.Option("--start", metavar="SECONDS", help="Where the clip starts.")] = 0.0,
    end: Annotated[float, typer.Option("--end", metavar="SECONDS", help="Where the clip ends.")] = 0.0,
    out: Annotated[Path | None, typer.Option("--out", metavar="FILE", help="Where to write the clip.")] = None,
    gain_db: Annotated[float, typer.Option("--gain-db", metavar="DB", help="Lift or cut the clip's audio.")] = 0.0,
    hold_seconds: Annotated[
        float, typer.Option("--hold-seconds", metavar="SECONDS", help="Hold the last frame this long.")
    ] = 0.0,
    set_: Overrides = None,
) -> ClipResult:
    """Cut a span of a built section into its own file.

    A clip is one thing in this product, which is a short video file, so the command that makes one
    is called what the file is called.
    """
    session = sessions.of(ctx)
    project = _opened(session, set_)
    chosen = one_section(section)
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


def _opened(session: sessions.Session, overrides: Sequence[str] | None) -> Project:
    """This run's project, opened with its `--set` pairs, which the loader validates before a stage runs."""
    session.overriding(pairs(overrides))
    return session.project()


def _replacing(session: sessions.Session, asked: bool) -> bool:
    """Whether paid takes are discarded, confirmed once on a terminal because the answer destroys money.

    Without a terminal the flag is the authorisation, because a run that was told to replace a take
    was told so on purpose and the safe default without the flag is to keep every take.
    """
    if not asked:
        return False
    if session.asks and not session.confirm("This discards every take it replaces. Carry on?"):
        return False
    return True


__all__ = ["assemble", "build", "clip", "cue", "narrate", "record", "soundscape", "verify"]
