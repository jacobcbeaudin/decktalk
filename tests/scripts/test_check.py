"""The check table in `scripts/check.py`, held to what its rows promise a run does.

The release pull request regenerates with `--group generated --write`, and the rehearsal runs the
same command on every pull request. A write that skipped a generator the check runs would leave a
file stale on the one branch nobody else pushes to, so the derivation is held here row by row. A row
that declares a tool has to fetch it before its checks run, and a suite that still finds no tool has
to fail, because a declared need with nothing behind it lets a suite pass with every test skipped.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

import check
from support import tools
from support.paths import REPO


def suites(group: check.Group) -> list[check.Suite]:
    """Every pytest run one row makes."""
    return [command for command in group.steps if isinstance(command, check.Suite)]


def scripts(commands: tuple[check.Command, ...]) -> list[str]:
    """The script each command runs, in order, for the commands that run one."""
    return [Path(part).name for command in commands for part in command.argv if part.startswith("scripts/")]


def test_every_generator_the_check_runs_is_written() -> None:
    group = check.BY_NAME["generated"]
    checked = [name for name in scripts(group.commands) if name.startswith(check.GENERATES)]
    assert scripts(check.writer(group).commands) == checked


def test_a_write_passes_write_where_the_check_passed_check() -> None:
    for command in check.writer(check.BY_NAME["generated"]).commands:
        assert command.argv[-1] == check.WRITE
        assert check.CHECK not in command.argv


def test_a_write_keeps_what_prepares_the_machine_in_its_place() -> None:
    group = check.BY_NAME["generated"]
    written = check.writer(group).steps
    assert written[: len(group.preparations)] == group.preparations
    assert check.NPM_CI in group.preparations


@pytest.mark.parametrize(
    "command",
    [
        check.BY_NAME["generated"].commands[-1],
        check.Run((*check.PYTHON, "scripts/build_example.py", "--check")),
        check.Suite(),
    ],
    ids=["docs links", "a script named like a generator", "a suite"],
)
def test_a_command_that_only_judges_has_no_write(command: check.Command) -> None:
    """Only a `Generator` writes, so a command is never made a writer by how its script is named."""
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


@pytest.mark.parametrize("script", sorted((REPO / "scripts").glob("build_*.py")), ids=lambda path: path.name)
def test_every_generator_is_checked_in_a_row_that_prepares_what_it_runs(script: Path) -> None:
    """A generator no row runs is never held, and the samples' generator runs DeckTalk, which needs ffmpeg."""
    rows = [group for group in check.GROUPS for command in group.commands if f"scripts/{script.name}" in command.argv]
    assert rows, f"no row checks {script.name}"
    if script.stem == check.SAMPLES:
        assert all("ffmpeg" in group.tools for group in rows), [group.name for group in rows]


@pytest.mark.parametrize(
    "group", [check.BY_NAME["generated"], check.BY_NAME["rehearsal"]], ids=lambda group: group.name
)
def test_a_row_that_runs_the_generators_fetches_chromium_only_when_one_launches_it(group) -> None:
    # Both rows run every generator, and the rehearsal through `--group generated` itself. Fetching a
    # browser no generator launches costs every pull request the download, and leaving out one a
    # generator launches fails the row on a fresh runner.
    launches = any(imports_playwright(check.ROOT / "scripts" / f"{name}.py") for name in check.GENERATORS)
    assert ("chromium" in group.tools) == launches


def imports_playwright(script: Path) -> bool:
    """Whether a script imports Playwright, which is how a generator would launch Chromium."""
    tree = ast.parse(script.read_text(encoding="utf-8"))
    names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    names += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    return any(name.split(".")[0] == "playwright" for name in names)


# ---- every need a row declares has something behind it ------------------------------------------


@pytest.mark.parametrize("group", check.GROUPS, ids=lambda group: group.name)
def test_every_tool_a_row_declares_is_prepared_before_its_checks(group) -> None:
    """A declared tool with no command behind it would leave a row's suite to skip every test it holds."""
    for tool in group.tools:
        prepare = check.NEEDS[tool].prepare
        if prepare is not None:
            prepared = group.steps[: len(group.preparations)]
            assert prepare in prepared, f"{group.name} declares {tool} and never fetches it"


def test_setup_runs_every_preparation_of_every_row_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """`pixi run setup` readies a new clone for every row, through the `--setup` the command line takes.

    A row that runs outside the default environment enters its own, so setup installs that one too,
    and the row then needs the network only when it says so.
    """
    ran: list[check.Command] = []
    monkeypatch.setattr(check, "run", lambda command, _env=(): ran.append(command) or True)
    monkeypatch.setattr("sys.argv", ["check.py", check.SETUP])
    assert check.main() == 0
    every = {command for group in check.GROUPS for command in group.preparations}
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", check.DEFAULT_ENVIRONMENT)
    entered = {check.entered(group)[-1] for group in check.GROUPS if check.entered(group)}
    every |= {check.Run(("pixi", "install", "--locked", "-e", name)) for name in entered}
    assert "floors" in entered
    assert set(ran) == every and len(ran) == len(every)


def test_no_task_spells_a_command_the_table_owns() -> None:
    """A task that ran `npm ci` or `decktalk install` itself would be a second home of a row's preparation."""
    tasks = pixi()["tasks"]
    spelled = [str(task.get("cmd", "")) if isinstance(task, dict) else str(task) for task in tasks.values()]
    owned = [" ".join(command.argv) for command in check.setup_commands()]
    assert not [(task, command) for task in spelled for command in owned if command in task]


def test_a_group_opens_with_the_command_that_runs_it_again_in_its_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = check.BY_NAME["unit"]
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME", raising=False)
    assert check.reproduce(group) == "pixi run check --group unit"
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", "py313")
    assert check.reproduce(group, " --write") == "pixi run -e py313 check --group unit --write"


@pytest.mark.parametrize(
    ("current", "inside"),
    [
        pytest.param("floors", (), id="a leg already in the row's environment"),
        pytest.param(check.DEFAULT_ENVIRONMENT, ("pixi", "run", "--locked", "-e", "floors"), id="a local run"),
    ],
)
def test_a_row_runs_in_its_own_environment_wherever_it_is_started(
    monkeypatch: pytest.MonkeyPatch, current: str, inside: tuple[str, ...]
) -> None:
    """The floors row run from the default environment would otherwise judge the newest versions as the floors."""
    started: list[tuple[str, ...]] = []
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", current)
    monkeypatch.setattr(check.shutil, "which", lambda tool, path=None: tool)
    monkeypatch.setattr(check.subprocess, "call", lambda argv, **_kwargs: started.append(tuple(argv)) or 0)
    floors = check.BY_NAME["floors"]
    assert check.run_group(floors)
    assert started == [(*inside, *command.argv) for command in floors.steps]


def test_a_preparation_fetches_only_what_the_tools_cache_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row's own run installs the Node packages, so a preparation that did too installed them twice."""
    ran: list[check.Command] = []
    monkeypatch.setattr(check, "run", lambda command, _env, _inside=(): ran.append(command) or True)
    both = replace(check.BY_NAME["generated"], tools=("npm", "chromium"))
    assert check.run_preparations((both,)) == 0
    assert ran == [check.INSTALL]


def test_a_shown_command_drops_the_checkout_only_where_a_path_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkout at `/src` leaves `runtime/src/` inside a glob alone, which CONTRIBUTING.md would otherwise lose."""
    monkeypatch.setattr(check, "ROOT", Path("/src"))
    root = check.ROOT
    command = ("node", "tests/decktalk/runtime/src/*.test.ts", f"{root}/scripts/x.sh", f"--junitxml={root}/out/u.xml")
    assert check.shell(command) == "node tests/decktalk/runtime/src/*.test.ts scripts/x.sh --junitxml=out/u.xml"


def test_a_shown_environment_value_drops_the_checkout_only_from_its_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The values a group prints follow the same rule as its commands."""
    monkeypatch.setattr(check, "ROOT", Path("/src"))
    monkeypatch.setattr(check, "run", lambda _command, _env, _inside=(): True)
    env = (("COVERAGE_FILE", f"{check.ROOT}/.coverage"), ("GLOB", "tests/runtime/src/*.ts"))
    assert check.run_group(replace(check.BY_NAME["lint"], env=env))
    shown = capsys.readouterr().out
    assert "   COVERAGE_FILE=.coverage\n" in shown
    assert "   GLOB=tests/runtime/src/*.ts\n" in shown


@pytest.mark.parametrize("marker", tools.INSTALLED)
def test_every_suite_that_needs_a_tool_runs_after_the_install(marker: str) -> None:
    rows = [group for group in check.GROUPS if any(suite.marker == marker for suite in suites(group))]
    assert rows, f"no row runs the {marker} suite"
    for group in rows:
        assert check.INSTALL in group.preparations, f"{group.name} runs -m {marker} with nothing that fetches its tools"


def test_every_suite_prints_the_reason_of_every_skip() -> None:
    every = [suite for group in check.GROUPS for suite in suites(group)]
    assert every
    assert all("-rs" in suite.argv for suite in every)


@pytest.mark.parametrize(
    ("tools", "when", "match"),
    [
        pytest.param(("kubernetes",), (), "no row can provide", id="a need nothing provides"),
        pytest.param((), ("release",), "no run of ci.yml is", id="a moment no run is"),
    ],
)
def test_a_row_that_names_what_nothing_answers_is_refused(
    tools: tuple[str, ...], when: tuple[str, ...], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        check.Group(name="x", why="x.", commands=(), runners=(), tools=tools, timeout=1, when=when)


def test_a_row_that_runs_in_an_environment_pyproject_lacks_is_refused() -> None:
    with pytest.raises(ValueError, match="does not declare"):
        check.Group(name="x", why="x.", commands=(), runners=(), tools=(), timeout=1, when=(), environments=("py399",))


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


def test_the_key_names_the_runner_image_and_the_two_pins_that_decide_the_download() -> None:
    key = check.tools_key(check.BY_NAME["e2e"], check.LINUX)
    assert f"-playwright-{check.playwright_minor()}-" in key
    assert check.ffmpeg_pin() in key
    assert "e2e" in key and check.LINUX in key
    assert "," not in key, "actions/cache refuses a key with a comma in it"


def test_the_playwright_minor_is_the_floor_of_the_range_pyproject_names() -> None:
    """The cache key reads the one home of the Playwright minor, so a Renovate bump of it moves the key."""
    [playwright] = [
        Requirement(line) for line in manifest()["project"]["dependencies"] if line.startswith("playwright")
    ]
    assert f">={check.playwright_minor()}" in {str(spec) for spec in playwright.specifier}


def test_the_npm_key_moves_when_the_lockfile_does(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lockfile = tmp_path / "package-lock.json"
    monkeypatch.setattr(check, "NODE_LOCKFILE", lockfile)
    group = check.BY_NAME["lint"]
    lockfile.write_text("{}")
    before = check.npm_key(group, check.LINUX)
    lockfile.write_text('{"lockfileVersion": 3}')
    assert check.npm_key(group, check.LINUX) != before


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


# ---- every leg runs in a Pixi environment pyproject.toml declares -------------------------------

RELEASE = REPO / ".github" / "workflows" / "release.yml"
MANIFEST = REPO / "pyproject.toml"
LOCK = REPO / "pixi.lock"


def manifest() -> dict[str, Any]:
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def pixi() -> dict[str, Any]:
    """The `[tool.pixi]` tables of pyproject.toml, which are the one home of every environment."""
    return manifest()["tool"]["pixi"]


SETUP_ACTION = "$/.github/actions/setup"
ACTION = REPO / ".github" / "actions" / "setup" / "action.yml"


def setups() -> list[tuple[str, dict[str, Any]]]:
    """Every step of ci.yml and release.yml that installs Pixi, with the job it belongs to."""
    return [(name, step) for name, step, _ in setups_in_jobs()]


def setups_in_jobs() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Every step that installs Pixi, with the name of its job and the job itself."""
    found = []
    for path in (WORKFLOW, RELEASE):
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items():
            found += [
                (name, step, job) for step in job.get("steps", []) if step.get("uses", "").startswith(SETUP_ACTION)
            ]
    return found


def test_no_workflow_installs_a_python_or_node_of_its_own() -> None:
    for path in (WORKFLOW, RELEASE):
        uses = [
            step.get("uses", "")
            for job in yaml.safe_load(path.read_text())["jobs"].values()
            for step in job.get("steps", [])
        ]
        assert not [
            use
            for use in uses
            if use.split("@")[0] in ("astral-sh/setup-uv", "actions/setup-node", "actions/setup-python")
        ]


def test_the_setup_action_is_the_one_place_setup_pixi_is_named() -> None:
    """Six jobs install Pixi, and one action holds the pin and the rules they share."""
    workflows = [
        path for path in (REPO / ".github").rglob("*.yml") if "setup-pixi@" in path.read_text(encoding="utf-8")
    ]
    assert workflows == [ACTION]
    [step] = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    assert step["with"]["pixi-version"] == "v${{ inputs.pixi-version }}"


EXPRESSION = re.compile(r"^\$\{\{ (.+) \}\}$")
COMPARED = re.compile(r"^inputs\.([\w-]+) != ''$")
READ = re.compile(r"^inputs\.([\w-]+)$")


def resolved(value: object, given: dict[str, str]) -> object:
    """One `with:` value of the action as GitHub resolves it for a caller that passed `given`.

    It reads the two forms the action writes, `inputs.<name> != ''` and `inputs.<name>`, joined by
    `&&`, which is enough to say what setup-pixi receives. Any other expression fails the test, so
    the action cannot grow a form this test does not read.
    """
    if not isinstance(value, str) or not (found := EXPRESSION.match(value)):
        return value
    result: object = True
    for term in found[1].split(" && "):
        if compared := COMPARED.match(term):
            result = given[compared[1]] != ""
        elif read := READ.match(term):
            result = {"true": True, "false": False}.get(given[read[1]], given[read[1]])
        else:
            raise AssertionError(f"the setup action writes {term!r}, which this test cannot resolve")
        if result is False or result == "":
            return False
    return result


def setup_pixi_inputs(caller: dict[str, Any]) -> dict[str, object]:
    """What setup-pixi receives when `caller` uses the action, with the action's own defaults filled in."""
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    given = {name: str(spec.get("default", "")) for name, spec in action["inputs"].items()}
    given |= {name: str(value) for name, value in caller.get("with", {}).items()}
    [step] = action["runs"]["steps"]
    return {name: resolved(value, given) for name, value in step["with"].items()}


INSTALL_ONLY = ("locked", "frozen", "cache", "environments")
"""The setup-pixi inputs it refuses without an install (`validateInputs` in its src/options.ts)."""
LOCK_ONLY = ("cache-write",)
"""The setup-pixi inputs it refuses without a pixi.lock in the job (`getOptions` in its src/options.ts)."""
FREE = ("pixi-version", "run-install", "activate-environment")
"""The setup-pixi inputs the action passes that setup-pixi takes in any job."""


@pytest.mark.parametrize("job", sorted({job for job, _ in setups()}))
def test_every_job_hands_setup_pixi_inputs_it_accepts(job: str) -> None:
    """setup-pixi 0.11.3 refuses an input that needs an install in a job with none, or a lock file in a job with none.

    The job has a pixi.lock only when it checks the repository out. A value the test cannot resolve,
    such as `${{ env.SAVE_CACHES }}`, may be true, so it counts as true. A job that installs an
    environment refuses a stale lock, so the environment is the one pixi.lock pins.
    """
    for caller, steps_of_job in [(step, job_["steps"]) for name, step, job_ in setups_in_jobs() if name == job]:
        received = setup_pixi_inputs(caller)
        assert set(received) <= {*INSTALL_ONLY, *LOCK_ONLY, *FREE}, (
            "the action passes an input this test has no rule for"
        )
        has_lock = any(step.get("uses", "").startswith("actions/checkout@") for step in steps_of_job)
        if received["run-install"] is False:
            assert [key for key in INSTALL_ONLY if received.get(key, False) not in (False, "")] == [], received
        else:
            assert received["locked"] is True and has_lock, received
        if not has_lock:
            assert [key for key in LOCK_ONLY if received.get(key, False) is not False] == [], received


@pytest.mark.parametrize(
    ("required", "read"),
    [pytest.param(">=9.8.7", "9.8.7", id="a floor"), pytest.param(">=9.8.7,<10", None, id="a range")],
)
def test_the_pixi_version_is_the_floor_requires_pixi_names_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, required: str, read: str | None
) -> None:
    """setup-pixi and install.sh take one version, so a range in `requires-pixi` is refused rather than cut."""
    monkeypatch.setattr(check, "manifest", lambda: {"tool": {"pixi": {"workspace": {"requires-pixi": required}}}})
    if read is None:
        with pytest.raises(SystemExit, match="one floor"):
            check.pixi_version()
    else:
        assert check.pixi_version() == read


def test_every_pixi_a_job_installs_is_read_from_requires_pixi() -> None:
    """No job writes the Pixi version: each hands on what `check.py --pixi-version` read from pyproject.toml."""
    named = {step["with"]["pixi-version"] for _, step in setups()}
    assert named == {
        "${{ needs.plan.outputs.pixi }}",
        "${{ steps.pixi.outputs.version }}",
        "${{ needs.build.outputs.pixi }}",
    }
    readers = [
        step["run"]
        for path in (WORKFLOW, RELEASE)
        for _, step in steps_of(path)
        if "--pixi-version" in step.get("run", "")
    ]
    assert len(readers) == 2 and all("python3 scripts/check.py --pixi-version" in run for run in readers)


def steps_of(path: Path) -> list[tuple[str, dict[str, Any]]]:
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    return [(name, step) for name, job in jobs.items() for step in job.get("steps", [])]


def test_the_release_builds_the_wheel_with_the_wheel_row_and_names_no_command_of_its_own() -> None:
    job = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]["build"]
    runs = [step for step in job["steps"] if "run" in step and "check.py --group" in step["run"]]
    assert [step["run"] for step in runs] == ["python scripts/check.py --group wheel"]
    assert set(runs[0]["env"]) == {"RELEASE_TAG"}


def release_jobs() -> dict[str, Any]:
    return yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]


def test_the_release_builds_the_conda_package_with_the_conda_row_before_pypi() -> None:
    """PyPI has no undo, so both artifacts are built and judged before the upload that cannot be taken back."""
    jobs = release_jobs()
    runs = [step["run"] for step in jobs["conda"]["steps"] if "run" in step]
    assert runs == ["python scripts/check.py --group playwright-pair,conda"]
    assert "if" not in jobs["conda"], "a candidate's release would reach PyPI with no conda build behind it"
    assert {"build", "conda"} <= set(jobs["publish"]["needs"])


def ancestors(jobs: dict[str, Any], name: str) -> set[str]:
    """Every job `name` waits on, directly or through another job."""
    needs = jobs[name].get("needs", [])
    direct = {needs} if isinstance(needs, str) else set(needs)
    return direct.union(*(ancestors(jobs, job) for job in direct))


def test_a_final_release_reaches_the_channel_before_pypi_and_attaches_after_both() -> None:
    """A prefix.dev package can be deleted and a PyPI version cannot, so the upload with no undo is the last."""
    jobs = release_jobs()
    assert jobs["publish-conda"]["if"] == "needs.build.outputs.prerelease == 'false'"
    assert {"build", "conda"} <= ancestors(jobs, "publish-conda")
    assert "publish" not in ancestors(jobs, "publish-conda")
    assert "publish-conda" in ancestors(jobs, "publish")
    for attach in ("attach", "attach-conda"):
        assert {"publish", "conda"} <= ancestors(jobs, attach), attach


STATUS = re.compile(r"\b(success|failure|always|cancelled)\(\)")
FACT = re.compile(r"\b(?:needs|github)\.[\w.-]+")
SUCCESS = "success"
"""The one result of a job, and the one conclusion of a run, that a condition of release.yml asks for."""
ASKED_OTHERWISE = re.compile(rf"\.(?:result|conclusion)(?! == '{SUCCESS}')")


def decided(condition: str, facts: dict[str, str]) -> bool:
    """Whether a condition is true in a run where each name in `facts` has its value.

    The condition is read as Python once each fact is written as its value: `&&`, `||`, `==` and
    `!=` read as they do in a workflow, and a run that nobody cancelled makes `!cancelled()` true. A
    name that is not in `facts`, such as an output of a job that did not run, is the empty string.
    """
    python = condition.removeprefix("${{").removesuffix("}}").replace("!cancelled()", "True")
    if leftover := STATUS.search(python):
        raise AssertionError(f"{leftover[0]} is a status function this test cannot read")
    if ASKED_OTHERWISE.search(python):
        raise AssertionError(
            f"{condition} asks a result for more than whether it is {SUCCESS}, which this test cannot read"
        )
    python = FACT.sub(lambda name: repr(facts.get(name[0], "")), python)
    python = python.replace("&&", " and ").replace("||", " or ")
    return bool(eval(python, {"__builtins__": {}}))


TRIGGER = {
    "github.event.workflow_run.conclusion": SUCCESS,
    "github.event.workflow_run.head_branch": "main",
    "github.event.workflow_run.event": "push",
}
"""The `ci` run that starts a release: green, a push, on `main`."""


def ran(tag: str, failing: frozenset[str] = frozenset()) -> set[str]:
    """The jobs of release.yml that GitHub runs for `tag`, when the jobs in `failing` fail.

    A job with no status function in its `if:` (or no `if:`) gets GitHub's implicit `success()`,
    which is true only when every job before it succeeded, the ones it does not name included. That
    rule is why a job after a job that may be skipped needs `!cancelled()` and its own test of
    `result`. A job that ran holds its result, and one that did not run holds None, which a
    condition reads as the empty string: every condition asks only whether a result is a success.
    """
    jobs = release_jobs()
    outputs = {
        "needs.release-please.outputs.release_created": "true",
        "needs.release-please.outputs.tag_name": tag,
        "needs.build.outputs.prerelease": "true" if "-" in tag else "false",
    }
    results: dict[str, str | None] = {}
    while len(results) < len(jobs):
        for name, job in jobs.items():
            needs = job.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            if name in results or not set(needs) <= set(results):
                continue
            facts = TRIGGER | {f"needs.{need}.result": results[need] or "" for need in needs}
            facts |= {key: value for key, value in outputs.items() if results.get(key.split(".")[1]) == SUCCESS}
            condition = str(job.get("if", ""))
            runs = decided(condition, facts) if condition else True
            if not STATUS.search(condition):
                runs = runs and all(results[before] == SUCCESS for before in ancestors(jobs, name))
            results[name] = None if not runs else "failure" if name in failing else SUCCESS
    return {name for name, result in results.items() if result is not None}


EVERY_JOB = {"release-please", "build", "conda", "publish-conda", "publish", "attach", "attach-conda"}


@pytest.mark.parametrize(
    ("tag", "failing", "expected"),
    [
        pytest.param(
            "v0.7.0-rc1",
            frozenset(),
            {"release-please", "build", "conda", "publish", "attach"},
            id="a candidate goes to PyPI and is attached, and never reaches the channel",
        ),
        pytest.param("v0.7.0", frozenset(), EVERY_JOB, id="a final goes to the channel, then PyPI, and attaches both"),
        pytest.param(
            "v0.7.0",
            frozenset({"publish-conda"}),
            {"release-please", "build", "conda", "publish-conda"},
            id="a final the channel refused stops before PyPI",
        ),
        pytest.param(
            "v0.7.0-rc1",
            frozenset({"conda"}),
            {"release-please", "build", "conda"},
            id="a package that failed its row stops a candidate before PyPI",
        ),
        pytest.param(
            "v0.7.0",
            frozenset({"publish"}),
            {"release-please", "build", "conda", "publish-conda", "publish"},
            id="a final PyPI refused is not attached",
        ),
    ],
)
def test_each_release_runs_exactly_the_jobs_it_needs(tag: str, failing: frozenset[str], expected: set[str]) -> None:
    """Every job's `needs` and `if:` together decide what a release does, so the test reads them all."""
    assert set(release_jobs()) == EVERY_JOB
    assert ran(tag, failing) == expected


def test_the_conda_upload_is_reviewed_and_uses_trusted_publishing_with_an_attestation() -> None:
    """A stored prefix.dev key would outlive the release, and an attestation needs the OIDC identity."""
    job = release_jobs()["publish-conda"]
    assert job["environment"] == "conda"
    assert job["permissions"] == {"id-token": "write"}
    text = yaml.safe_dump(job)
    assert "secrets." not in text and "PREFIX_API_KEY" not in text, text
    [upload] = [step["run"] for step in job["steps"] if "pixi upload prefix" in step.get("run", "")]
    assert "--generate-attestation" in upload and "--skip-existing" in upload, upload


def test_the_playwright_pair_row_runs_in_the_release_alone() -> None:
    """It reads anaconda.org, so an outage may stop a release and never turns `main` or a pull request red."""
    assert check.BY_NAME["playwright-pair"].when == ()
    assert all(check.BY_NAME["playwright-pair"] not in check.selected([], moment) for moment in check.MOMENTS)


def test_the_conda_row_gates_every_pull_request_on_every_platform() -> None:
    conda = check.BY_NAME["conda"]
    assert "pr" in conda.when
    assert conda.runners == check.EVERY_PLATFORM
    assert "network" in conda.tools


FLOOR = check.python_of(check.DEFAULT_ENVIRONMENT)
"""The lowest Python DeckTalk supports, which the default environment pins."""


def test_the_conda_package_is_built_on_the_floor_python() -> None:
    """The Python that builds the package is one CI tests, rather than the newest conda-forge holds."""
    assert pixi()["package"]["host-dependencies"]["python"] == f"{FLOOR}.*"


def test_pypi_offers_the_package_to_the_pythons_ci_tests_from_the_floor() -> None:
    """A floor Python dropped from the environments and left here would still be offered the package on PyPI."""
    assert SpecifierSet(manifest()["project"]["requires-python"]) == SpecifierSet(f">={FLOOR}")


def test_a_row_that_gates_no_run_says_a_release_runs_it() -> None:
    assert check.summary(check.BY_NAME["playwright-pair"])[3] == check.RELEASE_ONLY
    listed = check.epilog()
    assert "gates on ," not in listed and "run in ci as playwright-pair" not in listed


def test_the_matrix_is_every_group_on_each_of_its_runners_in_each_of_its_environments() -> None:
    """A leg the matrix lost is a Python or a platform no pull request runs, with every remaining leg green."""
    legs = [(row["group"], row["runs-on"], row["environment"]) for row in check.legs(check.GROUPS)]
    product = [(g.name, runner, env) for g in check.GROUPS for runner in g.runners for env in g.environments]
    assert sorted(legs) == sorted(product)
    assert {env for group, _, env in legs if group == "unit"} == set(check.supported())


def test_every_leg_runs_in_an_environment_pyproject_declares_with_one_python() -> None:
    for row in check.legs(check.GROUPS):
        assert row["environment"] in pixi()["environments"], row["leg"]
        assert check.python_of(row["environment"]), row["leg"]


def test_the_unit_row_runs_in_every_supported_environment_the_floor_first() -> None:
    supported = check.supported()
    assert supported[0] == check.DEFAULT_ENVIRONMENT
    assert check.BY_NAME["unit"].environments == supported
    assert "floors" not in supported


def test_the_classifiers_name_the_pythons_the_supported_environments_pin() -> None:
    classifiers = manifest()["project"]["classifiers"]
    declared = tuple(c.rsplit(" :: ", 1)[1] for c in classifiers if re.fullmatch(r".* :: Python :: 3\.\d+", c))
    assert declared == tuple(check.python_of(name) for name in check.supported())


def test_the_conda_package_admits_the_pythons_ci_tests_and_no_newer() -> None:
    """`pixi global install` takes the newest Python the channels hold, so the bound stops at the newest CI tests."""
    pythons = [Version(check.python_of(name)) for name in check.supported()]
    newest = max(pythons)
    bound = SpecifierSet(pixi()["package"]["run-dependencies"]["python"])
    assert bound == SpecifierSet(f">={min(pythons)},<{newest.major}.{newest.minor + 1}")


def test_check_imports_only_the_standard_library_so_the_plan_job_needs_no_pixi() -> None:
    tree = ast.parse((REPO / "scripts" / "check.py").read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }
    assert imported - {"__future__"} <= sys.stdlib_module_names


def test_the_plan_job_reads_the_table_with_the_runners_python() -> None:
    plan = workflow()["jobs"]["plan"]
    assert not [step for step in plan["steps"] if "setup-pixi" in step.get("uses", "")]
    table = next(step for step in plan["steps"] if step.get("id") == "table")["run"]
    assert "python3 scripts/check.py --json" in table


def test_the_node_pixi_installs_starts_at_the_floor_package_json_sets() -> None:
    floor = json.loads((REPO / "package.json").read_text(encoding="utf-8"))["engines"]["node"]
    assert pixi()["dependencies"]["nodejs"].startswith(f"{floor},")


def test_every_moment_the_workflow_computes_is_one_the_table_gates_at() -> None:
    plan = workflow()["jobs"]["plan"]
    moment = next(step for step in plan["steps"] if step.get("id") == "table")["env"]["MOMENT"]
    assert set(MOMENT_CHOSEN.findall(moment)) == set(check.MOMENTS)
    assert {when for group in check.GROUPS for when in group.when} <= set(check.MOMENTS)


def test_a_manual_run_can_plan_the_weekly_rows_apart_from_the_tips_run() -> None:
    """A dispatch may choose the weekly plan, which runs in a concurrency group of its own.

    YAML 1.1 reads the key `on` as true, which is the key PyYAML hands back for it.
    """
    document: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    moment = document[True]["workflow_dispatch"]["inputs"]["moment"]
    assert moment["options"] == [when for when in check.MOMENTS if when != "pr"]
    assert moment["default"] == "main"
    plan = next(step for step in document["jobs"]["plan"]["steps"] if step.get("id") == "table")["env"]["MOMENT"]
    assert "inputs.moment" in plan
    group = document["concurrency"]["group"]
    assert "github.event_name == 'schedule'" in group and "inputs.moment == 'schedule'" in group, group


def test_every_runner_a_workflow_names_is_the_one_the_table_names() -> None:
    """A job outside the matrix cannot read the table before it starts, so it names the Linux image the table names."""
    named = {
        (path.name, name): job["runs-on"]
        for path in sorted((REPO / ".github" / "workflows").glob("*.yml"))
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items()
        if not job["runs-on"].startswith("${{")
    }
    assert named and set(named.values()) == {check.LINUX}, named
    pattern = next(step["with"]["pattern"] for step in steps() if "download-artifact" in step.get("uses", ""))
    assert pattern == "coverage-*-${{ matrix['runs-on'] }}-*"
    assert {row["runs-on"] for row in check.legs((check.BY_NAME["coverage"],))} == {check.LINUX}


def caches(key: str) -> list[dict[str, Any]]:
    """The cache steps of the workflow that use the key the matrix carries in `key`, in order."""
    return [
        step
        for step in steps()
        if "actions/cache/" in step.get("uses", "") and step["with"]["key"] == f"${{{{ matrix.{key} }}}}"
    ]


@pytest.mark.parametrize("key", ["cache", "npm"])
def test_every_cache_is_saved_to_the_key_it_is_restored_from(key: str) -> None:
    """The tools cache and npm's download cache each have one restore and one save, under one key the table writes."""
    found = caches(key)
    assert [step["uses"].split("@")[0] for step in found] == ["actions/cache/restore", "actions/cache/save"]
    restore, save = found
    assert restore["with"] == save["with"]
    every = [step for step in steps() if "actions/cache/" in step.get("uses", "")]
    assert len(every) == 2 * len(["cache", "npm"])


def test_npm_downloads_are_kept_on_every_leg_that_runs_npm_and_no_other() -> None:
    for row in check.legs(check.GROUPS):
        assert bool(row["npm"]) == ("npm" in row["tools"]), row["leg"]


def test_the_tools_cache_is_the_one_directory_doctor_names() -> None:
    """Chromium and ffmpeg both live in the directory `doctor --json` names, so a leg on any platform keeps one."""
    restore, _ = caches("cache")
    assert restore["with"]["path"] == "${{ env.TOOLS_CACHE }}"
    named = [step for step in steps() if "TOOLS_CACHE=" in step.get("run", "")]
    assert len(named) == 1, "no step names the directory the tools are kept in"
    assert "decktalk doctor" in named[0]["run"] and '["cache"]' in named[0]["run"]
    assert named[0]["if"] == restore["if"]


def test_the_scaffold_build_judges_a_release_after_it_is_cut() -> None:
    assert check.BY_NAME["scaffold"].when == ("schedule",)


# ---- one row per contract ------------------------------------------------------------------------


HOOKS = REPO / ".pre-commit-config.yaml"


def hook_entries() -> dict[str, str]:
    """Every local hook of the hooks config, by id, with the command it runs."""
    config = yaml.safe_load(HOOKS.read_text(encoding="utf-8"))
    return {hook["id"]: hook["entry"] for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]}


def test_the_lint_row_runs_the_hooks_and_nothing_else() -> None:
    """A rule the row ran beside the hooks would pass a commit and fail CI."""
    assert check.BY_NAME["lint"].commands == (check.Run(("prek", "run", "--all-files", "--show-diff-on-failure")),)


@pytest.mark.parametrize(
    "rule",
    ["ruff check", "ruff format", "biome", "ty check", "pixi lock --check", "zizmor", "tests/house_rules.py"],
)
def test_every_lint_rule_is_a_hook(rule: str) -> None:
    assert [hook for hook, entry in hook_entries().items() if rule in entry], f"no hook runs {rule}"


def test_the_house_rules_run_in_a_hook_and_in_no_row() -> None:
    rows = [group.name for group in check.GROUPS for suite in suites(group) if suite.marker == tools.LINT]
    assert rows == []


def test_every_hook_starts_a_program_every_platform_can_run() -> None:
    """npm writes `node_modules/.bin/<tool>` as a sh script on Windows, which a Windows commit cannot run."""
    assert not [hook for hook, entry in hook_entries().items() if "node_modules/.bin/" in entry]


@pytest.mark.parametrize("marker", tools.BUILT)
def test_a_suite_that_reads_a_build_runs_in_one_row_right_after_the_build(marker: str) -> None:
    rows = [group for group in check.GROUPS if any(suite.marker == marker for suite in suites(group))]
    assert [group.name for group in rows] == ["wheel"]
    [wheel] = rows
    assert wheel.commands[0] == check.Run((*check.PYTHON, "-m", "build", "--no-isolation"))
    assert wheel.commands[1] == check.Suite(marker=marker)


def test_only_the_unit_floors_and_scaffold_rows_run_in_parallel() -> None:
    """Scaffold is the one tool row whose tests are whole builds, each its own work, so it spreads them too."""
    parallel = [group.name for group in check.GROUPS for suite in suites(group) if suite.parallel]
    assert parallel == ["unit", "floors", "scaffold"]


def test_the_floors_row_runs_the_suite_in_the_environment_solved_at_the_lowest_versions() -> None:
    floors = check.BY_NAME["floors"]
    assert floors.environments == ("floors",)
    assert floors.commands == (check.Suite(parallel=True),)
    assert pixi()["environments"]["floors"]["solve-strategy"] == "lowest-direct"
    assert "pr" in floors.when


def locked_pypi(environment: str, platform: str) -> dict[str, str]:
    """The PyPI packages pixi.lock holds for one environment on one platform, by name, with their versions."""
    lock = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    urls = {entry["pypi"] for entry in lock["environments"][environment]["packages"][platform] if "pypi" in entry}
    return {
        entry["name"]: entry["version"]
        for entry in lock["packages"]
        if entry.get("pypi") in urls and "version" in entry
    }


def floors() -> dict[str, Version]:
    """Every direct dependency with a floor, runtime and dev group alike, by name, with its floor.

    A requirement with no `>=` names no floor, such as `hatchling`, whose floor has its one home in
    `[build-system]`.
    """
    lines = [*manifest()["project"]["dependencies"], *manifest()["dependency-groups"]["dev"]]
    found = {}
    for line in lines:
        requirement = Requirement(line)
        named = [Version(spec.version) for spec in requirement.specifier if spec.operator == ">="]
        if named:
            [found[requirement.name]] = named
    return found


@pytest.mark.parametrize("platform", ["linux-64", "osx-arm64", "osx-64", "win-64"])
def test_the_floors_environment_holds_every_direct_dependency_at_its_floor(platform: str) -> None:
    """A lock keeps a version it holds when a floor moves down, so the floors row would judge the old floor.

    The locked version is the floor itself: `>=9.1` holds 9.1.0 and refuses 9.1.1 and 9.2.0, so a
    floor written with fewer parts, `>=9`, is refused too, and so is a floor that names a release PyPI
    never had or yanked: every floor names a release a solver installs. `pixi update -e floors` solves
    the environment again at the floors pyproject.toml names.
    """
    locked = locked_pypi("floors", platform)
    expected = floors()
    moved = [
        f"{name} {locked[name]} for the floor {floor}"
        for name, floor in expected.items()
        if Version(locked[name]) != floor
    ]
    assert moved == []


# ---- the installer row ---------------------------------------------------------------------------


def scenarios() -> list[check.Scenario]:
    return [command for command in check.BY_NAME["installer"].commands if isinstance(command, check.Scenario)]


def test_a_row_needs_docker_exactly_when_it_runs_a_scenario() -> None:
    for group in check.GROUPS:
        runs = any(isinstance(command, check.Scenario) for command in group.commands)
        assert ("docker" in group.tools) == runs, group.name


def test_every_scenario_pulls_its_image_by_digest_and_shows_its_tag() -> None:
    for scenario in scenarios():
        assert re.fullmatch(r"[\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}", scenario.image.reference), scenario
        assert scenario.image.reference in scenario.argv
        assert scenario.shown == f"{scenario.program} on {scenario.image.name}:{scenario.image.tag}"


def test_every_scenario_names_a_program_that_exists_and_mounts_install_sh_and_the_programs() -> None:
    for scenario in scenarios():
        assert (check.SHELL / f"{scenario.program}.sh").is_file(), scenario.program
        assert f"{check.ROOT / 'install.sh'}:/install.sh:ro" in scenario.argv
        assert f"{check.SHELL}:/shell:ro" in scenario.argv


def test_the_scenario_that_finds_pixi_already_there_runs_pixis_own_image() -> None:
    """scripts/build_pixi_pins.py writes the image at the Pixi `requires-pixi` names, and this row runs it."""
    assert check.PIXI_IMAGE in {scenario.image for scenario in scenarios()}


# ---- a suite the run named fails when its tool is missing -----------------------------------------
#
# The rows above fetch every tool before a suite starts, and `tests/support/tools.py` is what makes a
# suite that still finds no tool fail rather than skip, so the two halves of the promise sit together.


def doctor_reports(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]) -> None:
    """Make `decktalk doctor --json` answer with these tool rows and nothing else."""

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=(), returncode=0, stdout=json.dumps({"tools": rows}), stderr="")

    monkeypatch.setattr(tools.subprocess, "run", run)


@pytest.mark.parametrize(
    ("rows", "wanted", "missing"),
    [
        pytest.param(
            [{"tool": "chromium", "version": None}, {"tool": "ffmpeg", "version": "8.1.2"}],
            ("chromium", "ffmpeg"),
            ["chromium"],
            id="reported with no version",
        ),
        pytest.param([], ("ffmpeg",), ["ffmpeg"], id="never named"),
        pytest.param(
            [{"tool": "chromium", "version": "153"}, {"tool": "ffmpeg", "version": "8.1.2"}],
            ("chromium", "ffmpeg"),
            [],
            id="every tool held",
        ),
    ],
)
def test_a_tool_is_missing_exactly_when_doctor_cannot_vouch_for_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    rows: list[dict[str, object]],
    wanted: tuple[str, ...],
    missing: list[str],
) -> None:
    doctor_reports(monkeypatch, rows)
    assert tools.missing(wanted, tmp_path) == missing


def test_a_missing_tool_fails_the_run_and_names_the_command_that_fetches_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    doctor_reports(monkeypatch, [{"tool": "chromium", "version": "153"}])
    with pytest.raises(pytest.fail.Exception, match="ffmpeg") as failed:
        tools.require(("chromium", "ffmpeg"), tmp_path)
    assert tools.FETCH in str(failed.value)


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
