"""Every generator under `scripts/` runs through one runner, so a caller never guesses its flags.

CI writes with `--write` on a release pull request and checks with `--check` everywhere else. A
generator that wrote when run with no flag broke the release job that passed `--write` to it, so
every `build_*.py` hands its files to `scripts/generated.py`, and running one with neither mode or
with both is refused before it reads or writes anything.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

import generated
from support.paths import REPO

GENERATORS = sorted((REPO / "scripts").glob("build_*.py"))

USAGE_ERROR = 2
"""The exit code argparse gives a command line it refuses."""


def invoked(script: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    """The generator run by the interpreter the tests run under, from the repository root."""
    return subprocess.run((sys.executable, str(script), *flags), cwd=REPO, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("script", GENERATORS, ids=lambda path: path.name)
def test_a_generator_hands_its_files_to_the_runner_and_exits_with_its_answer(script: Path) -> None:
    """A generator that dropped the runner's exit code would pass `--check` over any stale file."""
    text = script.read_text(encoding="utf-8")
    assert "sys.exit(generated.run(" in text, f"{script.name} does not exit with what scripts/generated.py says"


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


def test_a_check_over_a_stale_file_and_an_orphan_exits_1_and_names_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exit code is the whole contract of `--check`, and the orphan branch is what refuses an extra schema."""
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "kept.json").write_text("old\n", encoding="utf-8")
    (owned / "orphan.json").write_text("left\n", encoding="utf-8")
    monkeypatch.setattr(generated, "ROOT", tmp_path)
    monkeypatch.setitem(sys.modules, "__main__", types.ModuleType("__main__", "A generator under test."))
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(tmp_path / "build_test.py"), raising=False)
    monkeypatch.setattr(sys, "argv", ["build_test.py", "--check"])
    code = generated.run(lambda: {owned / "kept.json": "new\n"}, owned=[owned / "*.json"])
    said = capsys.readouterr().out.splitlines()
    assert code == 1
    assert said == [
        generated.STALE.format(path="owned/kept.json", reason="line 1 differs from its source", script="build_test.py"),
        generated.STALE.format(path="owned/orphan.json", reason=generated.GONE, script="build_test.py"),
    ]
    assert (owned / "orphan.json").exists() and (owned / "kept.json").read_text(encoding="utf-8") == "old\n"
