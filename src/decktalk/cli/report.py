"""The five commands that read a project and spend nothing.

They open the project, report what they found and buy nothing, which is why they sit in one group
and why none of them takes a spending flag. `status` judges nothing at all, so it never takes
`--fail-on` either: a third judge beside `check` and `verify` would be a third answer to one
question.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from typer._click import Context

from decktalk.cli import session as sessions
from decktalk.cli.app import command, docs_for
from decktalk.cli.options import Fix, Group, Overrides, Panel, Sections, pairs, sections_of
from decktalk.results import CheckResult, ServeResult, StatusResult, StoryboardResult, WordsResult

STATUS_EPILOG = f"""\
Reads the project and writes nothing. The JSON object carries run, name,
script, cues, sections, film, runs and next. Docs: {docs_for("status")}"""

CHECK_EPILOG = f"""\
Judges the written files and the pages, and prices what a voiced build costs.
The JSON object carries run, judged, pages, frames, spend, storyboard,
written and findings. Docs: {docs_for("check")}"""

WORDS_EPILOG = f"""\
Prints the clock a cue phrase is written against. The JSON object carries run
and sections. Docs: {docs_for("words")}"""

STORYBOARD_EPILOG = f"""\
Writes build/storyboard.html, which is the checkpoint before credits are
spent. The JSON object carries run, storyboard, panels and written.
Docs: {docs_for("storyboard")}"""

SERVE_EPILOG = f"""\
Serves the deck directory and the files decktalk.toml declares, and nothing
else. The JSON object carries run, url, port and root. Docs: {docs_for("serve")}"""

DEFAULT_HOST = "127.0.0.1"
"""Where the origin listens, which is this machine alone until a caller names another interface."""

# The four selectors `storyboard` adds to `--section`, each repeatable and each a narrowing. They
# live here rather than in `options.py`, because one command carries them and a shared family is
# what `options.py` is for.
Slides = Annotated[
    list[str] | None,
    typer.Option(
        "--slide",
        metavar="ID",
        rich_help_panel=Panel.SCOPE.value,
        help="Only these slides, by the id the page declares. Repeats.",
    ),
]
After = Annotated[
    list[str] | None,
    typer.Option(
        "--after",
        metavar="CUE",
        rich_help_panel=Panel.SCOPE.value,
        help="Freeze the slide at the moment this cue fires. Repeats.",
    ),
]
Before = Annotated[
    list[str] | None,
    typer.Option(
        "--before",
        metavar="CUE",
        rich_help_panel=Panel.SCOPE.value,
        help="Freeze the slide just before this cue fires. Repeats.",
    ),
]
At = Annotated[
    list[float] | None,
    typer.Option(
        "--at",
        metavar="SECONDS",
        rich_help_panel=Panel.SCOPE.value,
        help="Freeze whatever is on screen this many seconds into its section. Repeats.",
    ),
]


@command(group=Group.PROJECT, epilog=STATUS_EPILOG)
def status(ctx: Context, set_: Overrides = None) -> StatusResult:
    """Report what is written, what is built and what is stale.

    It judges nothing and never exits 1, so it is a reading of the project rather than a third
    verdict beside `check` and `verify`.
    """
    session = sessions.of(ctx)
    session.overriding(pairs(set_))
    project = session.project()
    with session.watching(project.events):
        return project.status(cancel=session.cancel)


@command(group=Group.PROJECT, epilog=CHECK_EPILOG)
def check(
    ctx: Context,
    paths: Annotated[
        list[Path] | None, typer.Argument(metavar="PATH", help="Deck pages to judge, beside the project's own.")
    ] = None,
    section: Sections = None,
    no_pages: Annotated[
        bool, typer.Option("--no-pages", help="Judge the written files with no browser, and say what was not reached.")
    ] = False,
    no_frames: Annotated[
        bool, typer.Option("--no-frames", help="Keep the browser and drop the freeze comparison.")
    ] = False,
    fix: Fix = None,
    set_: Overrides = None,
) -> CheckResult:
    """Judge script.md, cues.json and the pages before a build.

    It produces nothing and prices what a voiced build would cost, so an agent that wants the price
    of a run makes this one call.
    """
    session = sessions.of(ctx)
    session.overriding(pairs(set_))
    project = session.project()
    with session.watching(project.events):
        judged = project.check(
            *(paths or ()),
            only=sections_of(section),
            pages=not no_pages,
            frames=not no_frames,
            cancel=session.cancel,
        )
    return _fixed(session, judged, fix)


def _fixed(session: sessions.Session, judged: CheckResult, fix: bool | None) -> CheckResult:
    """Apply the safe fixes when the caller asked, and judge again so the result is what is true now.

    Without a terminal and without the flag nothing is applied and every fix is reported, so an
    agent applies them itself from the objects it already holds.
    """
    offered = [found for found in judged.findings if found.fix is not None]
    if not offered:
        return judged
    wanted = fix if fix is not None else session.asks and session.confirm(f"Apply {len(offered)} fixes?")
    if not wanted:
        return judged
    project = session.project()
    project.apply(offered)
    fresh = project.reload()
    with session.watching(fresh.events):
        return fresh.check(pages=judged.pages, frames=judged.frames, cancel=session.cancel)


@command(group=Group.PROJECT, epilog=WORDS_EPILOG)
def words(ctx: Context, section: Sections = None, set_: Overrides = None) -> WordsResult:
    """Print every spoken word with its start and end.

    It is the one command that maps script text to seconds, which is how a cue phrase is written.
    """
    session = sessions.of(ctx)
    session.overriding(pairs(set_))
    project = session.project()
    with session.watching(project.events):
        return project.words(only=sections_of(section), cancel=session.cancel)


@command(group=Group.PROJECT, epilog=STORYBOARD_EPILOG)
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

    One panel of a storyboard is still a storyboard, so the name survives every selector, and the
    page it writes is the checkpoint a voiced build points at before it buys. The five selectors
    narrow the sheet and a selector that matches nothing draws nothing, which is what a section
    number that matches no section already does.
    """
    session = sessions.of(ctx)
    session.overriding(pairs(set_))
    project = session.project()
    with session.watching(project.events):
        return project.storyboard(
            only=sections_of(section), slide=slide, after=after, before=before, at=at, cancel=session.cancel
        )


@command(group=Group.PROJECT, epilog=SERVE_EPILOG)
def serve(
    ctx: Context,
    host: Annotated[str, typer.Option("--host", metavar="HOST", help="The interface to listen on.")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option("--port", metavar="PORT", help="The port to listen on, or 0 for any.")] = 0,
    set_: Overrides = None,
) -> ServeResult:
    """Serve the project on a local origin over http.

    It prints where the deck is served and then closes stdout, so a caller that captured the output
    reads one object and is not left waiting on a stream that never ends.
    """
    session = sessions.of(ctx)
    session.overriding(pairs(set_))
    project = session.project()
    origin = project.serve(host=host, port=port)
    session.report(origin.result)
    session.out.file.flush()
    try:
        origin.wait()
    except KeyboardInterrupt:
        pass
    finally:
        origin.close()
    return origin.result


__all__ = ["check", "serve", "status", "storyboard", "words"]
