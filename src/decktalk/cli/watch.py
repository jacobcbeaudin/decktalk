"""One terminal that serves the deck and rebuilds the section a save changed.

The loop is the draft loop. It starts the local origin itself and prints the URL, builds once
without buying, playing every take on disk, making each missing one with a voice that bills
nothing, and playing a placeholder for each one a voice that bills would sell, and on every save
rebuilds only the sections the changed file touches. It never spends, whatever the settings say,
and it says so when a save leaves a voiced take behind. The explicit spend is a different command,
`decktalk narrate --section 3 --spend`, because the safe default is the rule and the named escape
is a separate act.

Files are watched by their modification times rather than by an operating-system channel, because a
poll a tenth of a second long is indistinguishable to an author and costs no dependency that three
platforms would each have to be proved on.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from decktalk.cli import session as sessions
from decktalk.errors import Cancelled, DeckTalkError, ErrorInfo
from decktalk.pipeline import Stage
from decktalk.project import Project
from decktalk.results import Billing, BuildResult, Cost, CostState, Layer

POLL_SECONDS = 0.4
"""How long the loop sleeps between two readings of the tree, which is under an author's own pause."""

IGNORED = frozenset({"build", ".git", ".venv", "node_modules", "__pycache__"})
"""The directories a save never means, which are what a run writes rather than what an author edits."""


def loop(
    session: sessions.Session,
    project: Project,
    *,
    skip: Sequence[Stage] = (),
    only: Sequence[int] | None = None,
    force: bool = False,
) -> BuildResult:
    """Serve the project, build it once, and rebuild what each save touches until the caller stops.

    The result given back is the last build made, so a caller that stopped the loop still receives
    the run it was watching rather than nothing.
    """
    origin = project.serve()
    session.say(f"Serving {origin.result.url}")
    session.say(
        "Watching for saves. Nothing here spends: a free voice reads each change, and a take a voice bills for goes "
        "stale rather than being bought again."
    )
    built = _once(session, project, skip=skip, only=only, force=force)
    seen = _stamps(project)
    try:
        while True:
            time.sleep(POLL_SECONDS)
            fresh = _stamps(project)
            changed = [path for path, stamp in fresh.items() if seen.get(path) != stamp]
            seen = fresh
            if not changed:
                continue
            project = project.reload()
            built = _once(session, project, skip=skip, only=_touched(project, changed) or only, force=force)
            _stale(session, project)
    except KeyboardInterrupt:
        # silent: an interrupt is how a person ends the watch loop.
        session.say("Stopped.")
    finally:
        origin.close()
    return built


def _once(
    session: sessions.Session,
    project: Project,
    *,
    skip: Sequence[Stage],
    only: Sequence[int] | None,
    force: bool,
) -> BuildResult:
    """One unpaid build, whose refusal is reported and whose loop carries on.

    A watch loop that stopped on the first broken file would make an author restart it to see the
    next error, so a refusal is printed as the one error block and the loop keeps watching.
    """
    try:
        with session.watching(project.events):
            return project.build(
                skip=tuple(skip),
                only=only,
                spend=False,
                force=force,
                allow=session.allowed,
                stop_on=session.fail_on.stops_on,
                cancel=session.cancel,
            )
    except Cancelled:
        raise
    except DeckTalkError as refused:
        refusal = ErrorInfo.of(refused)
        session.reported(refusal)
        return _nothing(refusal)


def _nothing(refusal: ErrorInfo) -> BuildResult:
    """The result a refused rebuild leaves behind, which carries the refusal and no film.

    A run that never opened bought nothing and asked no voice how it bills, so its price is nothing
    on a bill nobody declared, at a rate nobody stated.
    """
    return BuildResult(
        ok=False,
        error=refusal,
        run="",
        stages=(),
        spend=False,
        cost=Cost(
            state=CostState.ESTIMATE,
            sections=(),
            characters=0,
            dollars=0.0,
            ceiling_dollars=0.0,
            billing=Billing.UNDECLARED,
            dollars_per_1000_characters=0.0,
            price_layer=Layer.DEFAULT,
        ),
        elapsed_seconds=0.0,
    )


def _touched(project: Project, changed: Iterable[Path]) -> tuple[int, ...] | None:
    """Every section the saved files touch, or None when a save touched the whole project."""
    sections: list[int] = []
    for path in changed:
        found = project.sections_touching(path)
        if not found:
            return None
        sections.extend(found)
    return tuple(dict.fromkeys(sections)) or None


def _stale(session: sessions.Session, project: Project) -> None:
    """Say which sections now hold a voiced take that no longer matches what the author wrote."""
    reported = project.status()
    gone = [row.section for row in reported.sections if row.voiced and row.stale]
    for section in gone:
        session.say(
            f"Section {section} has a voiced take that no longer matches the script. "
            f"Run decktalk narrate --section {section} --spend to voice it again."
        )


def _stamps(project: Project) -> dict[Path, float]:
    """Every file an author edits under the project, with when it was last written.

    The walk prunes a directory before it descends, so it never stats what `node_modules` or `.git`
    holds. The project's own build and take folders are pruned wherever its settings put them,
    because a build that wrote into a watched folder would start the next build without end.
    """
    written = {
        project._inputs.workspace.build.resolve(),
        *(place.resolve() for place in project._inputs.workspace.take_places),
    }
    found: dict[Path, float] = {}
    for folder, dirs, files in os.walk(project.root):
        here = Path(folder)
        dirs[:] = [name for name in dirs if name not in IGNORED and (here / name).resolve() not in written]
        for name in files:
            try:
                found[here / name] = (here / name).stat().st_mtime
            except OSError:
                # silent: a file removed between the listing and its stat is not there to watch.
                continue
    return found


__all__ = ["loop"]
