"""Every check DeckTalk runs, in one table, spelled once.

    pixi run check                           # every group a pull request runs, in order
    pixi run check --fast                    # lint and unit alone, the two a change most often fails
    pixi run check --group browser           # one group, by name, repeatable and comma-separated
    pixi run check --list                    # the table, for a person
    pixi run check --group generated --write # every generator in the group, writing
    pixi run check --group e2e --prepare     # only what fetches the group's cached tools
    pixi run check --setup                   # every row's preparation, once, for a new clone
    pixi run -e py313 check --group unit     # one group on another Python
    pixi run check --json --when pr          # the matrix, for a workflow
    python3 scripts/check.py --pixi-version  # the Pixi a workflow installs before any environment

`pixi run check` is `python scripts/check.py` in a Pixi environment, and every command a row runs
runs in that same environment, so pixi.lock decides every tool and every package a check sees. The
script imports nothing outside the standard library, so a workflow reads the table with the
runner's own Python before Pixi is there.

`GROUPS` below is the only place any check is written down. A workflow reads this table at runtime and
names no command of its own, so a workflow cannot disagree with it. There is no switch that skips a
check and no way to mark one advisory, because a switch that exists becomes permanent. A run that
selects fewer groups than the full set prints the rows it did not run and why, so a short run is never
mistaken for a complete one.

`--write` turns a group's checks into the commands that fix them. Every `build_*.py --check` in the
group runs as `--write` instead, the commands that prepare the machine run as they are, and a
command that only judges is left out. The write commands are read from the same rows, so the one
command that regenerates what a release made stale cannot forget a generator the check remembers.

`--prepare` runs only the commands at the head of a row that fetch what the tools cache keeps. CI
runs it before it saves that cache, so a cache is only ever saved from a fetch that finished.

`--setup` runs every row's preparation once and installs every Pixi environment a local run of a
row enters, which is what `pixi run setup` does before it installs the git hooks, so a new clone is
ready for every row and no task spells a command this table owns.

The first run may download headless Chromium and ffmpeg through `decktalk install`, once per machine.
No check needs an ElevenLabs key. After `pixi run setup`, a row needs the network only when it
names the `network` need, and the table prints that need beside the row.
"""

from __future__ import annotations

import argparse
import ast
import functools
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "pyproject.toml"

PYTHON = ("python",)
"""Every Python command runs on the interpreter of the Pixi environment the run is in, so pixi.lock decides
what it imports."""

PYTEST = (*PYTHON, "-m", "pytest", "-q", "-rs")
"""How every suite is run. `-rs` prints the reason of every skip, so a skipped test is never silent in a log."""

PARALLEL = ("-n", "auto")
"""What a suite adds to run on every core, which pytest-xdist reads and the unit and scaffold rows pass.

The suites that drive a real tool on every pull request stay serial, because a build already records
its sections across the machine's cores, so two builds at once would only wait on each other. The
scaffold row is the exception: each of its tests builds and records a whole project, and run one
after another they outgrow the row's limit, so it spreads them across workers.
"""

MEASURE = ("--cov", "--cov-report=")
"""What a suite adds to measure itself, which is the data file and no report of its own."""

REPORTS = ROOT / "tests" / "out" / "junit"
"""Where every measuring suite writes the JUnit report of what it ran, one file named after the group.

Coverage data says which lines a suite reached, and a suite whose every test skipped still reaches
the lines its imports run, so the data alone would call such a suite reporting. The report says
how many tests ran and how many skipped, which is what `check_coverage.py` needs to call it silent.
"""


@dataclass(frozen=True)
class Run:
    """One command a row runs as it is written, such as a script or a tool."""

    argv: tuple[str, ...]

    @property
    def shown(self) -> str:
        """The command as a person reads it in a log or in the table."""
        return shell(self.argv)


@dataclass(frozen=True)
class Suite:
    """One run of pytest: the suite a marker selects, or the suite that needs no tool when `marker` is None.

    `measured` names the group whose coverage the suite writes and whose report of what it ran it
    leaves for `check_coverage.py`, or is None for a suite the coverage floor does not read.
    """

    marker: str | None = None
    parallel: bool = False
    measured: str | None = None

    @property
    def argv(self) -> tuple[str, ...]:
        selection = ("-m", self.marker) if self.marker else ()
        spread = PARALLEL if self.parallel else ()
        measure = (*MEASURE, f"--junitxml={REPORTS / f'{self.measured}.xml'}") if self.measured else ()
        return (*PYTEST, *spread, *selection, *measure)

    @property
    def shown(self) -> str:
        return shell(self.argv)


def measured(name: str) -> tuple[tuple[str, str], ...]:
    """The data file one group writes, named after the group so that no two groups overwrite each other.

    `coverage combine` reads every `.coverage.*` beside it, so naming each group's file is what lets
    a whole run on one machine be combined at the end. One shared `.coverage` would hold only the
    suite that finished last.

    The path is absolute because a suite that drives the command line starts its subprocesses in the
    project they are building, and a relative name would leave each subprocess writing its measure
    into a temporary directory nothing ever reads.
    """
    return (("COVERAGE_FILE", str(ROOT / f".coverage.{name}")),)


LINUX, MACOS, WINDOWS = "ubuntu-26.04", "macos-26", "windows-2025"
"""The runner images every leg runs on, each the newest image GitHub hosts for its system.

Each is named by its image rather than by a `-latest` label. A label that moves to a new image would
change the machine under every row with no change to this repository, and the tools cache key, which
names the runner, would then restore what another image fetched. So a new image is a change to this
line. Every leg reads its runner from the matrix this table writes. A job that runs outside the
matrix, such as the plan job that reads the table, names the Linux image itself, and a test holds
each of those to `LINUX`.
"""

EVERY_PLATFORM = (LINUX, MACOS, WINDOWS)


@functools.cache
def manifest() -> dict[str, Any]:
    """pyproject.toml as data, which holds every Pixi environment and the Python each one pins."""
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def environments() -> dict[str, list[str]]:
    """Every Pixi environment pyproject.toml declares, with the features it is made of."""
    declared = manifest()["tool"]["pixi"]["environments"]
    return {name: spec if isinstance(spec, list) else spec["features"] for name, spec in declared.items()}


def python_of(environment: str) -> str:
    """The Python line one environment pins, such as `3.13`, read from the one feature of it that pins a Python."""
    features = manifest()["tool"]["pixi"]["feature"]
    pins = [
        pin.removesuffix(".*")
        for feature in environments()[environment]
        if (pin := features.get(feature, {}).get("dependencies", {}).get("python")) is not None
    ]
    if len(pins) != 1:
        raise SystemExit(f"the Pixi environment {environment} pins {len(pins)} Pythons, and a leg runs on one.")
    return pins[0]


DEFAULT_ENVIRONMENT = "default"
"""The Pixi environment a bare `pixi install` builds and a bare `pixi run` enters, which holds the floor."""

DEVELOPING = "dev"
"""The feature that carries the dependency group, which every environment that runs the suite holds."""

SOLVED = "solve-strategy"
"""The key of an environment that pyproject.toml solves another way than at the newest versions."""


def supported() -> tuple[str, ...]:
    """Every environment that holds one Python line DeckTalk supports, the default first.

    Such an environment carries the dependency group and is solved at the newest versions. The
    `floors` environment carries the group too, and is solved at the lowest, so it is not a Python
    line of its own.
    """
    declared = manifest()["tool"]["pixi"]["environments"]
    return tuple(
        name
        for name, features in environments().items()
        if DEVELOPING in features and not (isinstance(declared[name], dict) and SOLVED in declared[name])
    )


PLATFORM_HEADROOM = 15
"""Calibration: the minutes a macOS or Windows row gets beyond its Linux row, as Windows e2e took 28 of 30."""

RUNTIME_TESTS = "tests/decktalk/runtime/src/*.test.ts"
"""Every test of the runtime, named as a pattern so that the runner runs the test files and no other module.

`node --test` expands the pattern itself, so the row is the same on every platform and needs no shell
to expand it.
"""

SCRIPT_TESTS = "tests/scripts/*.test.mjs"
"""Every test of a Node script under `scripts/`, named the same way and run beside the runtime's."""


SHELL = ROOT / "scripts" / "shell"
"""The shell programs the installer row runs, kept as files so shellcheck reads them as it reads `install.sh`."""


@dataclass(frozen=True)
class Image:
    """One container image, by its tag and by the digest the tag named when it was chosen.

    The digest is what is pulled, so a tag that moves to other bytes is a change to this repository
    rather than a change under it. The tag is what a person reads.
    """

    name: str
    tag: str
    digest: str

    @property
    def reference(self) -> str:
        return f"{self.name}:{self.tag}@{self.digest}"

    @property
    def shown(self) -> str:
        return f"{self.name}:{self.tag}"


DEBIAN = Image("debian", "13-slim", "sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2")
"""Debian stable with nothing but apt, which is the bare machine most readers of the one-liner have."""

UBUNTU = Image("ubuntu", "26.04", "sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c")
"""The newest Ubuntu LTS, which is what a new cloud machine or CI runner starts from."""

FEDORA = Image("fedora", "45", "sha256:a7d72ba2745b100a3351087472e5ee7b2450af7cf331e7673007e3817612839c")
"""The newest Fedora, the one glibc distribution in the row that installs with dnf rather than apt."""

ALPINE = Image("alpine", "3.24", "sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf")
"""The newest Alpine, a musl system, which the installer must refuse before it downloads anything."""

# pixi-image:start
PIXI_IMAGE = Image(
    "ghcr.io/prefix-dev/pixi", "0.84.1", "sha256:2d8821a1f3ee11a7fe8055f2c72d2b116d22f00c4caa73809857b5f216fcdebc"
)
# pixi-image:end
"""Pixi's own image, at the Pixi `requires-pixi` names, written by scripts/build_pixi_pins.py."""


@dataclass(frozen=True)
class Scenario:
    """One shell program under `scripts/shell/`, run by POSIX sh inside `image`.

    The container holds `install.sh` at `/install.sh` and the shell programs at `/shell`, both read
    only, and nothing else of this checkout. A program that needs the conda channel sources
    `/shell/scenario.sh` itself, which reads the channel from `/install.sh`, so the channel is written
    once and a scenario names its own dependencies.
    """

    image: Image
    program: str

    @property
    def argv(self) -> tuple[str, ...]:
        mounts = ("-v", f"{ROOT / 'install.sh'}:/install.sh:ro", "-v", f"{SHELL}:/shell:ro")
        return ("docker", "run", "--rm", *mounts, self.image.reference, "sh", "-eu", f"/shell/{self.program}.sh")

    @property
    def shown(self) -> str:
        return f"{self.program} on {self.image.shown}"


NPM_CI = Run(("npm", "ci"))
"""The pinned Node toolchain, which the runtime generator compiles with and the linters run from."""

INSTALL = Run(("decktalk", "install"))
"""Chromium and ffmpeg, fetched by the command a person runs, which is a no-op on a machine that has both."""


@dataclass(frozen=True)
class Need:
    """One thing a group needs on its runner, and what provides it.

    `prepare` is the command at the head of the row that provides it, or None when the workflow
    provides it before the row starts, which ci.yml does by reading the need's name from the matrix.
    A need that neither a command nor the workflow provides is a promise nothing keeps, and a row
    whose tools are missing passes in CI with every test skipped.
    """

    def __post_init__(self) -> None:
        if self.prepare is None and not self.workflow and not self.runner:
            raise ValueError(f"nothing provides the need described as: {self.why}")

    why: str
    prepare: Run | None = None
    cached: bool = False  # whether the workflow keeps what `prepare` downloaded, keyed by `tools_key`
    workflow: bool = False  # whether ci.yml acts on this need before the row starts
    runner: bool = False  # whether every runner and a contributor's machine has it before anything runs


NEEDS: dict[str, Need] = {
    "npm": Need(
        why="Node, which every Pixi environment provides, and the pinned packages, which the row installs.",
        prepare=NPM_CI,
    ),
    "chromium": Need(
        why="The headless Chromium the recorder drives.",
        prepare=INSTALL,
        cached=True,
    ),
    "ffmpeg": Need(
        why="The pinned ffmpeg and ffprobe every media measurement runs through.",
        prepare=INSTALL,
        cached=True,
    ),
    "history": Need(
        why="Every commit and tag since the last release, which the workflow's checkout fetches in full.",
        workflow=True,
    ),
    "docker": Need(
        why="A Docker engine, which every Linux runner has, for a row that runs its programs in a container.",
        runner=True,
    ),
    "network": Need(
        why="The network, for a row that reads a package index or a channel each time it runs.",
        runner=True,
    ),
}
"""Every need a row may declare. The Pixi environment is not one of them, because every row runs in one."""

MOMENTS = ("pr", "main", "schedule")
"""The moments a row can gate at, which are the three ci.yml computes from the event that started it.

A pull request is `pr`, a push to `main` and a manual run are `main`, and the weekly run is
`schedule`. A release runs no check of its own, because release.yml consumes the verdict of the run
on the tip of `main`, so a release gates on exactly what `main` gates on and has no column here.
"""

FFMPEG_PIN = ROOT / "src" / "decktalk" / "toolchain" / "ffmpeg_fetch.py"
FFMPEG_VERSION = "FFMPEG_VERSION"
"""The name `ffmpeg_fetch.py` gives its pin, read from the source because this script imports no package."""

PLAYWRIGHT = "playwright"


def playwright_minor() -> str:
    """The Playwright minor `[project.dependencies]` names, which decides the Chromium revision it fetches.

    pyproject.toml holds Playwright to one minor, such as `>=1.64,<1.65`, so the floor of the range
    is the minor. It is read from the manifest, its one home, as the text of the requirement,
    because this script imports nothing outside the standard library.
    """
    for line in manifest()["project"]["dependencies"]:
        name, operator, rest = line.partition(">=")
        if name.strip() == PLAYWRIGHT and operator:
            return rest.split(",", 1)[0].strip()
    raise SystemExit(f"pyproject.toml holds {PLAYWRIGHT} to no floor, so no cache key can name its minor.")


def ffmpeg_pin() -> str:
    """The ffmpeg release `decktalk install` fetches, read from the assignment in `ffmpeg_fetch.py`."""
    tree = ast.parse(FFMPEG_PIN.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == FFMPEG_VERSION for target in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise SystemExit(f"{FFMPEG_PIN.relative_to(ROOT)} no longer assigns {FFMPEG_VERSION}, so no cache key can name it.")


def tools_key(group: Group, runner: str) -> str:
    """The cache key of the tools one leg fetches, or an empty string when the leg fetches nothing to keep.

    The key names the leg, its runner image and the two pins that decide what `decktalk install`
    downloads, and nothing else. A key shared by every leg would let whichever leg saved first decide
    the cache, and so how many tests every later leg could run. A key on the whole lockfile would
    throw the download away on every unrelated dependency bump.
    """
    if not any(NEEDS[tool].cached for tool in group.tools):
        return ""
    return f"tools-{group.name}-{runner}-playwright-{playwright_minor()}-ffmpeg-{ffmpeg_pin()}"


NODE_LOCKFILE = ROOT / "package-lock.json"


def npm_key(group: Group, runner: str) -> str:
    """The cache key of the packages `npm ci` downloads on one leg, or an empty string when the leg runs no npm.

    Every leg on one runner installs the same package-lock.json, so the legs share one key, and the
    key changes only when the lockfile does. setup-pixi keeps the Pixi environment and nothing else,
    so npm's download cache needs a key of its own.
    """
    if "npm" not in group.tools:
        return ""
    return f"npm-{runner}-{hashlib.sha256(NODE_LOCKFILE.read_bytes()).hexdigest()}"


CHECK, WRITE, PREPARE, SETUP = "--check", "--write", "--prepare", "--setup"

GENERATES = "build_"
"""The prefix every generator's script carries, which is how the generated row finds a new one."""


@dataclass(frozen=True)
class Generator:
    """A generated file held to its source. Every generator takes `--check` and `--write` alike.

    The script runs on the environment's interpreter, where the package it generates from is
    installed from this checkout, so pixi.lock decides what a generator sees, the same way it decides
    what a test sees.
    """

    name: str
    mode: str = CHECK

    @property
    def argv(self) -> tuple[str, ...]:
        return (*PYTHON, f"scripts/{self.name}.py", self.mode)

    @property
    def shown(self) -> str:
        return shell(self.argv)


Command = Run | Suite | Generator | Scenario
"""Everything a row can run. Each renders the argv it runs and the line a person reads."""


FIRST_GENERATOR = "build_runtime"
"""The generator that runs before the others, because it writes `page.py`, which every generator that imports the
package reads."""

SAMPLES = "build_samples"
"""The generator that runs DeckTalk on a starter for the docs samples, which needs ffmpeg and Chromium and runs in the
e2e row."""

GENERATORS = (
    FIRST_GENERATOR,
    *sorted(
        path.stem for path in (ROOT / "scripts").glob(f"{GENERATES}*.py") if path.stem not in (FIRST_GENERATOR, SAMPLES)
    ),
)
"""Every generator the generated row runs, read from the directory so a new one gates from the commit that adds it.

A write runs each in a process of its own, in this order, so each one imports the `page.py` the
runtime's generator has just written rather than the one the run started with.
"""


def writing(command: Command) -> Generator | None:
    """The command that writes what `command` checks, or None when it only judges.

    A generator's write is the same generator with `--write`. A check that generates nothing, such as
    `check_docs_links.py` or `check_wheel.py`, has no write at all.
    """
    return replace(command, mode=WRITE) if isinstance(command, Generator) else None


def writer(group: Group) -> Group:
    """The same group with every generator writing and every check left out.

    The preparations stay where they are, because the row still declares the tools they fetch.
    """
    commands = tuple(written for command in group.commands if (written := writing(command)) is not None)
    if not commands:
        raise SystemExit(f"the group {group.name} generates nothing, so --write has nothing to write.")
    return replace(group, why=f"{group.why} This run writes what those checks judge.", commands=commands)


@dataclass(frozen=True)
class Group:
    """One check group: what it runs, where, what it needs and when it gates."""

    name: str
    why: str
    commands: tuple[Command, ...]
    runners: tuple[str, ...]
    tools: tuple[str, ...]  # names in NEEDS, each provided by a command at the head of the row or by the workflow
    timeout: int  # minutes, which is the CI job's timeout-minutes
    when: tuple[str, ...]
    environments: tuple[str, ...] = (DEFAULT_ENVIRONMENT,)  # the Pixi environments, each a leg on every runner
    env: tuple[tuple[str, str], ...] = ()  # what this group's commands need in the environment

    def __post_init__(self) -> None:
        unknown = [tool for tool in self.tools if tool not in NEEDS]
        if unknown:
            raise ValueError(f"the group {self.name} needs {', '.join(unknown)}, which no row can provide.")
        never = [moment for moment in self.when if moment not in MOMENTS]
        if never:
            raise ValueError(f"the group {self.name} gates at {', '.join(never)}, which no run of ci.yml is.")
        missing = [name for name in self.environments if name not in environments()]
        if missing:
            raise ValueError(
                f"the group {self.name} runs in {', '.join(missing)}, which pyproject.toml does not declare."
            )

    @property
    def preparations(self) -> tuple[Run, ...]:
        """The commands that provide what this row needs, once each, in the order the row names them."""
        commands: list[Run] = []
        for tool in self.tools:
            prepare = NEEDS[tool].prepare
            if prepare is not None and prepare not in commands:
                commands.append(prepare)
        return tuple(commands)

    @property
    def steps(self) -> tuple[Command, ...]:
        """Everything a run of this row executes, which is its preparations and then its checks."""
        return (*self.preparations, *self.commands)


def elsewhere(group: Group) -> Group:
    """The same group on macOS and Windows, gating every pull request beside the Linux leg.

    A group that drives a real tool is the only kind a second platform can fail on its own. Gating
    only the merge found those failures on main, after the change had landed, and the repository is
    public, so the legs cost nothing but the wait, which runs beside Linux rather than after it. The
    two runners are slower than Linux, so the row waits longer before it is cut off.
    """
    return replace(
        group,
        name=f"{group.name}-platforms",
        why=f"{group.why} This row is macOS and Windows.",
        runners=(MACOS, WINDOWS),
        timeout=group.timeout + PLATFORM_HEADROOM,
    )


ON_A_REAL_TOOL: tuple[Group, ...] = (
    Group(
        name="browser",
        why="Everything that needs layout or a compositor, in the Chromium `decktalk install` fetches.",
        commands=(Suite(marker="browser", measured="browser"),),
        runners=(LINUX,),
        tools=("chromium",),
        timeout=25,
        when=("pr", "main"),
        env=measured("browser"),
    ),
    Group(
        name="media",
        why="Frame and audio measurement against the real ffmpeg, on synthetic files the tests build.",
        commands=(Suite(marker="media", measured="media"),),
        runners=(LINUX,),
        tools=("ffmpeg",),
        timeout=25,
        when=("pr", "main"),
        env=measured("media"),
    ),
    Group(
        name="e2e",
        why="The pipeline fixture built end to end, and the docs samples DeckTalk writes from a real run.",
        commands=(Suite(marker="e2e", measured="e2e"), Generator(SAMPLES)),
        runners=(LINUX,),
        tools=("chromium", "ffmpeg"),
        timeout=30,
        when=("pr", "main"),
        # This suite drives the command line as a subprocess, and a subprocess measures nothing
        # unless it is told where the configuration is. Without this the leg reports no coverage at
        # all, which reads exactly like a leg that passed.
        env=(*measured("e2e"), ("COVERAGE_PROCESS_START", str(MANIFEST))),
    ),
)
"""The three groups that drive a real tool, each written once and run on Linux and on the other two.

Every other group is the same on three platforms or is about one of them already, so these are the
only rows `elsewhere()` makes a second of.
"""


GROUPS: tuple[Group, ...] = (
    Group(
        name="lint",
        why="Style, types and shell held to one set of rules, so no review spends a comment on them.",
        # The hooks config is the one place a lint rule, its arguments and its pin are written, so
        # this row runs every hook over every file and nothing else: ruff, ty, Biome, shellcheck,
        # zizmor, the lock check and the house rules for prose. A hook that fixes a file fails here
        # and prints what it changed.
        commands=(Run(("prek", "run", "--all-files", "--show-diff-on-failure")),),
        runners=(LINUX,),
        tools=("npm",),
        timeout=10,
        when=("pr", "main"),
    ),
    Group(
        name="playwright-pair",
        why="conda-forge holds the Playwright client and its driver at the minor pyproject.toml names.",
        commands=(Run((*PYTHON, "scripts/check_playwright_pair.py")),),
        runners=(LINUX,),
        tools=("network",),
        # One request to anaconda.org per package, each held to 30 seconds by the script.
        timeout=5,
        # The row reads anaconda.org, so it gates no run of ci.yml, and an anaconda.org outage can
        # stop a release but never turn `main` red. release.yml runs it before the conda build, so
        # no release ships a Playwright minor conda-forge lacks on a platform a user installs on.
        # The unit suite holds the two ranges in pyproject.toml equal on every pull request, and
        # the conda row solves the pair on three platforms.
        when=(),
    ),
    Group(
        name="unit",
        why="Every test that needs no tool, which the collection hook makes the default suite.",
        # The suite runs on every core the runner has, which halves the leg every push waits on.
        # pytest-cov combines what each worker measured into the one data file the floor reads.
        commands=(Suite(parallel=True, measured="unit"),),
        runners=(LINUX,),
        environments=supported(),
        tools=(),
        timeout=15,
        when=("pr", "main"),
        # The floor is one number over every suite, and this is the suite that reaches most of the
        # package, so a floor combined without it is a floor no complete run could meet.
        env=measured("unit"),
    ),
    Group(
        name="floors",
        why="The suite that needs no tool, with every direct dependency at the lowest version pyproject.toml allows.",
        # pixi.lock solves the `floors` environment at the lowest version each direct dependency
        # admits, so every other row passes whatever a floor says and this one judges the floors. A
        # test holds each locked version to its floor, because a lock keeps a version it already
        # holds when a floor moves down.
        commands=(Suite(parallel=True),),
        runners=(LINUX,),
        environments=("floors",),
        tools=(),
        timeout=15,
        when=("pr", "main"),
    ),
    Group(
        name="node",
        why="The runtime's pure functions and the release's next version, under node --test, with no framework.",
        commands=(Run(("node", "--test", RUNTIME_TESTS, SCRIPT_TESTS)),),
        runners=(LINUX,),
        tools=("npm",),
        timeout=10,
        when=("pr", "main"),
    ),
    *ON_A_REAL_TOOL,
    *(elsewhere(group) for group in ON_A_REAL_TOOL),
    Group(
        name="platform",
        why="The short list only macOS or Windows can prove, plus the two commands every machine runs.",
        # `decktalk install` is this row's preparation, so every run of it proves the fetch works
        # unattended on all three platforms before the suite asserts what the fetch left behind.
        commands=(Suite(marker="platform"), Run(("decktalk", "doctor"))),
        runners=EVERY_PLATFORM,
        tools=("chromium", "ffmpeg"),
        timeout=20,
        when=("pr", "main"),
    ),
    Group(
        name="generated",
        why="Every generated file held to the source it is generated from, and every link in them.",
        # The runtime bundles are compiled by the pinned TypeScript, so the row needs npm. No
        # generator launches a browser, so the row fetches none.
        commands=(
            *(Generator(name) for name in GENERATORS),
            Run((*PYTHON, "scripts/check_docs_links.py")),
        ),
        runners=(LINUX,),
        tools=("npm",),
        timeout=20,
        when=("pr", "main"),
    ),
    Group(
        name="rehearsal",
        why="The version bump release-please makes, rehearsed in a copy that regenerates, checks and builds its wheel.",
        # The release path otherwise runs only on release-please's own pull request, which is the
        # last place a failure should surface. The script bumps a throwaway copy of
        # the checkout, so a contributor who runs this row locally keeps the tree they had. The next
        # version is computed by release-please's own code from the history since the last tag, so
        # the row needs the Node packages in the checkout and the whole history, which `history`
        # asks the workflow's checkout for. The copy runs the wheel row, which reads PyPI.
        commands=(Run((*PYTHON, "scripts/rehearse_release.py")),),
        runners=(LINUX,),
        tools=("npm", "history", "network"),
        timeout=20,
        when=("pr", "main"),
    ),
    Group(
        name="coverage",
        why="One floor, measured on Linux, failing when a suite it combines never reported.",
        commands=(
            # The data files are kept rather than consumed, because the check reads them to learn
            # which suites reported, and a suite that is missing is the one thing a combined total
            # cannot show: it looks exactly like a suite that passed. The report comes after the
            # check for the same reason, because measurement is parallel here and a report combines
            # every data file it finds, which consumes them.
            Run((*PYTHON, "-m", "coverage", "combine", "--keep")),
            Run((*PYTHON, "scripts/check_coverage.py", "--check")),
            Run((*PYTHON, "-m", "coverage", "report")),
        ),
        runners=(LINUX,),
        tools=(),
        timeout=10,
        when=("pr", "main"),
    ),
    Group(
        name="wheel",
        why="What `python -m build` writes, installed from PyPI on a machine that has only the wheel and the tag.",
        # The build uses the hatchling pixi.lock pins rather than one it fetches. `--no-isolation`
        # still refuses a hatchling below the floor `[build-system]` names. check_wheel.py installs
        # the wheel as a user's installer does, with its dependencies from PyPI, so the row reads
        # the network.
        commands=(
            Run((*PYTHON, "-m", "build", "--no-isolation")),
            Suite(marker="wheel"),
            Run((*PYTHON, "scripts/check_wheel.py")),
        ),
        runners=EVERY_PLATFORM,
        tools=("network",),
        timeout=15,
        when=("pr", "main"),
    ),
    Group(
        name="conda",
        why="The conda package `pixi publish` builds, installed by `pixi global install` into an empty Pixi home.",
        # The package is what `pixi global install` serves a user, and PyPI's upload has no undo, so
        # every pull request builds it and release.yml builds it with this row before PyPI is
        # reached. `--force` replaces a package of the same version a run left before, which Pixi
        # would otherwise keep. The install solves the package's dependencies from conda-forge, so
        # the row reads the network, on every platform a user installs on.
        commands=(
            Run(("pixi", "publish", "--path", ".", "--target-dir", "dist-conda", "--force")),
            Run((*PYTHON, "scripts/check_conda.py")),
        ),
        runners=EVERY_PLATFORM,
        tools=("chromium", "ffmpeg", "network"),
        timeout=20,
        when=("pr", "main"),
    ),
    Group(
        name="scaffold",
        why="Every packaged project recorded again and again, held to the frames one recording of it holds.",
        # The row judges what was already released rather than gating the release: it runs weekly,
        # because it records every packaged project several times over, and `e2e` builds and
        # verifies each of them once on every pull request. A release is never more than a week from
        # its first scaffold run. A manual run of ci.yml that chooses the `schedule` moment runs it on
        # demand. Its tests run on every core, because one after another they outgrow the row's limit.
        commands=(Suite(marker="scaffold", parallel=True),),
        runners=(LINUX,),
        tools=("chromium", "ffmpeg"),
        timeout=30,
        when=("schedule",),
    ),
    Group(
        name="installer",
        why="The one-line installer run for real, on images that start with nothing but a package manager.",
        # The Pixi download runs whatever the channel holds, so the pinned version and its digests
        # are proved on a real machine from the first run. The two musl scenarios need no channel.
        # Every other scenario installs from the conda channel, and skips with its reason while the
        # channel holds fewer releases than it needs.
        commands=(
            Scenario(DEBIAN, "download-the-pinned-pixi"),
            Scenario(DEBIAN, "install-in-a-bare-image"),
            Scenario(UBUNTU, "install-in-a-bare-image"),
            Scenario(FEDORA, "install-in-a-bare-image"),
            Scenario(ALPINE, "refuse-musl"),
            Scenario(DEBIAN, "install-beside-musl"),
            Scenario(PIXI_IMAGE, "keep-the-pixi-already-there"),
            Scenario(DEBIAN, "install-the-pinned-version"),
            Scenario(DEBIAN, "rerun-upgrades"),
        ),
        runners=(LINUX,),
        tools=("docker", "network"),
        timeout=25,
        when=("main", "schedule"),
    ),
)

BY_NAME = {group.name: group for group in GROUPS}

FAST = ("lint", "unit")
"""What `--fast` expands to. It is an alias so that the first thing a contributor types teaches the
group vocabulary rather than hiding it."""

DEFAULT_WHEN = "pr"
"""A bare run is the pull request's set, which is every group that gates a change."""


def bare(word: str) -> str:
    """A word as a person reads it: a path into this checkout loses the checkout's own path where the path starts,
    which is the start of the word or of an option's value after `=`, and nowhere else, because a checkout at `/src`
    would otherwise clip the `src/` inside `runtime/src/*.test.ts`."""
    option, equals, value = word.partition("=")
    checkout = f"{ROOT}/"
    return f"{option.removeprefix(checkout)}{equals}{value.removeprefix(checkout)}"


def shell(argv: tuple[str, ...]) -> str:
    """One line a person reads, with this checkout's own path left out."""
    return " ".join(bare(part) for part in argv)


def legs(groups: tuple[Group, ...]) -> list[dict[str, Any]]:
    """One matrix row per group, runner and Pixi environment, which is what a workflow consumes."""
    return [
        {
            "group": group.name,
            "runs-on": runner,
            "environment": environment,
            "timeout-minutes": group.timeout,
            "tools": list(group.tools),
            "cache": tools_key(group, runner),
            "npm": npm_key(group, runner),
            "leg": f"{group.name} ({runner}, {environment})",
        }
        for group in groups
        for runner in group.runners
        for environment in group.environments
    ]


def pixi_version() -> str:
    """The Pixi every leg and every release job installs, which is the `requires-pixi` floor of pyproject.toml.

    setup-pixi reads no version from the manifest, and a workflow needs the version before any Pixi
    environment exists, so the workflow asks this script with the runner's own Python.
    """
    required = manifest()["tool"]["pixi"]["workspace"]["requires-pixi"]
    floor = required.removeprefix(">=")
    if floor == required or not floor.replace(".", "").isdigit():
        raise SystemExit(f"requires-pixi is {required!r}, and setup-pixi and install.sh take one floor, as >=X.Y.Z.")
    return floor


def selected(names: list[str], when: str | None) -> tuple[Group, ...]:
    """The groups a run asked for, by name or by the moment they gate."""
    if names:
        unknown = [name for name in names if name not in BY_NAME]
        if unknown:
            raise SystemExit(f"no such group: {', '.join(unknown)}. The groups are {', '.join(BY_NAME)}.")
        return tuple(BY_NAME[name] for name in names)
    return tuple(group for group in GROUPS if (when or DEFAULT_WHEN) in group.when)


IN_ENVIRONMENT = ("pixi", "run", "--locked", "-e")
"""How a row's command enters one of the row's own environments when the run is in another."""

INSTALL_ENVIRONMENT = ("pixi", "install", "--locked", "-e")
"""How `--setup` installs an environment a row enters, so the row's first run downloads nothing."""


def entered(group: Group) -> tuple[str, ...]:
    """What a row's commands are prefixed with so that they run in an environment the row names.

    A run in one of the row's environments runs its commands as they are, which is every CI leg,
    because setup-pixi activates the leg's environment. A local run from another environment, such
    as `pixi run check` reaching the floors row from the default environment, enters the row's
    first environment, so the row judges what it says it judges wherever it is started.
    """
    current = os.environ.get("PIXI_ENVIRONMENT_NAME", DEFAULT_ENVIRONMENT)
    return () if current in group.environments else (*IN_ENVIRONMENT, group.environments[0])


def run(command: Command, extra: tuple[tuple[str, str], ...] = (), inside: tuple[str, ...] = ()) -> bool:
    argv = (*inside, *command.argv)
    print(f"\n$ {shell(argv)}", flush=True)
    started = time.monotonic()
    env = {**os.environ, **dict(extra)}
    tool, *rest = argv
    # The tool is looked up rather than handed to the process table, because Windows spells npm as
    # npm.cmd and a name it cannot resolve is a traceback out of subprocess rather than a sentence
    # about a tool this machine does not have.
    program = shutil.which(tool, path=env.get("PATH"))
    if program is None:
        print(f"{tool} is not on this machine's PATH, so nothing ran. Install it and run this group again.")
        print(f"FAILED (no {tool}) in {time.monotonic() - started:.1f}s", flush=True)
        return False
    code = subprocess.call((program, *rest), cwd=ROOT, env=env)
    print(f"{'ok' if code == 0 else f'FAILED (exit {code})'} in {time.monotonic() - started:.1f}s", flush=True)
    return code == 0


def reproduce(group: Group, mode: str = "") -> str:
    """The one local command that runs `group` again, in the Pixi environment this run is in.

    Pixi names the environment it entered in `PIXI_ENVIRONMENT_NAME`, and setup-pixi's activation
    sets the same variable on a CI leg, so a py313 leg prints the command that picks py313.
    """
    name = os.environ.get("PIXI_ENVIRONMENT_NAME", DEFAULT_ENVIRONMENT)
    chosen = "" if name == DEFAULT_ENVIRONMENT else f" -e {name}"
    return f"pixi run{chosen} check --group {group.name}{mode}"


def run_group(group: Group, mode: str = "") -> bool:
    """Run one group, opening with the name and the one local command that reproduces it."""
    print(f"\n== {group.name}: {reproduce(group, mode)}", flush=True)
    print(f"   {group.why}", flush=True)
    for name, value in group.env:
        print(f"   {name}={bare(value)}", flush=True)
    inside = entered(group)
    return all(run(command, group.env, inside) for command in group.steps)


NO_TOOLS = "nothing beyond Pixi"
"""What a row that fetches nothing says it needs, because an empty cell reads as a gap."""

PLATFORM_NAMES = {LINUX: "Linux", MACOS: "macOS", WINDOWS: "Windows"}
"""The runners as the words a contributor uses rather than the labels GitHub uses."""


RELEASE_ONLY = "a release only"
"""What a row that gates no run of ci.yml says for its moment, because release.yml runs it."""


def summary(group: Group) -> tuple[str, str, str, str]:
    """A row's first check with how many follow it, what it needs, where it runs and when it gates.

    The table below and the one `build_contributing.py` writes into CONTRIBUTING.md are two renderings
    of these four facts, so a person reading either reads the same row.
    """
    more = f", and {len(group.commands) - 1} more" if len(group.commands) > 1 else ""
    return (
        f"`{group.commands[0].shown}`{more}",
        ", ".join(group.tools) or NO_TOOLS,
        ", ".join(PLATFORM_NAMES[runner] for runner in group.runners),
        ", ".join(group.when) or RELEASE_ONLY,
    )


def table() -> str:
    """Every group with its first check, what it needs and the job that calls it.

    No wall time is printed. A time typed into this table would be a number nothing checks, so the
    time a group takes is read from the job that ran it rather than from here.
    """
    rows = []
    for group in GROUPS:
        runs, needs, where, when = summary(group)
        leg = f", run in ci as {group.name} (runner, environment)" if group.when else ""
        rows.append(
            f"  {group.name}\n      {runs}\n      needs {needs} on {where}, gates on {when}{leg}\n      {group.why}"
        )
    return "\n".join(rows)


def epilog() -> str:
    return (
        "the groups:\n"
        + table()
        + f"\n\n--fast is an alias for --group {','.join(FAST)}."
        + "\nA group runs every command in its row. Nothing here can be skipped or made advisory."
    )


def arguments() -> argparse.ArgumentParser:
    """The command line, whose two modes that change what a row runs cannot be combined."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog(),
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        metavar="NAME",
        help="run this group, repeatable and comma-separated",
    )
    parser.add_argument("--fast", action="store_true", help=f"an alias for --group {','.join(FAST)}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        WRITE,
        action="store_true",
        help="run each named group's generators with --write instead of --check, and its other checks not at all",
    )
    mode.add_argument(
        PREPARE,
        action="store_true",
        help="run only the commands that fetch what each named group keeps in the tools cache",
    )
    mode.add_argument(
        SETUP,
        action="store_true",
        help="run every preparation any row names, once each, which readies a new clone for every row",
    )
    parser.add_argument("--list", action="store_true", help="print the table and run nothing")
    parser.add_argument("--json", action="store_true", help="print the matrix a workflow consumes, and run nothing")
    parser.add_argument(
        "--pixi-version", action="store_true", help="print the Pixi version a workflow installs, and run nothing"
    )
    parser.add_argument(
        "--when",
        choices=MOMENTS,
        help=f"the groups that gate at this moment, default {DEFAULT_WHEN}",
    )
    return parser


def counted(groups: tuple[Group, ...]) -> str:
    """How many groups a run covered, as a reader says it."""
    return f"{len(groups)} group" + ("s" if len(groups) != 1 else "")


def run_writes(groups: tuple[Group, ...]) -> int:
    """Run every named group with its generators writing, which is what a release pull request needs."""
    writers = tuple(writer(group) for group in groups)
    started = time.monotonic()
    if not all(run_group(group, f" {WRITE}") for group in writers):
        return 1
    print(f"\nwrote every generated file of {counted(writers)} in {time.monotonic() - started:.0f}s", flush=True)
    return 0


def run_preparations(groups: tuple[Group, ...]) -> int:
    """Run only what fetches each named group's cached tools, which CI does before it saves the tools cache.

    A need the cache does not keep, such as the Node packages, is left to the row's own run, so a leg
    never installs it twice.
    """
    preparing = tuple(
        replace(group, tools=tuple(tool for tool in group.tools if NEEDS[tool].cached), commands=()) for group in groups
    )
    return 0 if all(run_group(group, f" {PREPARE}") for group in preparing) else 1


def setup_commands() -> tuple[Run, ...]:
    """Every preparation any row names, once each, in the order the table first names them.

    Then an install of every environment a run from the default environment enters, which is the
    first environment of a row that does not run in the default one, such as `floors`.
    """
    preparations = (command for group in GROUPS for command in group.preparations)
    entered = (
        Run((*INSTALL_ENVIRONMENT, group.environments[0]))
        for group in GROUPS
        if DEFAULT_ENVIRONMENT not in group.environments
    )
    return tuple(dict.fromkeys((*preparations, *entered)))


def run_setup() -> int:
    """Run every row's preparation once, which is what `pixi run setup` runs before it installs the git hooks."""
    print(f"\n== setup: pixi run check {SETUP}", flush=True)
    print("   Every preparation the table names and every environment a row enters, so a clone is ready.", flush=True)
    return 0 if all(run(command) for command in setup_commands()) else 1


def run_checks(groups: tuple[Group, ...]) -> int:
    """Run every named group in order, stopping at the first that fails, and name the rows not run."""
    started = time.monotonic()
    for group in groups:
        if not run_group(group):
            return 1
    print(f"\n{counted(groups)} passed in {time.monotonic() - started:.0f}s", flush=True)
    for group in GROUPS:
        if group not in groups:
            print(f"not run: {group.name:<10} {group.why}", flush=True)
    return 0


def main() -> int:
    parser = arguments()
    args = parser.parse_args()

    if args.pixi_version:
        print(pixi_version())
        return 0
    names = [name for value in args.group for name in value.split(",") if name]
    if args.fast:
        names = [*FAST, *names]
    if args.setup:
        if names:
            parser.error("--setup prepares the machine for every row, so it takes no --group.")
        return run_setup()
    if (args.write or args.prepare) and not names:
        parser.error("--write and --prepare change what a row runs, so they run only the groups --group names.")
    groups = selected(names, args.when)

    # `--list` and `--json` are the same answer in two renderings, so asking for both is asking for
    # the listing a workflow reads rather than for the table and then nothing.
    if args.list or args.json:
        print(json.dumps(legs(groups)) if args.json else epilog())
        return 0
    if args.prepare:
        return run_preparations(groups)
    if args.write:
        return run_writes(groups)
    return run_checks(groups)


if __name__ == "__main__":
    sys.exit(main())
