"""The check table in `scripts/check.py`, held to what its rows promise a run does.

The release pull request regenerates with `--group generated --write`, and the rehearsal runs the
same command on every pull request. A write that skipped a generator the check runs would leave a
file stale on the one branch nobody else pushes to, so the derivation is held here row by row. A row
that declares a tool has to fetch it before its checks run, and a suite that still finds no tool has
to fail, because a declared need with nothing behind it once let three suites pass with every test
skipped.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from support import tools
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
        assert command[-1] == check.WRITE
        assert check.CHECK not in command


def test_a_write_keeps_what_prepares_the_machine_in_its_place() -> None:
    group = check.BY_NAME["generated"]
    written = check.writer(group).steps
    assert written[: len(group.preparations)] == group.preparations
    assert check.NPM_CI in group.preparations and check.INSTALL in group.preparations


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
    assert rehearsal.steps[0] == check.NPM_CI
    assert "history" in rehearsal.tools


# ---- every need a row declares has something behind it ------------------------------------------


@pytest.mark.parametrize("group", check.GROUPS, ids=lambda group: group.name)
def test_every_tool_a_row_declares_is_prepared_before_its_checks(group) -> None:
    """A declared tool with no command behind it is how the browser and e2e rows once skipped every test."""
    for tool in group.tools:
        prepare = check.NEEDS[tool].prepare
        if prepare is not None:
            prepared = group.steps[: len(group.preparations)]
            assert prepare in prepared, f"{group.name} declares {tool} and never fetches it"


@pytest.mark.parametrize("marker", ["browser", "media", "e2e", "scaffold", "platform"])
def test_every_suite_that_needs_a_tool_runs_after_the_install(marker: str) -> None:
    rows = [group for group in check.GROUPS if any(marker in command for command in group.commands)]
    assert rows, f"no row runs the {marker} suite"
    for group in rows:
        assert check.INSTALL in group.preparations, f"{group.name} runs -m {marker} with nothing that fetches its tools"


def test_every_suite_prints_the_reason_of_every_skip() -> None:
    suites = [command for group in check.GROUPS for command in group.steps if "pytest" in command]
    assert suites
    assert all("-rs" in command for command in suites)


def test_a_row_that_names_a_need_nothing_provides_is_refused() -> None:
    with pytest.raises(ValueError, match="no row can provide"):
        check.Group(
            name="x",
            why="x.",
            commands=(),
            runners=(),
            pythons=(),
            tools=("docker",),
            timeout=1,
            when=(),
            wall_seconds=0,
        )


# ---- a suite the run named fails when its tool is missing -----------------------------------------
#
# The rows above fetch every tool before a suite starts, and `tests/support/tools.py` is what makes a
# suite that still finds no tool fail rather than skip, so the two halves of the promise sit together.


def doctor_reports(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]) -> None:
    """Make `decktalk doctor --json` answer with these tool rows and nothing else."""

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=(), returncode=0, stdout=json.dumps({"tools": rows}), stderr="")

    monkeypatch.setattr(tools.subprocess, "run", run)


def test_a_tool_doctor_reports_no_version_for_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor_reports(monkeypatch, [{"tool": "chromium", "version": None}, {"tool": "ffmpeg", "version": "8.1.2"}])
    assert tools.missing(("chromium", "ffmpeg"), tmp_path) == ["chromium"]


def test_a_tool_doctor_never_names_is_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor_reports(monkeypatch, [])
    assert tools.missing(("ffmpeg",), tmp_path) == ["ffmpeg"]


def test_a_missing_tool_fails_the_run_and_names_the_command_that_fetches_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doctor_reports(monkeypatch, [{"tool": "chromium", "version": "153"}])
    with pytest.raises(pytest.fail.Exception, match="ffmpeg") as failed:
        tools.require(("chromium", "ffmpeg"), tmp_path)
    assert tools.FETCH in str(failed.value)


def test_a_machine_that_holds_every_tool_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    doctor_reports(monkeypatch, [{"tool": "chromium", "version": "153"}, {"tool": "ffmpeg", "version": "8.1.2"}])
    tools.require(("chromium", "ffmpeg"), tmp_path)


def test_a_named_suite_whose_fixture_finds_no_tool_fails_rather_than_skips(pytester: pytest.Pytester) -> None:
    """The collection hook admits a marked test only when the run named its marker, so the fixture fails it."""
    pytester.makepyfile(
        """
        import pytest

        from support.tools import absent

        @pytest.fixture
        def page():
            pytest.fail(absent("chromium", "no executable"))

        @pytest.mark.browser
        def test_a_page(page):
            pass
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*chromium is not usable on this machine*decktalk install*"])
