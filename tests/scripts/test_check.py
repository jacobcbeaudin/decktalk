"""The check table in `scripts/check.py`, held to what its rows promise a run does.

The release pull request regenerates with `--group generated --write`, and the rehearsal runs the
same command on every pull request. A write that skipped a generator the check runs would leave a
file stale on the one branch nobody else pushes to, so the derivation is held here row by row. A row
that declares a tool has to fetch it before its checks run, and a suite that still finds no tool has
to fail, because a declared need with nothing behind it lets a suite pass with every test skipped.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import replace
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
    assert check.NPM_CI in group.preparations


def test_a_generator_that_needs_more_to_check_needs_the_same_to_write() -> None:
    command = ("python", "-X", "utf8", "scripts/build_example.py", "--check")
    assert check.writing(command) == (*command[:-1], "--write")


@pytest.mark.parametrize(
    "command",
    [
        check.BY_NAME["generated"].commands[-1],
        (*check.PYTHON, "scripts/check_wheel.py"),
        ("ruff", "check", "src"),
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


@pytest.mark.parametrize("script", sorted((REPO / "scripts").glob("build_*.py")), ids=lambda path: path.name)
def test_every_generator_is_checked_in_a_row_that_prepares_what_it_runs(script: Path) -> None:
    """A generator no row runs is never held, and the samples' generator runs DeckTalk, which needs ffmpeg."""
    rows = [group for group in check.GROUPS for command in group.commands if f"scripts/{script.name}" in command]
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
    launches = any("playwright" in (check.ROOT / "scripts" / f"{g}.py").read_text() for g in check.GENERATORS)
    assert ("chromium" in group.tools) == launches


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
    """`pixi run setup` readies a new clone for every row, so no task spells `npm ci` or `decktalk install` itself."""
    ran: list[tuple[str, ...]] = []
    monkeypatch.setattr(check, "run", lambda command, _env=(): ran.append(command) or True)
    assert check.run_setup() == 0
    every = {command for group in check.GROUPS for command in group.preparations}
    assert ran == list(check.setup_commands())
    assert set(ran) == every and len(ran) == len(every)
    assert ran == [check.NPM_CI, check.INSTALL]


def test_a_group_opens_with_the_command_that_runs_it_again_in_its_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = check.BY_NAME["unit"]
    monkeypatch.delenv("PIXI_ENVIRONMENT_NAME", raising=False)
    assert check.reproduce(group) == "pixi run check --group unit"
    monkeypatch.setenv("PIXI_ENVIRONMENT_NAME", check.environment("3.13"))
    assert check.reproduce(group, " --write") == "pixi run -e py313 check --group unit --write"


def test_a_preparation_fetches_only_what_the_tools_cache_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row's own run installs the Node packages, so a preparation that did too installed them twice."""
    ran: list[tuple[str, ...]] = []
    monkeypatch.setattr(check, "run", lambda command, _env: ran.append(command) or True)
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
    monkeypatch.setattr(check, "run", lambda _command, _env: True)
    env = (("COVERAGE_FILE", f"{check.ROOT}/.coverage"), ("GLOB", "tests/runtime/src/*.ts"))
    assert check.run_group(replace(check.BY_NAME["lint"], env=env))
    shown = capsys.readouterr().out
    assert "   COVERAGE_FILE=.coverage\n" in shown
    assert "   GLOB=tests/runtime/src/*.ts\n" in shown


@pytest.mark.parametrize("marker", tools.INSTALLED)
def test_every_suite_that_needs_a_tool_runs_after_the_install(marker: str) -> None:
    rows = [group for group in check.GROUPS if any(marker in command for command in group.commands)]
    assert rows, f"no row runs the {marker} suite"
    for group in rows:
        assert check.INSTALL in group.preparations, f"{group.name} runs -m {marker} with nothing that fetches its tools"


def test_every_suite_prints_the_reason_of_every_skip() -> None:
    suites = [command for group in check.GROUPS for command in group.steps if "pytest" in command]
    assert suites
    assert all("-rs" in command for command in suites)


@pytest.mark.parametrize(
    ("tools", "when", "match"),
    [
        pytest.param(("docker",), (), "no row can provide", id="a need nothing provides"),
        pytest.param((), ("release",), "no run of ci.yml is", id="a moment no run is"),
    ],
)
def test_a_row_that_names_what_nothing_answers_is_refused(
    tools: tuple[str, ...], when: tuple[str, ...], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        check.Group(name="x", why="x.", commands=(), runners=(), pythons=(), tools=tools, timeout=1, when=when)


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


# ---- every leg runs in the Pixi environment pyproject.toml declares for its Python ---------------

RELEASE = REPO / ".github" / "workflows" / "release.yml"


def setups() -> list[tuple[str, dict[str, Any]]]:
    """Every setup-pixi step in ci.yml and release.yml, with the job it belongs to."""
    found = []
    for path in (WORKFLOW, RELEASE):
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items():
            found += [(name, step) for step in job.get("steps", []) if "setup-pixi" in step.get("uses", "")]
    return found


def test_no_workflow_installs_a_python_or_node_of_its_own() -> None:
    for path in (WORKFLOW, RELEASE):
        text = path.read_text(encoding="utf-8")
        assert "setup-uv" not in text and "setup-node" not in text and "setup-python" not in text, path.name


def installing() -> list[tuple[str, dict[str, Any]]]:
    """Every setup-pixi step that installs a workspace environment, which is every one that runs Python."""
    return [(name, step) for name, step in setups() if step["with"].get("run-install", True) is not False]


def test_every_job_that_runs_python_installs_a_named_environment_from_the_lock() -> None:
    """A step with no `environments` caches under another key, and one without `locked` may solve."""
    jobs = {name for name, _ in installing()}
    assert jobs == {"plan", "run", "coverage", "regenerate", "build"}, jobs
    for name, step in installing():
        assert step["with"]["environments"], name
        assert step["with"]["locked"] is True, name
        assert step["with"]["activate-environment"] is True, name
    named = {step["with"]["environments"] for _, step in installing()}
    assert named == {"plan", check.DEFAULT_ENVIRONMENT, "${{ matrix.pixi }}"}


def test_only_the_conda_jobs_run_pixi_without_an_environment() -> None:
    """`pixi build` solves its own build environment and `pixi upload` needs none, so those two jobs
    install nothing, and no other job may skip the install and run on whatever the runner has."""
    bare = {name for name, step in setups() if step["with"].get("run-install", True) is False}
    assert bare == {"conda", "publish-conda"}, bare


def test_every_pixi_ci_runs_is_the_floor_pyproject_requires() -> None:
    """setup-pixi reads no version from the manifest, so `requires-pixi` is held here to every step's pin."""
    floor = pixi()["workspace"]["requires-pixi"]
    assert floor.startswith(">=")
    assert {step["with"]["pixi-version"] for _, step in setups()} == {f"v{floor.removeprefix('>=')}"}


def test_the_release_builds_and_judges_the_wheel_as_the_wheel_row_does() -> None:
    build, _, judge = check.BY_NAME["wheel"].commands
    job = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]["build"]
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert " ".join(build) in runs
    assert any(run.startswith(" ".join(judge)) for run in runs), runs


def test_the_conda_package_ships_only_for_a_final_release_and_only_after_pypi() -> None:
    """A candidate stays on PyPI, and the channel never holds a version PyPI refused."""
    jobs = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]
    assert jobs["conda"]["if"] == "needs.build.outputs.prerelease == 'false'"
    assert "publish" in jobs["publish-conda"]["needs"]
    assert "publish-conda" in jobs["attach-conda"]["needs"]


def test_the_conda_upload_uses_trusted_publishing_with_an_attestation() -> None:
    """A stored prefix.dev key would outlive the release, and an attestation needs the OIDC identity."""
    job = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]["publish-conda"]
    assert job["permissions"] == {"id-token": "write"}
    text = yaml.safe_dump(job)
    assert "secrets." not in text and "PREFIX_API_KEY" not in text, text
    uploads = [step["run"] for step in job["steps"] if "pixi upload prefix" in step.get("run", "")]
    assert len(uploads) == 1 and "--generate-attestation" in uploads[0], uploads
    # The channel is namespaced, so `--channel` is the path after the host of the URL install.sh reads.
    [url] = re.findall(r'^CHANNEL="(.+)"$', (REPO / "install.sh").read_text(encoding="utf-8"), re.M)
    assert f"--channel {url.removeprefix('https://prefix.dev/')} " in uploads[0], (url, uploads)


def test_pixis_own_image_is_the_pixi_the_manifest_requires() -> None:
    """The scenario that finds Pixi already there runs the Pixi every CI leg and install.sh run."""
    floor = pixi()["workspace"]["requires-pixi"].removeprefix(">=")
    assert check.PIXI_IMAGE.split("@")[0] == f"ghcr.io/prefix-dev/pixi:{floor}"
    assert any(check.PIXI_IMAGE in command for command in check.BY_NAME["installer"].commands)


MANIFEST = REPO / "pyproject.toml"

INTERPRETER = check.PYTHON[0]
"""The conda package each Python feature of the manifest pins, which is named as the interpreter every row runs."""


def pixi() -> dict[str, Any]:
    """The `[tool.pixi]` tables of pyproject.toml, which are the one home of every environment."""
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["tool"]["pixi"]


def pinned_pythons(environment: str) -> list[str]:
    """The Python lines the features of one Pixi environment pin, which a leg needs exactly one of."""
    spec = pixi()["environments"][environment]
    features = spec if isinstance(spec, list) else spec["features"]
    return [
        pin
        for feature in features
        if (pin := pixi()["feature"].get(feature, {}).get("dependencies", {}).get(INTERPRETER)) is not None
    ]


def test_every_python_a_leg_runs_is_the_one_its_pixi_environment_pins() -> None:
    for row in check.legs(check.GROUPS):
        assert pinned_pythons(row["pixi"]) == [f"{row['python']}.*"], row["leg"]


def test_every_python_the_package_supports_has_an_environment_and_no_other_does() -> None:
    """The classifiers, the table and the environments name the same Pythons, the floor being the default."""
    classifiers = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["project"]["classifiers"]
    declared = tuple(c.rsplit(" :: ", 1)[1] for c in classifiers if re.fullmatch(r".* :: Python :: 3\.\d+", c))
    assert declared == check.EVERY_PYTHON
    named = {check.environment(python) for python in check.EVERY_PYTHON}
    developing = {name for name in pixi()["environments"] if name != "plan"}
    assert named == developing
    assert check.environment(check.FLOOR) == check.DEFAULT_ENVIRONMENT


def test_the_plan_environment_is_the_floor_python_and_nothing_else() -> None:
    plan = pixi()["environments"]["plan"]
    assert plan["no-default-feature"] is True
    assert pinned_pythons("plan") == [f"{check.FLOOR}.*"]
    assert all(set(pixi()["feature"][feature]) == {"dependencies"} for feature in plan["features"])
    assert all(set(pixi()["feature"][feature]["dependencies"]) == {INTERPRETER} for feature in plan["features"])


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


def test_every_runner_the_workflow_names_is_one_the_table_names() -> None:
    labels = {job["runs-on"] for job in workflow()["jobs"].values() if not job["runs-on"].startswith("${{")}
    assert labels == {check.LINUX}
    pattern = next(step["with"]["pattern"] for step in steps() if "download-artifact" in step.get("uses", ""))
    assert f"-{check.LINUX}-" in pattern


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


# ---- one row per contract ------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(check.ELSEWHERE))
def test_a_file_the_unit_suite_leaves_elsewhere_runs_in_its_row_alone(path: str) -> None:
    assert (REPO / path).is_file(), f"{path} is left to another row and is not there any more"
    runs = [group.name for group in check.GROUPS for command in group.commands if path in command]
    assert runs == [check.ELSEWHERE[path]], runs
    (unit,) = check.BY_NAME["unit"].commands
    assert f"--ignore={path}" in unit


@pytest.mark.parametrize("marker", tools.BUILT)
def test_a_suite_that_reads_a_build_runs_in_one_row_right_after_the_build(marker: str) -> None:
    rows = [
        (group.name, group.commands) for group in check.GROUPS if marker in map(check.selected_marker, group.commands)
    ]
    assert [name for name, _ in rows] == ["wheel"], rows
    ((_, commands),) = rows
    assert commands[0] == (*check.PYTHON, "-m", "build", "--no-isolation")
    assert check.selected_marker(commands[1]) == marker


def test_only_the_unit_and_scaffold_rows_run_in_parallel() -> None:
    """Scaffold is the one tool row whose tests are whole builds, each its own work, so it spreads them too."""
    parallel = [group.name for group in check.GROUPS for command in group.commands if "-n" in command]
    assert parallel == ["unit", "scaffold"]


def test_the_scaffold_build_judges_a_release_after_it_is_cut() -> None:
    assert check.BY_NAME["scaffold"].when == ("schedule",)


def test_a_pull_request_runs_the_command_line_at_every_floor_pyproject_declares() -> None:
    """The lockfile holds every dependency at its newest, so only a row that installs at the floors judges them."""
    floors = check.BY_NAME["floors"]
    ((*_, script, _, floor),) = floors.commands
    assert '--constraint "$scratch/floors.txt"' in script
    assert "sys.version_info" in script and '"$1"' in script
    assert floor == check.FLOOR
    assert "--version" in script
    assert "pr" in floors.when


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
