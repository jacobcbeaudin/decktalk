"""The check table in `scripts/check.py`, held to what its rows promise a run does.

The release pull request regenerates with `--group generated --write`, and the rehearsal runs the
same command on every pull request. A write that skipped a generator the check runs would leave a
file stale on the one branch nobody else pushes to, so the derivation is held here row by row. A row
that declares a tool has to fetch it before its checks run, and a suite that still finds no tool has
to fail, because a declared need with nothing behind it once let three suites pass with every test
skipped.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

import check
from support import tools
from support.paths import REPO


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
        check.BY_NAME["generated"].commands[-1],
        ("uv", "run", "scripts/check_wheel.py"),
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
        )


# ---- the tools cache belongs to one leg -----------------------------------------------------------


def test_every_leg_that_fetches_a_tool_keeps_it_under_a_key_of_its_own() -> None:
    rows = check.legs(check.GROUPS)
    keyed = {(row["group"], row["runs-on"]): row["cache"] for row in rows if row["cache"]}
    assert keyed, "no leg caches the tools it fetches"
    assert len(set(keyed.values())) == len(keyed), "two legs share one tools cache"


def test_a_leg_that_fetches_nothing_to_keep_has_no_cache() -> None:
    for row in check.legs(check.GROUPS):
        cached = any(check.NEEDS[tool].cached for tool in row["tools"])
        assert bool(row["cache"]) == cached, row["leg"]


def test_the_key_names_the_two_pins_that_decide_the_download() -> None:
    key = check.tools_key(check.BY_NAME["e2e"], check.LINUX)
    assert check.locked_version("playwright") in key
    assert check.ffmpeg_pin() in key
    assert "e2e" in key and check.LINUX in key


# ---- the workflow reads the table and adds nothing of its own -------------------------------------

WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
TOOLS_READ = re.compile(r"contains\(matrix\.tools, '([^']+)'\)")
MOMENT_CHOSEN = re.compile(r"(?:&&|\|\|) '([a-z]+)'")
"""A moment the plan job's expression can yield, which is a quoted word after `&&` or `||`."""


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps() -> list[dict[str, Any]]:
    return [step for job in workflow()["jobs"].values() for step in job.get("steps", [])]


def test_the_workflow_acts_on_exactly_the_needs_the_table_says_it_provides() -> None:
    read = set(TOOLS_READ.findall(WORKFLOW.read_text(encoding="utf-8")))
    assert read == {name for name, need in check.NEEDS.items() if need.workflow}


def test_every_node_the_workflow_installs_is_the_tables() -> None:
    versions = [step["with"]["node-version"] for step in steps() if "setup-node" in step.get("uses", "")]
    assert versions, "the workflow installs no Node, so this test says nothing"
    assert all(version in ("${{ matrix.node }}", check.NODE) for version in versions), versions
    assert all(row["node"] == check.NODE for row in check.legs(check.GROUPS))


def test_every_moment_the_workflow_computes_is_one_the_table_gates_at() -> None:
    plan = workflow()["jobs"]["plan"]
    moment = next(step for step in plan["steps"] if step.get("id") == "table")["env"]["MOMENT"]
    assert set(MOMENT_CHOSEN.findall(moment)) == set(check.MOMENTS)
    assert {when for group in check.GROUPS for when in group.when} <= set(check.MOMENTS)


def test_every_runner_the_workflow_names_is_one_the_table_names() -> None:
    labels = {job["runs-on"] for job in workflow()["jobs"].values() if not job["runs-on"].startswith("${{")}
    assert labels == {check.LINUX}
    pattern = next(step["with"]["pattern"] for step in steps() if "download-artifact" in step.get("uses", ""))
    assert f"-{check.LINUX}-" in pattern


def test_the_tools_cache_is_saved_to_the_key_it_is_restored_from() -> None:
    caches = [step for step in steps() if "actions/cache/" in step.get("uses", "")]
    assert [step["uses"].split("@")[0] for step in caches] == ["actions/cache/restore", "actions/cache/save"]
    restore, save = caches
    assert restore["with"] == save["with"]
    assert restore["with"]["key"] == "${{ matrix.cache }}"


def test_a_row_that_gates_at_a_moment_no_run_is_refused() -> None:
    with pytest.raises(ValueError, match="no run of ci.yml is"):
        check.Group(
            name="x",
            why="x.",
            commands=(),
            runners=(),
            pythons=(),
            tools=(),
            timeout=1,
            when=("release",),
        )


# ---- cue timing ----------------------------------------------------------------------------------


def test_the_linux_e2e_row_reports_timing_until_it_is_trusted_to_gate() -> None:
    (command,) = check.BY_NAME["e2e"].commands
    assert (check.REPORT_TIMING in command) is not check.LINUX_GATES_TIMING


# ---- one row per contract ------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(check.ELSEWHERE))
def test_a_file_the_unit_suite_leaves_elsewhere_runs_in_its_row_alone(path: str) -> None:
    assert (REPO / path).is_file(), f"{path} is left to another row and is not there any more"
    runs = [group.name for group in check.GROUPS for command in group.commands if path in command]
    assert runs == [check.ELSEWHERE[path]], runs
    (unit,) = check.BY_NAME["unit"].commands
    assert f"--ignore={path}" in unit


def test_only_the_unit_row_runs_in_parallel() -> None:
    parallel = [group.name for group in check.GROUPS for command in group.commands if "-n" in command]
    assert parallel == ["unit"]


def test_the_scaffold_build_judges_a_release_after_it_is_cut() -> None:
    assert check.BY_NAME["scaffold"].when == ("schedule",)


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


def test_biome_json_names_the_schema_of_the_biome_package_json_pins() -> None:
    """Biome is pinned once, in `package.json`, and a schema of another release validates other rules."""
    pinned = json.loads((REPO / "package.json").read_text(encoding="utf-8"))["devDependencies"]["@biomejs/biome"]
    schema = json.loads((REPO / "biome.json").read_text(encoding="utf-8"))["$schema"]
    assert schema == f"https://biomejs.dev/schemas/{pinned}/schema.json"
