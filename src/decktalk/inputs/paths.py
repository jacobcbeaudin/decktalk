"""Where a file is, said the one way every result and every finding says it.

Every path DeckTalk publishes is relative to the project root and spelled with forward slashes, so
`model_dump_json()` needs no root and a golden comparison reads the same on all three platforms.
Paths are made relative the moment a stage fills them rather than at serialisation time, which is
why this sits at the bottom of the input layer where every filler can reach it.

A path outside the project, such as a take directory `[narration] cache_dir` moved elsewhere, is
published as it is, because a relative path with `..` in it names nothing a reader can open.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.findings import Location


def relative(path: Path, root: Path) -> Path:
    """`path` as the project sees it, or `path` itself when it is not under the project."""
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return path


def at(
    path: Path,
    root: Path | None = None,
    *,
    line: int | None = None,
    section: int | None = None,
    cue: str | None = None,
) -> Location:
    """The location of one file, named by the file itself, with whatever else the caller knows."""
    shown = relative(path, root) if root is not None else path
    return Location(where=shown.name, file=shown, line=line, section=section, cue=cue)


__all__ = ["at", "relative"]
