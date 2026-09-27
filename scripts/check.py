# /// script
# requires-python = ">=3.12"
# ///
"""Every check DeckTalk runs, in one table, spelled once.

    uv run scripts/check.py                  # every group a pull request runs, in order
    uv run scripts/check.py --fast           # lint and unit alone, the two a change most often fails
    uv run scripts/check.py --group browser  # one group, by name, repeatable and comma-separated
    uv run scripts/check.py --list           # the table, for a person
    uv run scripts/check.py --group generated --write  # every generator in the group, writing
    uv run scripts/check.py --group e2e --prepare      # only what fetches the group's tools
    uv run scripts/check.py --json --when pr # the matrix, for a workflow

`GROUPS` below is the only place any check is written down. A workflow reads this table at runtime and
names no command of its own, so a workflow cannot disagree with it. There is no switch that skips a
check and no way to mark one advisory, because a knob that exists becomes permanent. A run that
selects fewer groups than the full set prints the rows it did not run and why, so a short run is never
mistaken for a complete one.

`--write` turns a group's checks into the commands that fix them. Every `build_*.py --check` in the
group runs as `--write` instead, the commands that prepare the machine run as they are, and a
command that only judges is left out. The write commands are read from the same rows, so the one
command that regenerates what a release made stale cannot forget a generator the check remembers.

`--prepare` runs only the commands at the head of a row, which fetch what the row declares it needs.
CI runs it before it saves the tools cache, so a cache is only ever saved from a fetch that finished.

The first run may download headless Chromium and ffmpeg through `decktalk install`, once per machine.
No check needs an ElevenLabs key, and after `decktalk install` no check needs the network.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UV = ("uv", "run")
"""Every Python command runs in the project environment, so the lockfile decides what it runs."""

PYTEST = (*UV, "pytest", "-q", "-rs")
"""How every suite is run. `-rs` prints the reason of every skip, so a skipped test is never silent in a log."""

PARALLEL = ("-n", "auto")
"""What a suite adds to run on every core, which pytest-xdist reads and only the unit row passes.

The suites that drive a real tool stay serial, because a recording shares the machine's compositor
and two at once is a timing measurement of the runner rather than of the deck.
"""

MEASURE = ("--cov", "--cov-report=")
"""What a suite adds to measure itself, which is the data file and no report of its own."""

REPORTS = ROOT / "tests" / "out" / "junit"
"""Where every measuring suite writes the JUnit report of what it ran, one file named after the group.

Coverage data says which lines a suite reached, and a suite whose every test skipped still reaches
the lines its imports run, so the data alone called such a suite reporting. The report says how
many tests ran and how many skipped, which is what `check_coverage.py` needs to call it silent.
"""


def measuring(name: str, *selection: str) -> tuple[str, ...]:
    """The suite of the group `name`, measuring its coverage and writing the report of what it ran."""
    return (*PYTEST, *selection, *MEASURE, f"--junitxml={REPORTS / f'{name}.xml'}")


WHEEL_TEST = "tests/contract/test_wheel.py"
"""The test of the built wheel, which only the wheel group runs, right after `uv build` writes one."""

LINT_TESTS = (
    "tests/contract/test_prose.py",
    "tests/contract/test_vocabulary.py",
    "tests/contract/test_numbers.py",
)
"""The house rules for prose, vocabulary and numbers, which read the repository's files as text.

They are lint rather than behaviour, so they run once in the lint row. In the unit suite they ran
on three Pythons for one answer and counted as a fifth of the tests the suite claimed.
"""

ELSEWHERE: dict[str, str] = {
    WHEEL_TEST: "wheel",
    **dict.fromkeys(LINT_TESTS, "lint"),
}
"""Every test file the unit suite leaves to another row, and the row that runs it instead.

A contract held by two rows runs twice for one answer, and the wheel test built a wheel of its own in
unit on three Pythons and again in the wheel row on three platforms, which was nine builds per pull
request. Each file named here runs in its row alone, and a test holds every name to that row.
"""


def ignoring_elsewhere() -> tuple[str, ...]:
    """What the unit suite passes so that it leaves every file in `ELSEWHERE` to the row that owns it."""
    return tuple(f"--ignore={path}" for path in ELSEWHERE)


def selected_marker(command: tuple[str, ...]) -> str | None:
    """The marker a suite's command names with `-m`, or None for the suite that needs no tool."""
    if "pytest" not in command or "-m" not in command:
        return None
    return command[command.index("-m") + 1]


def measured(name: str) -> tuple[tuple[str, str], ...]:
    """The data file one group writes, named after the group so that no two groups overwrite each other.

    `coverage combine` reads every `.coverage.*` beside it, so naming each group's file is what lets
    a whole run on one machine be combined at the end. Without this each suite wrote `.coverage` and
    the last suite to finish was the only one the floor ever saw.

    The path is absolute because a suite that drives the command line starts its subprocesses in the
    project they are building, and a relative name would leave each subprocess writing its measure
    into a temporary directory nothing ever reads.
    """
    return (("COVERAGE_FILE", str(ROOT / f".coverage.{name}")),)


LINUX, MACOS, WINDOWS = "ubuntu-latest", "macos-latest", "windows-latest"
EVERY_PLATFORM = (LINUX, MACOS, WINDOWS)

FLOOR = "3.12"
"""The lowest Python DeckTalk supports, and the only one macOS and Windows carry."""

EVERY_PYTHON = (FLOOR, "3.13", "3.14")

NODE = "22"
"""The Node every leg that lists npm runs, which is the floor `package.json` sets and ci.yml reads from the matrix."""

TOOLS = {
    "shellcheck": "0.11.0.1",
    "zizmor": "1.30.1",
}
"""Every tool the lint row fetches by version, because neither the lockfile nor `package.json` holds it.

`shellcheck` is also the `shellcheck-py` rev in `.pre-commit-config.yaml`, and `zizmor` is named here
alone. A tool fetched without a version is a different tool on the day it releases, which is a check
that changes its mind on its own. Ruff is pinned by the lockfile and Biome by `package.json`.
"""

PYPI_DECKTALK = "https://pypi.org/pypi/decktalk/json"

RUNTIME_TESTS = "tests/decktalk/runtime/src/*.test.ts"
"""Every test of the runtime, named as a pattern because Node 22 runs a directory rather than reading it.

`node --test <dir>` searches the directory on Node 24 and later and runs the directory itself as a
module on Node 22, which is the version `package.json` sets as the floor and the version CI has. A
pattern is expanded by the test runner on every version, so this one string is what both this table
and the `test` script in `package.json` name.
"""

SCRIPT_TESTS = "tests/scripts/*.test.mjs"
"""Every test of a Node script under `scripts/`, named the same way and run beside the runtime's."""


# What `install.sh` has to survive: an image with nothing but curl on it. The installer's own
# promise is that a machine that has never had DeckTalk ends with `decktalk --version` printing one,
# so the whole check is that line, run in a shell the installer did not write.
INSTALL_IN_A_BARE_IMAGE = """
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq && apt-get install -y -qq curl >/dev/null
    else
      dnf install -y -q curl >/dev/null
    fi
    sh /install.sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    decktalk --version
"""

# Playwright publishes no musllinux wheels, so the resolver fails on musl whatever the installer does.
# The check is that the installer says so and stops before installing anything, not that it fails late.
REFUSE_MUSL = """
    apk add --no-cache curl >/dev/null
    set +e
    out="$(sh /install.sh 2>&1)"
    code=$?
    set -e
    printf "%s\\n" "$out"
    if [ "$code" -ne 1 ]; then
      echo "expected exit 1 on musl, got $code"
      exit 1
    fi
    case "$out" in
    *musl*glibc*) ;;
    *) echo "the refusal never says musl and glibc, so it teaches nothing"; exit 1 ;;
    esac
    if command -v uv >/dev/null 2>&1; then
      echo "it installed uv before refusing"
      exit 1
    fi
"""

# uv's own image carries uv and no curl, which also proves the installer needs no downloader of its
# own once uv is there.
KEEP_THE_UV_THAT_IS_ALREADY_THERE = """
    out="$(sh /install.sh)"
    printf "%s\\n" "$out"
    case "$out" in
    *"is already installed"*) ;;
    *) echo "it did not recognise the uv that was already on PATH"; exit 1 ;;
    esac
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    decktalk --version
"""

# The release before the current one. Pinning to the latest version would pass on an installer that
# dropped the pin on the floor and installed the latest anyway.
INSTALL_THE_PINNED_VERSION = f"""
    apt-get update -qq && apt-get install -y -qq curl jq >/dev/null
    DECKTALK_VERSION="$(curl -LsSf {PYPI_DECKTALK} | jq -r '.releases | keys_unsorted[]' | sort -V | tail -2 | head -1)"
    export DECKTALK_VERSION
    if [ -z "$DECKTALK_VERSION" ]; then
      echo "no released version to pin to" >&2
      exit 1
    fi
    sh /install.sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    # 0.4 prints `decktalk 0.4.1` and 0.5 prints `0.5.0rc2`, and the pin is whichever release is
    # second newest, so the name is dropped before the two versions are compared.
    got="$(decktalk --version)"
    got="${{got#decktalk }}"
    if [ "$got" != "$DECKTALK_VERSION" ]; then
      echo "pinned $DECKTALK_VERSION, installed $got"
      exit 1
    fi
"""


def in_image(image: str, script: str) -> tuple[str, ...]:
    """`script` run by POSIX sh inside `image`, with `install.sh` mounted read only and nothing else."""
    return ("docker", "run", "--rm", "-v", f"{ROOT / 'install.sh'}:/install.sh:ro", image, "sh", "-euc", script)


NPM_CI = ("npm", "ci")
"""The pinned Node toolchain, which the runtime generator compiles with and the linters run from."""

INSTALL = (*UV, "decktalk", "install")
"""Chromium and ffmpeg, fetched by the command a person runs, which is a no-op on a machine that has both."""


@dataclass(frozen=True)
class Need:
    """One thing a group needs on its runner, and what provides it.

    `prepare` is the command at the head of the row that provides it, or None when the workflow
    provides it before the row starts, which ci.yml does by reading the need's name from the matrix.
    A need that neither a command nor the workflow provides is a promise nothing keeps, which is how
    the browser and e2e rows once passed in CI with every test skipped.
    """

    def __post_init__(self) -> None:
        if self.prepare is None and not self.workflow:
            raise ValueError(f"nothing provides the need described as: {self.why}")

    why: str
    prepare: tuple[str, ...] | None = None
    cached: bool = False  # whether the workflow keeps what `prepare` downloaded, keyed by `tools_key`
    workflow: bool = False  # whether ci.yml acts on this need before the row starts


NEEDS: dict[str, Need] = {
    "npm": Need(
        why="Node, which the workflow installs, and the pinned packages, which the row installs.",
        prepare=NPM_CI,
        workflow=True,
    ),
    "chromium": Need(
        why="The headless Chromium the recorder drives and `build_assets.py` measures the hero in.",
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
}
"""Every need a row may declare. uv is not one of them, because every row runs through it."""

MOMENTS = ("pr", "main", "schedule")
"""The moments a row can gate at, which are the three ci.yml computes from the event that started it.

A pull request is `pr`, a push to `main` and a manual run are `main`, and the weekly run is
`schedule`. A release runs no check of its own, because release.yml consumes the verdict of the run
on the tip of `main`, so a release gates on exactly what `main` gates on and has no column here.
"""

LOCKFILE = ROOT / "uv.lock"
FFMPEG_PIN = ROOT / "src" / "decktalk" / "toolchain" / "ffmpeg_fetch.py"
FFMPEG_VERSION = "FFMPEG_VERSION"
"""The name `ffmpeg_fetch.py` gives its pin, read from the source because this script imports no package."""


def locked_version(package: str) -> str:
    """The version of `package` the lockfile resolves, which decides the Chromium revision Playwright fetches."""
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    return next(str(entry["version"]) for entry in lock["package"] if entry["name"] == package)


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

    The key names the leg and the two pins that decide what `decktalk install` downloads, and nothing
    else. A key shared by every leg let whichever leg saved first decide the cache for every later
    run, so one suite ran 102 tests on one run and 5 on the next. A key on the whole lockfile threw
    the download away on every unrelated dependency bump.
    """
    if not any(NEEDS[tool].cached for tool in group.tools):
        return ""
    return f"tools-{group.name}-{runner}-playwright-{locked_version('playwright')}-ffmpeg-{ffmpeg_pin()}"


CHECK, WRITE, PREPARE = "--check", "--write", "--prepare"

GENERATES = "build_"
"""The prefix every generator's script carries, and the one thing that tells a generator from a check."""


def generator(name: str) -> tuple[str, ...]:
    """A generated file held to its source. Every generator takes `--check` and `--write` alike.

    The script is named to the project's own interpreter rather than run as a file. No generator
    carries an inline script header, so either form runs in the project environment today, and
    naming the interpreter keeps it there even if one gains a header, because a file with a header
    is resolved by `uv run` in an environment of its own and most generators read the package they
    generate from. The lockfile decides what a generator sees, the same way it decides what a test
    sees.
    """
    return (*UV, "python", f"scripts/{name}.py", CHECK)


def writing(command: tuple[str, ...]) -> tuple[str, ...] | None:
    """The command that writes what `command` checks, or None when it only judges.

    A generator is a `scripts/build_*.py` run with `--check`, and its write is the same command with
    `--write`, so a generator that needs `uv run --with` to check needs it to write as well. A check
    that generates nothing, such as `check_docs_links.py` or `check_wheel.py`, has no write at all.
    """
    generates = any(Path(part).name.startswith(GENERATES) for part in command)
    if not generates or command[-1] != CHECK:
        return None
    return (*command[:-1], WRITE)


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
    commands: tuple[tuple[str, ...], ...]
    runners: tuple[str, ...]
    pythons: tuple[str, ...]
    tools: tuple[str, ...]  # names in NEEDS, each provided by a command at the head of the row or by the workflow
    timeout: int  # minutes, which is the CI job's timeout-minutes
    when: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()  # what this group's commands need in the environment

    def __post_init__(self) -> None:
        unknown = [tool for tool in self.tools if tool not in NEEDS]
        if unknown:
            raise ValueError(f"the group {self.name} needs {', '.join(unknown)}, which no row can provide.")
        never = [moment for moment in self.when if moment not in MOMENTS]
        if never:
            raise ValueError(f"the group {self.name} gates at {', '.join(never)}, which no run of ci.yml is.")

    @property
    def preparations(self) -> tuple[tuple[str, ...], ...]:
        """The commands that provide what this row needs, once each, in the order the row names them."""
        commands: list[tuple[str, ...]] = []
        for tool in self.tools:
            prepare = NEEDS[tool].prepare
            if prepare is not None and prepare not in commands:
                commands.append(prepare)
        return tuple(commands)

    @property
    def steps(self) -> tuple[tuple[str, ...], ...]:
        """Everything a run of this row executes, which is its preparations and then its checks."""
        return (*self.preparations, *self.commands)


REPORT_TIMING = "--timing=report"
"""What a leg whose compositor is not trustworthy passes to a suite that measures a cue.

A hosted macOS or Windows runner composites through a stack DeckTalk does not own, and a hosted
Linux runner that renders in software presents a frame tens of milliseconds after the paint it
answers. Either way the measurement moves and the deck did not, so these legs report a late reveal
and Linux is the one meant to gate it, once `LINUX_GATES_TIMING` says it may. This weakens nothing
else: `tests/support/timing_policy.py` tolerates a late landing alone, and a cue that never changed
the picture still fails every runner.
"""


def reports_timing(command: tuple[str, ...]) -> tuple[str, ...]:
    """The same command with cue timing reported, which only a suite has an opinion about.

    The flag is `tests/conftest.py`'s own option, so it is added to the suites and to nothing else.
    A tool that never collected a test would exit on an argument it has never heard of.
    """
    return (*command, REPORT_TIMING) if "pytest" in command and REPORT_TIMING not in command else command


LINUX_GATES_TIMING = False
"""Whether a late reveal fails the Linux e2e row, which is the one row meant to gate cue timing.

It reports for now. Until every row fetched the tools it declares, that row skipped every test, so
cue timing has never been measured on a GitHub Linux runner and nobody knows yet whether its
compositor is trustworthy. When three runs of ci in a row on `main` show the row's log with no late
reveal, this becomes True and the row gates from then on, as `REPORT_TIMING` describes.
"""


def linux_timing(command: tuple[str, ...]) -> tuple[str, ...]:
    """The Linux row's suite with cue timing gated or reported, as `LINUX_GATES_TIMING` decides."""
    return command if LINUX_GATES_TIMING else reports_timing(command)


def elsewhere(group: Group) -> Group:
    """The same group on macOS and Windows, gating a merge and a release rather than a pull request.

    A group that drives a real tool is the only kind a second platform can fail on its own, and that
    happens a few times a year. Running all three on every push would make every change wait for
    three legs to buy one difference, so the Linux leg gates the change and this one gates the merge,
    which is still before a user meets it.

    These two runners are also the ones whose compositor is not trustworthy, so cue timing is
    reported here, and the Linux row of the same group is the one meant to gate it.
    """
    return replace(
        group,
        name=f"{group.name}-platforms",
        why=f"{group.why} This row is macOS and Windows, which gate a merge rather than a pull request.",
        commands=tuple(reports_timing(command) for command in group.commands),
        runners=(MACOS, WINDOWS),
        when=("main",),
    )


ON_A_REAL_TOOL: tuple[Group, ...] = (
    Group(
        name="browser",
        why="Everything that needs layout or a compositor, in the Chromium `decktalk install` fetches.",
        commands=(measuring("browser", "-m", "browser"),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("chromium",),
        timeout=25,
        when=("pr", "main"),
        env=measured("browser"),
    ),
    Group(
        name="media",
        why="Frame and audio measurement against the real ffmpeg, on synthetic files the tests build.",
        commands=(measuring("media", "-m", "media"),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("ffmpeg",),
        timeout=25,
        when=("pr", "main"),
        env=measured("media"),
    ),
    Group(
        name="e2e",
        why="The pipeline fixture built end to end, which samples the joint behaviour of every tool.",
        commands=(linux_timing(measuring("e2e", "-m", "e2e")),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("chromium", "ffmpeg"),
        timeout=30,
        when=("pr", "main"),
        # This suite drives the command line as a subprocess, and a subprocess measures nothing
        # unless it is told where the configuration is. Without this the leg reports no coverage at
        # all, which reads exactly like a leg that passed.
        env=(*measured("e2e"), ("COVERAGE_PROCESS_START", str(ROOT / "pyproject.toml"))),
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
        commands=(
            ("uv", "lock", "--check"),
            (*UV, "ruff", "check", "src", "tests", "scripts"),
            (*UV, "ruff", "format", "--check", "src", "tests", "scripts"),
            (*UV, "ty", "check", "src"),
            ("npm", "exec", "--no", "--", "biome", "ci", "."),
            ("uvx", "--from", f"shellcheck-py=={TOOLS['shellcheck']}", "shellcheck", "-s", "sh", "install.sh"),
            ("uvx", f"zizmor@{TOOLS['zizmor']}", ".github/workflows"),
            (*PYTEST, *LINT_TESTS),
        ),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("npm",),
        timeout=10,
        when=("pr", "main"),
    ),
    Group(
        name="unit",
        why="Every test that needs no tool, which the collection hook makes the default suite.",
        # The suite runs on every core the runner has, which halves the leg every push waits on.
        # pytest-cov combines what each worker measured into the one data file the floor reads.
        commands=(measuring("unit", *PARALLEL, *ignoring_elsewhere()),),
        runners=(LINUX,),
        pythons=EVERY_PYTHON,
        tools=(),
        timeout=15,
        when=("pr", "main"),
        # The floor is one number over every suite, and this is the suite that reaches most of the
        # package, so a floor combined without it is a floor no complete run could meet.
        env=measured("unit"),
    ),
    Group(
        name="node",
        why="The runtime's pure functions and the release's next version, under node --test, with no framework.",
        commands=(("node", "--test", RUNTIME_TESTS, SCRIPT_TESTS),),
        runners=(LINUX,),
        pythons=(FLOOR,),
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
        commands=(
            (*PYTEST, "-m", "platform"),
            (*UV, "decktalk", "doctor"),
        ),
        runners=EVERY_PLATFORM,
        pythons=(FLOOR,),
        tools=("chromium", "ffmpeg"),
        timeout=20,
        when=("pr", "main"),
    ),
    Group(
        name="generated",
        why="Every generated file held to the source it is generated from, and every link in them.",
        # The runtime bundles are compiled by the pinned TypeScript, so the row needs npm.
        # `build_assets.py` measures the hero's word widths in the real Chromium with the real font,
        # and a generator that launches Playwright directly reaches nothing that would fetch it, so
        # the row needs Chromium as much as the browser group does.
        commands=(
            generator("build_runtime"),
            generator("build_result_schemas"),
            generator("build_settings_schema"),
            generator("build_settings_reference"),
            generator("build_cli_reference"),
            generator("build_api"),
            generator("build_code_pages"),
            generator("build_agents_doc"),
            generator("build_outbound_reference"),
            generator("build_skills_list"),
            generator("build_contributing"),
            generator("build_changelog"),
            ("uv", "run", "--with", "fonttools[woff]>=4.50", "python", "scripts/build_assets.py", "--check"),
            ("uv", "run", "--with", "pyyaml>=6", "python", "scripts/check_docs_links.py", "--check"),
        ),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("npm", "chromium"),
        timeout=20,
        when=("pr", "main"),
    ),
    Group(
        name="rehearsal",
        why="The version bump release-please makes, rehearsed in a copy, then every generator written and checked.",
        # The release path otherwise runs only on release-please's own pull request, which is where
        # every failure of the first release candidate surfaced. The script bumps a throwaway copy of
        # the checkout, so a contributor who runs this row locally keeps the tree they had. The next
        # version is computed by release-please's own code from the history since the last tag, so
        # the row needs the Node packages in the checkout and the whole history, which `history`
        # asks the workflow's checkout for.
        commands=((*UV, "python", "scripts/rehearse_release.py"),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("npm", "chromium", "history"),
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
            (*UV, "coverage", "combine", "--keep"),
            (*UV, "scripts/check_coverage.py", "--check"),
            (*UV, "coverage", "report"),
        ),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=(),
        timeout=10,
        when=("pr", "main"),
    ),
    Group(
        name="wheel",
        why="What `uv build` writes, opened on a machine that has only the wheel and the tag.",
        commands=(
            ("uv", "build"),
            (*PYTEST, WHEEL_TEST),
            (*UV, "scripts/check_wheel.py", "--check"),
        ),
        runners=EVERY_PLATFORM,
        pythons=(FLOOR,),
        tools=(),
        timeout=15,
        when=("pr", "main"),
    ),
    Group(
        name="scaffold",
        why="Every packaged project recorded and verified without a voice, which is the scaffold's promise.",
        # The row judges what was already released rather than gating the release: it runs weekly,
        # because seven minutes on every merge bought one answer that the template's own data tests
        # give on every pull request. A release is never more than a week from its first scaffold run.
        #
        # This row records five projects in one job, so the runner renders in software throughout and
        # presents a reveal tens of milliseconds after the frame it belongs on. The promise being
        # judged is that a project out of the wheel builds and verifies, which the cue timing of the
        # machine it was built on is no part of, so this row reports a late landing and fails on
        # every other finding exactly as the gated rows do.
        commands=(reports_timing((*PYTEST, "-m", "scaffold")),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("chromium", "ffmpeg"),
        timeout=30,
        when=("schedule",),
    ),
    Group(
        name="installer",
        why="The one-line installer run for real, on images that start with nothing but a package manager.",
        commands=(
            in_image("debian:13-slim", INSTALL_IN_A_BARE_IMAGE),
            in_image("ubuntu:24.04", INSTALL_IN_A_BARE_IMAGE),
            in_image("fedora:42", INSTALL_IN_A_BARE_IMAGE),
            in_image("alpine:3.22", REFUSE_MUSL),
            in_image("ghcr.io/astral-sh/uv:debian-slim", KEEP_THE_UV_THAT_IS_ALREADY_THERE),
            in_image("debian:13-slim", INSTALL_THE_PINNED_VERSION),
        ),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=(),
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


def shell(command: tuple[str, ...]) -> str:
    """One line a person reads, with this checkout's own path and any embedded script left out."""
    parts = ["<shell script>" if "\n" in part else part.replace(f"{ROOT}/", "") for part in command]
    return " ".join(parts)


def legs(groups: tuple[Group, ...]) -> list[dict[str, object]]:
    """One matrix row per group, runner and Python, which is what a workflow consumes."""
    rows: list[dict[str, object]] = []
    for group in groups:
        for runner in group.runners:
            pythons = group.pythons if runner == LINUX else (FLOOR,)
            for python in pythons:
                rows.append(
                    {
                        "group": group.name,
                        "runs-on": runner,
                        "python": python,
                        "timeout-minutes": group.timeout,
                        "tools": list(group.tools),
                        "node": NODE,
                        "cache": tools_key(group, runner),
                        "leg": f"{group.name} ({runner}, {python})",
                    }
                )
    return rows


def selected(names: list[str], when: str | None) -> tuple[Group, ...]:
    """The groups a run asked for, by name or by the moment they gate."""
    if names:
        unknown = [name for name in names if name not in BY_NAME]
        if unknown:
            raise SystemExit(f"no such group: {', '.join(unknown)}. The groups are {', '.join(BY_NAME)}.")
        return tuple(BY_NAME[name] for name in names)
    return tuple(group for group in GROUPS if (when or DEFAULT_WHEN) in group.when)


def run(command: tuple[str, ...], extra: tuple[tuple[str, str], ...] = ()) -> bool:
    print(f"\n$ {shell(command)}", flush=True)
    started = time.monotonic()
    # uv runs this script in an environment of its own, and a nested `uv run` would warn about it.
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    env.update(extra)
    # The tool is looked up rather than handed to the process table, because Windows spells npm as
    # npm.cmd and a name it cannot resolve is a traceback out of subprocess rather than a sentence
    # about a tool this machine does not have.
    program = shutil.which(command[0], path=env.get("PATH"))
    if program is None:
        print(f"{command[0]} is not on this machine's PATH, so nothing ran. Install it and run this group again.")
        print(f"FAILED (no {command[0]}) in {time.monotonic() - started:.1f}s", flush=True)
        return False
    code = subprocess.call((program, *command[1:]), cwd=ROOT, env=env)
    print(f"{'ok' if code == 0 else f'FAILED (exit {code})'} in {time.monotonic() - started:.1f}s", flush=True)
    return code == 0


def run_group(group: Group, mode: str = "") -> bool:
    """Run one group, opening with the name and the one local command that reproduces it."""
    print(f"\n== {group.name}: uv run scripts/check.py --group {group.name}{mode}", flush=True)
    print(f"   {group.why}", flush=True)
    for name, value in group.env:
        print(f"   {name}={value.replace(f'{ROOT}/', '')}", flush=True)
    return all(run(command, group.env) for command in group.steps)


def table() -> str:
    """Every group with its first step, what it needs and the job that calls it.

    No wall time is printed. A time typed into this table was a number nothing checked, and every one
    that was measured against CI was wrong by a factor of two or more, so the time a group takes is
    read from the job that ran it rather than from here.
    """
    rows = []
    for group in GROUPS:
        steps = group.steps
        more = f" and {len(steps) - 1} more" if len(steps) > 1 else ""
        needs = ", ".join(group.tools) or "nothing beyond uv"
        rows.append(
            f"  {group.name}\n"
            f"      {shell(steps[0])}{more}\n"
            f"      needs {needs} on {', '.join(group.runners)}, "
            f"gates on {', '.join(group.when)}, run by ci / run ({group.name})\n"
            f"      {group.why}"
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
        help="run only the commands that fetch what each named group needs, which is what CI caches",
    )
    parser.add_argument("--list", action="store_true", help="print the table and run nothing")
    parser.add_argument("--json", action="store_true", help="print the matrix a workflow consumes, and run nothing")
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
    """Run only what fetches each named group's needs, which CI does before it saves the tools cache."""
    preparing = tuple(replace(group, commands=()) for group in groups)
    return 0 if all(run_group(group, f" {PREPARE}") for group in preparing) else 1


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

    names = [name for value in args.group for name in value.split(",") if name]
    if args.fast:
        names = [*FAST, *names]
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
