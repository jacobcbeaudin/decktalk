"""Both ignore files keep every `.env` variant out of git and leave `.env.example` in.

The rules are checked with git itself, in a throwaway repository that holds each ignore file,
so the test reads the same rules git will apply.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from support.paths import REPO

ROOT = REPO
IGNORE_FILES = {
    "repo": ROOT / ".gitignore",
    "starter": ROOT / "src" / "decktalk" / "template" / "starter" / "gitignore",
}
SECRET_NAMES = [".env", ".env.local", ".env.production", ".env.test", ".env.backup", ".env.2026"]
KEPT_NAMES = [".env.example"]


def ignored(rules: Path, names: list[str], tmp_path: Path) -> set[str]:
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    shutil.copyfile(rules, repo / ".gitignore")
    proc = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--no-index", *names], capture_output=True, text=True, check=False
    )
    assert proc.returncode in (0, 1), proc.stderr
    return set(proc.stdout.split())


@pytest.mark.parametrize("which", sorted(IGNORE_FILES))
def test_every_env_file_is_ignored_and_the_example_is_not(which, tmp_path):
    result = ignored(IGNORE_FILES[which], SECRET_NAMES + KEPT_NAMES, tmp_path)
    assert result == set(SECRET_NAMES), which


def test_the_repository_tracks_its_env_example():
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    proc = subprocess.run(
        ["git", "ls-files", "src/decktalk/template/starter/env.example"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.stdout.strip() == "src/decktalk/template/starter/env.example"
