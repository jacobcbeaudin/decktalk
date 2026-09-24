"""One terminal that serves the deck and rebuilds the section a save changed.

The loop is the draft loop. It starts the local origin itself and prints the URL, builds once with
placeholder narration, and on every save rebuilds only the sections the changed file touches. It
never voices, whatever the settings say, and it says so when a save leaves a paid take behind. The
explicit spend is a different command, `decktalk narrate --section 3`, because the safe default is
the rule and the named escape is a separate act.

Files are watched by their modification times rather than by an operating-system channel, because a
poll a tenth of a second long is indistinguishable to an author and costs no dependency that three
platforms would each have to be proved on.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from decktalk.cli import session as sessions
from decktalk.errors import Cancelled, DeckTalkError, ErrorInfo
from decktalk.pipeline import Stage
from decktalk.project import Project
from decktalk.results import BuildResult, Layer, Spend, SpendState, Voicing

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
    session.say(f"Serving {origin.url}")
    session.say("Watching for saves. Nothing here spends, so a voiced take goes stale rather than being replaced.")
    built = _once(session, project, skip=skip, only=only, force=force)
    seen = _stamps(project.root)
    try:
        while True:
            time.sleep(POLL_SECONDS)
            fresh = _stamps(project.root)
            changed = [path for path, stamp in fresh.items() if seen.get(path) != stamp]
            seen = fresh
            if not changed:
                continue
            project = project.reload()
            built = _once(session, project, skip=skip, only=_touched(project, changed) or only, force=force)
            _stale(session, project)
    except KeyboardInterrupt:
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
                voice=Voicing.PLACEHOLDER,
                force=force,
                soundscape=Stage.SOUNDSCAPE not in skip,
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

    A run that never opened bought nothing, so its price is nothing at a rate nobody stated.
    """
    return BuildResult(
        ok=False,
        error=refusal,
        run="",
        stages=(),
        voice=Voicing.PLACEHOLDER,
        spend=Spend(
            state=SpendState.ESTIMATE,
            sections=(),
            characters=0,
            dollars=0.0,
            ceiling_dollars=0.0,
            price_per_1000_characters=0.0,
            price_layer=Layer.DEFAULT,
        ),
        seconds=0.0,
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
    """Say which sections now hold a paid take that no longer matches what the author wrote."""
    reported = project.status()
    gone = [row.section for row in reported.sections if row.voiced and row.stale]
    for section in gone:
        session.say(
            f"Section {section} has a voiced take that no longer matches the script. "
            f"Run decktalk narrate --section {section} --spend to voice it again."
        )


def _stamps(root: Path) -> dict[Path, float]:
    """Every file an author edits under the project, with when it was last written."""
    found: dict[Path, float] = {}
    for path in root.rglob("*"):
        if not path.is_file() or IGNORED & set(path.relative_to(root).parts):
            continue
        try:
            found[path] = path.stat().st_mtime
        except OSError:
            continue
    return found


__all__ = ["loop"]
