"""Which files one run of the house rules reads: every file of the tree, or the files a commit stages.

A run started by `tests/house_rules.py` names its files, which are the files prek passes the
`house-rules` hook. A commit stages a few, so the rules read those alone. The lint row runs every
hook with `--all-files`, which passes every tracked file, so CI reads the whole tree. A run that
names no files, such as a plain `pytest -m lint`, reads every file.

A rule about the tree itself, such as a baseline entry whose file is gone, judges every entry
whatever a run names, because a deleted file is never a file a commit passes. A commit that only
deletes files passes none, so prek runs no hook at all, and the lint row judges that commit.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from support.paths import REPO

NAMED = pytest.StashKey[frozenset[Path]]()
"""The files a run of the house rules names, each resolved, which `tests/house_rules.py` sets."""


def named(config: pytest.Config | None) -> frozenset[Path] | None:
    """The files this run names, or None when it names none, or is no pytest run at all, and so reads every file."""
    return None if config is None else config.stash.get(NAMED, None)


def within(config: pytest.Config | None, paths: Iterable[Path]) -> list[Path]:
    """`paths` cut to the files this run names, or every one of them when it names none."""
    files = named(config)
    return [path for path in paths if files is None or path.resolve() in files]


def excused(config: pytest.Config | None, baseline: dict[str, int]) -> dict[str, int]:
    """The baseline entries this run judges: those of the files it names, and every entry whose file is gone."""
    files = named(config)
    if files is None:
        return baseline
    return {
        name: count
        for name, count in baseline.items()
        if (REPO / name).resolve() in files or not (REPO / name).exists()
    }
