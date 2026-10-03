"""The five commands that read a project and spend nothing.

They open the project, report what they found and buy nothing, which is why they sit in one group
and why none of them takes a spend flag. `status` judges one thing alone, a file the project
names and does not have, so it never takes `--fail-on` either: a third judge beside `check` and
`verify` would be a third answer to one question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from typer import Context

from decktalk.cli import session as sessions
from decktalk.cli.app import command
from decktalk.cli.options import Fix, Group, Overrides, Panel, Sections, sections_of
from decktalk.project import LOOPBACK
from decktalk.results import CheckResult, ServeResult, StatusResult, StoryboardResult, WordsResult

# The four selectors `storyboard` adds to `--section`, each repeatable and each a narrowing. They
# live here rather than in `options.py`, because one command carries them and a shared family is
# what `options.py` is for.
Slides = Annotated[
    list[str] | None,
    typer.Option(
        "--slide",
        metavar="ID",
        rich_help_panel=Panel.SELECTION.value,
        help="Only these slides, by the id the page declares. Repeats.",
    ),
]
After = Annotated[
    list[str] | None,
    typer.Option(
        "--after",
        metavar="CUE",
        rich_help_panel=Panel.SELECTION.value,
        help="Freeze the slide at the moment this cue fires. Repeats.",
    ),
]
Before = Annotated[
    list[str] | None,
    typer.Option(
        "--before",
        metavar="CUE",
        rich_help_panel=Panel.SELECTION.value,
        help="Freeze the slide just before this cue fires. Repeats.",
    ),
]
At = Annotated[
    list[float] | None,
    typer.Option(
        "--at",
        metavar="SECONDS",
        rich_help_panel=Panel.SELECTION.value,
        help="Freeze whatever is on screen this many seconds into its section. Repeats.",
    ),
]


@command(group=Group.PROJECT, epilog="Reads the project and writes only its run's events file.")
def status(ctx: Context, set_: Overrides = None) -> StatusResult:
    """Report what is written, what is built and what is stale.

    It judges one thing, a file the project names and does not have, and exits 1 only then.
    Everything else it reports is a reading of the project rather than a third verdict beside
    `check` and `verify`.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    with session.watching(project.events):
        return project.status(cancel=session.cancel)


@command(
    group=Group.PROJECT, epilog="Judges the written files and the pages, and prices the narration a voiced build buys."
)
def check(
    ctx: Context,
    paths: Annotated[
        list[Path] | None, typer.Argument(metavar="PATH", help="Deck pages to judge, beside the project's own.")
    ] = None,
    section: Sections = None,
    pages: Annotated[
        bool,
        typer.Option(
            "--pages/--no-pages",
            help="Open the pages in a browser, or judge the written files alone and say what was not reached.",
        ),
    ] = True,
    frames: Annotated[
        bool,
        typer.Option("--frames/--no-frames", help="Freeze and compare the slides, or keep the browser and drop that."),
    ] = True,
    fix: Fix = None,
    set_: Overrides = None,
) -> CheckResult:
    """Judge script.md, cues.json and the pages before a build.

    It produces nothing and prices what a voiced build would cost, so an agent that wants the price
    of a run makes this one call.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    asked = {"only": sections_of(section), "pages": pages, "frames": frames}
    heard: set[str] = set()
    with session.watching(project.events, heard=heard):
        judged = project.check(*(paths or ()), **asked, cancel=session.cancel)
    return _fixed(session, judged, fix, paths=tuple(paths or ()), asked=asked, heard=heard)


def _fixed(
    session: sessions.Session,
    judged: CheckResult,
    fix: bool | None,
    *,
    paths: tuple[Path, ...],
    asked: dict[str, Any],
    heard: set[str],
) -> CheckResult:
    """Apply the safe fixes when the caller asked, and judge again so the result is what is true now.

    Without a terminal and without the flag nothing is applied and every fix is reported, so an
    agent applies them itself from the objects it already holds. The second judgement is asked
    exactly what the first was, with the same pages and the same sections, because a re-check that
    widened to the whole project priced sections the caller never named. A note the first judgement
    printed is in `heard` and is not printed again, because the second judgement says it too.
    """
    offered = session.fixes_wanted(judged.findings, fix)
    if not offered:
        return judged
    project = session.project()
    with session.watching(project.events, heard=heard):
        project.apply(offered)
    fresh = project.reload()
    with session.watching(fresh.events, heard=heard):
        return fresh.check(*paths, **asked, cancel=session.cancel)


@command(group=Group.PROJECT, epilog="Prints the clock a cue phrase is written against.")
def words(ctx: Context, section: Sections = None, set_: Overrides = None) -> WordsResult:
    """Print every spoken word with its start and end.

    It is the one command that maps script text to seconds, which is how a cue phrase is written.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    with session.watching(project.events):
        return project.words(only=sections_of(section), cancel=session.cancel)


@command(group=Group.PROJECT, epilog="Writes build/storyboard.html, which is the checkpoint before anything is bought.")
def storyboard(
    ctx: Context,
    section: Sections = None,
    slide: Slides = None,
    after: After = None,
    before: Before = None,
    at: At = None,
    set_: Overrides = None,
) -> StoryboardResult:
    """Freeze every slide at every cue onto one page.

    The page it writes is the checkpoint a voiced build points at before it buys. The five selectors
    narrow the sheet and a selector that matches nothing draws nothing, which is what a section
    number that matches no section already does.
    \f
    One panel of a storyboard is still a storyboard, so the name survives every selector.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    with session.watching(project.events):
        return project.storyboard(
            only=sections_of(section), slides=slide, after=after, before=before, times=at, cancel=session.cancel
        )


@command(
    group=Group.PROJECT, epilog="Serves the deck directory and the files decktalk.toml declares, and nothing else."
)
def serve(
    ctx: Context,
    host: Annotated[str, typer.Option("--host", metavar="HOST", help="The interface to listen on.")] = LOOPBACK,
    port: Annotated[int, typer.Option("--port", metavar="PORT", help="The port to listen on, or 0 for any.")] = 0,
    set_: Overrides = None,
) -> ServeResult:
    """Serve the project on a local origin over http.

    It prints where the deck is served as one object, flushed at once, and then serves until it is
    interrupted. Its standard output stays open while it serves, so a caller reads the object as it
    arrives rather than waiting for the stream to end.
    """
    session = sessions.of(ctx)
    project = session.opened(set_)
    origin = project.serve(host=host, port=port)
    session.report(origin.result)
    session.out.file.flush()
    try:
        origin.wait()
    except KeyboardInterrupt:
        # silent: an interrupt is how a person ends the report.
        pass
    finally:
        origin.close()
    return origin.result


__all__ = ["check", "serve", "status", "storyboard", "words"]
