"""Every generator under `scripts/` takes the same two modes, so a caller never guesses its flags.

CI writes with `--write` on a release pull request and checks with `--check` everywhere else. A
generator that wrote when run with no flag broke the release job that passed `--write` to it, so
both modes are held here for every `build_*.py`, and running one with neither or with both is
refused before it reads or writes anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from support.paths import REPO

GENERATORS = sorted((REPO / "scripts").glob("build_*.py"))

USAGE_ERROR = 2
"""The exit code argparse gives a command line it refuses."""


def invoked(script: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    """The generator run by the interpreter the tests run under, from the repository root."""
    return subprocess.run((sys.executable, str(script), *flags), cwd=REPO, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("script", GENERATORS, ids=lambda path: path.name)
def test_a_generator_offers_write_and_check(script: Path) -> None:
    text = script.read_text(encoding="utf-8")
    assert '"--write"' in text, f"{script.name} has no --write"
    assert '"--check"' in text, f"{script.name} has no --check"
    assert "add_mutually_exclusive_group(required=True)" in text, f"{script.name} must require one mode"


@pytest.mark.parametrize("script", GENERATORS, ids=lambda path: path.name)
def test_a_generator_run_with_no_mode_is_refused(script: Path) -> None:
    result = invoked(script)
    assert result.returncode == USAGE_ERROR, f"{script.name} ran with no mode: {result.stdout}"
    assert "--write" in result.stderr
    assert "--check" in result.stderr


@pytest.mark.parametrize("script", GENERATORS, ids=lambda path: path.name)
def test_a_generator_run_with_both_modes_is_refused(script: Path) -> None:
    result = invoked(script, "--write", "--check")
    assert result.returncode == USAGE_ERROR, f"{script.name} ran with both modes: {result.stdout}"


def test_the_list_of_generators_is_not_empty() -> None:
    assert GENERATORS, "no scripts/build_*.py found"
