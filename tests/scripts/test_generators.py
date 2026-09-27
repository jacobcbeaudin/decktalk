"""Every generator under `scripts/` runs through one runner, so a caller never guesses its flags.

CI writes with `--write` on a release pull request and checks with `--check` everywhere else. A
generator that wrote when run with no flag broke the release job that passed `--write` to it, so
every `build_*.py` hands its files to `scripts/generated.py`, and running one with neither mode or
with both is refused before it reads or writes anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from support.paths import REPO

sys.path.insert(0, str(REPO / "scripts"))
import generated  # noqa: E402

GENERATORS = sorted((REPO / "scripts").glob("build_*.py"))

USAGE_ERROR = 2
"""The exit code argparse gives a command line it refuses."""


def invoked(script: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    """The generator run by the interpreter the tests run under, from the repository root."""
    return subprocess.run((sys.executable, str(script), *flags), cwd=REPO, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("script", GENERATORS, ids=lambda path: path.name)
def test_a_generator_hands_its_files_to_the_runner(script: Path) -> None:
    assert "generated.run(" in script.read_text(encoding="utf-8"), f"{script.name} does not use scripts/generated.py"


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


def test_a_splice_rewrites_between_the_markers_alone() -> None:
    page = "head\n<a>\nold\n<b>\ntail\n"
    assert generated.splice(page, ("<a>", "<b>"), "\nnew\n", where=REPO / "page.md") == "head\n<a>\nnew\n<b>\ntail\n"


def test_a_splice_with_a_marker_missing_is_refused() -> None:
    with pytest.raises(SystemExit, match="page.md has no <a> ... <b> block"):
        generated.splice("head\n<a>\ntail\n", ("<a>", "<b>"), "new", where=REPO / "page.md")


def test_a_stale_file_names_the_first_line_that_moved(tmp_path: Path) -> None:
    committed = tmp_path / "page.md"
    committed.write_text("one\ntwo\n", encoding="utf-8")
    assert generated.differs(committed, "one\ntwo\n") is None
    assert generated.differs(committed, "one\nthree\n") == "line 2 differs from its source"
    assert generated.differs(committed, "one\ntwo\nthree\n") == "its length differs from its source"
    assert generated.differs(tmp_path / "missing.md", "one\n") == "it is not committed"
