"""Where a file is, said the one way every result and every finding says it.

Every path DeckTalk publishes is relative to the project root and spelled with forward slashes, so
`model_dump_json()` needs no root and a golden comparison reads the same on all three platforms.
Paths are made relative the moment a stage fills them rather than at serialisation time, which is
why this sits at the bottom of the input layer where every filler can reach it.

A path outside the project, such as a take found in the machine's `[narration] cache_dir`, is
published as it is, because a relative path with `..` in it names nothing a reader can open.

Every file the project itself names is read through `contained`, which is the one rule that says a
project path stays inside the project. `tomlmap` refuses `..` and an absolute value by their
spelling, and a link is what that spelling cannot see: a `script.md` that links to a file elsewhere
on the machine would have its lines voiced, captioned and published. So the rule resolves every link
before it compares, and it lives here, where every reader of a project path already reaches.

The build directory and a project's own take directory are the places DeckTalk writes in the
project, and a project that arrives with either tree already in it chose every name in that tree.
`confined` is the rule for such a tree: it resolves every entry under the directory and refuses the
tree when one leads out of it, so a link at `build/narrate` or a committed take file linked to a file
elsewhere cannot turn a read, a write or a prune into one outside the project.
"""

from __future__ import annotations

import os
from pathlib import Path

from decktalk.errors import InputError
from decktalk.findings import Location


def relative(path: Path, root: Path) -> Path:
    """`path` as the project sees it, or `path` itself when it is not under the project."""
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        # silent: a path outside the project is named as it was given.
        return path


def contained(root: Path, named: str | Path) -> Path:
    """The path a project names, joined to its root, refused when it resolves outside the project.

    Links are followed before the comparison, so a link inside the project that points elsewhere is
    refused however its name is spelled. The path comes back as the project named it rather than as
    what it resolved to, because a link that stays inside the project is the author's own layout and
    every name DeckTalk publishes is the one the author wrote. The refusal names the path and never
    the place it resolved to, which belongs to the machine and not to the project.
    """
    given = Path(named)
    path = root / given
    if not path.resolve().is_relative_to(root.resolve()):
        raise InputError(
            f"{Path(named).as_posix()} resolves to a place outside the project, so it is not read.",
            hint="Keep every file the project names inside the project directory, and never a link out of it.",
            location=Location(where=path.name, file=given),
        )
    return path


def confined(root: Path, directory: Path, *, named: str = "the build directory") -> Path:
    """`directory` resolved, once it and everything under it are known to stay inside it.

    The directory itself is held to the project by `contained`. Every entry under it is then
    resolved without following a link into a walk, and the tree is refused when a link resolves
    outside the directory or a file shares its contents with another name through a hard link,
    because a write that follows either one lands wherever the tree's author pointed it. A link that
    stays inside the directory is harmless and is left alone. A directory that is not there yet holds
    nothing to refuse. `named` is what the refusal calls the directory.
    """
    top = contained(root, directory).resolve()
    if not top.is_dir():
        return top
    for parent, folders, files in os.walk(top):
        for name in (*folders, *files):
            entry = Path(parent) / name
            escapes = entry.is_symlink() and not entry.resolve().is_relative_to(top)
            shared = not entry.is_symlink() and entry.is_file() and entry.lstat().st_nlink > 1
            if escapes or shared:
                home = root.resolve()
                shown = entry.relative_to(home) if entry.is_relative_to(home) else entry.relative_to(top)
                raise InputError(
                    f"{shown.as_posix()} leads outside {named}, so DeckTalk writes nothing there.",
                    hint=f"Delete {named}, or the entry named here, and run again.",
                    location=Location(where=entry.name, file=shown),
                )
    return top


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


__all__ = ["at", "confined", "contained", "relative"]
