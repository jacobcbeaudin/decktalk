"""What `scripts/check.py --write` runs, which is read from the check table rather than listed twice.

The release pull request regenerates with `--group generated --write`, and the rehearsal runs the
same command on every pull request. A write that skipped a generator the check runs would leave a
file stale on the one branch nobody else pushes to, so the derivation is held here row by row.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from support.paths import REPO


def _check() -> ModuleType:
    """`scripts/check.py` as a module, which is the only way to reach a file outside the package."""
    spec = importlib.util.spec_from_file_location("check", REPO / "scripts" / "check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check"] = module
    spec.loader.exec_module(module)
    return module


check = _check()


def scripts(commands: tuple[tuple[str, ...], ...]) -> list[str]:
    """The script each command runs, in order, for the commands that run one."""
    return [Path(part).name for command in commands for part in command if part.startswith("scripts/")]


def test_every_generator_the_check_runs_is_written() -> None:
    group = check.BY_NAME["generated"]
    checked = [name for name in scripts(group.commands) if name.startswith(check.GENERATES)]
    assert scripts(check.writer(group).commands) == checked


def test_a_write_passes_write_where_the_check_passed_check() -> None:
    for command in check.writer(check.BY_NAME["generated"]).commands:
        if command in check.PREPARES:
            continue
        assert command[-1] == check.WRITE
        assert check.CHECK not in command


def test_a_write_keeps_what_prepares_the_machine_in_its_place() -> None:
    written = check.writer(check.BY_NAME["generated"]).commands
    assert written[: len(check.PREPARES)] == check.PREPARES


def test_a_generator_that_needs_more_to_check_needs_the_same_to_write() -> None:
    command = ("uv", "run", "--with", "fonttools", "python", "scripts/build_assets.py", "--check")
    assert check.writing(command) == (*command[:-1], "--write")


@pytest.mark.parametrize(
    "command",
    [
        ("uv", "run", "python", "scripts/check_docs_links.py", "--check"),
        ("uv", "run", "scripts/check_wheel.py", "--check"),
        ("uv", "run", "ruff", "check", "src"),
    ],
    ids=["docs links", "wheel", "ruff"],
)
def test_a_command_that_only_judges_has_no_write(command: tuple[str, ...]) -> None:
    assert check.writing(command) is None


@pytest.mark.parametrize("name", ["lint", "unit", "wheel"])
def test_a_group_that_generates_nothing_refuses_to_write(name: str) -> None:
    with pytest.raises(SystemExit, match="generates nothing"):
        check.writer(check.BY_NAME[name])


def test_the_rehearsal_row_has_the_node_packages_and_the_history_it_reads() -> None:
    # The rehearsal asks scripts/next_version.mjs, which imports release-please from node_modules and
    # reads every commit since the last tag, so a fresh runner needs both before the script starts.
    rehearsal = check.BY_NAME["rehearsal"]
    assert rehearsal.commands[0] == check.NPM_CI
    assert "history" in rehearsal.tools
