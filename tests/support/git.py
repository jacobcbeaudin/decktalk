"""A throwaway repository holding a fresh clone of a project, which is how the Action's checkout is read.

The takes directory and the score directory are paid records a project commits, so a test of either
asks git itself whether a run without spend changed a tracked file. Both kinds share this one helper,
so the identity, the environment and what a clone leaves out are written once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def git(repo: Path, *argv: str) -> str:
    """One git command in `repo` under a fixed identity, with what it printed."""
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    identity = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")
    # A suite run from a git hook or a rebase inherits GIT_DIR and its kin, which would point these
    # commands at the repository under test instead of the throwaway one.
    environ = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    done = subprocess.run(
        ["git", *identity, "-C", str(repo), *argv], capture_output=True, text=True, check=True, env=environ
    )
    return done.stdout


def tracked_copy(root: Path, to: Path) -> Path:
    """What a fresh clone of the project at `root` holds: every file but the build directory and `.env`."""
    shutil.copytree(root, to, ignore=shutil.ignore_patterns("build", ".env"))
    return to


def committed_clone(root: Path, to: Path) -> Path:
    """A fresh clone of the project at `root`, committed in a repository that ignores only `build/`."""
    clone = tracked_copy(root, to)
    (clone / ".gitignore").write_text("build/\n", encoding="utf-8")
    git(clone, "init", "-q")
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "paid records")
    return clone


__all__ = ["committed_clone", "git", "tracked_copy"]
