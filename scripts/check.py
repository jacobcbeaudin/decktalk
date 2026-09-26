# /// script
# requires-python = ">=3.12"
# ///
"""Every check DeckTalk runs, in one table, spelled once.

    uv run scripts/check.py                  # every group a pull request runs, in order
    uv run scripts/check.py --fast           # lint and unit alone, in a few seconds
    uv run scripts/check.py --group browser  # one group, by name, repeatable and comma-separated
    uv run scripts/check.py --list           # the table, for a person
    uv run scripts/check.py --group generated --write  # every generator in the group, writing
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

The first run may download headless Chromium and ffmpeg through `decktalk install`, once per machine.
No check needs an ElevenLabs key, and after `decktalk install` no check needs the network.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UV = ("uv", "run")
"""Every Python command runs in the project environment, so the lockfile decides what it runs."""

MEASURE = ("--cov", "--cov-report=")
"""What a suite adds to measure itself, which is the data file and no report of its own."""


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

TOOLS = {
    "ruff": "0.16.8",
    "biome": "2.5.13",
    "shellcheck": "0.11.0.1",
    "zizmor": "1.30.1",
}
"""Every tool pinned outside the lockfile, and the version every other file must agree on.

`ruff` is the dev group's floor in `pyproject.toml` and the `ruff-pre-commit` rev, `biome` is
`biome.json`'s `$schema` and the `@biomejs/biome` entry in `package.json`, `shellcheck` is the
`shellcheck-py` rev, and `zizmor` is named here alone. A tool fetched without a version is a
different tool on the day it releases, which is a check that changes its mind on its own.
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
    got="$(decktalk --version)"
    if [ "$got" != "decktalk $DECKTALK_VERSION" ]; then
      echo "pinned $DECKTALK_VERSION, installed $got"
      exit 1
    fi
"""


def in_image(image: str, script: str) -> tuple[str, ...]:
    """`script` run by POSIX sh inside `image`, with `install.sh` mounted read only and nothing else."""
    return ("docker", "run", "--rm", "-v", f"{ROOT / 'install.sh'}:/install.sh:ro", image, "sh", "-euc", script)


NPM_CI = ("npm", "ci")
"""The pinned Node toolchain, which the runtime generator compiles with and the linters run from."""

FETCH_CHROMIUM = (*UV, "python", "-m", "playwright", "install", "chromium")
"""The browser `build_assets.py` measures the hero in, fetched the way `media/browser.py` fetches it."""

PREPARES = (NPM_CI, FETCH_CHROMIUM)
"""The commands that prepare a machine rather than judge it, which a write needs as much as a check."""

CHECK, WRITE = "--check", "--write"

GENERATES = "build_"
"""The prefix every generator's script carries, and the one thing that tells a generator from a check."""


def generator(name: str) -> tuple[str, ...]:
    """A generated file held to its source. Every generator takes `--check` and `--write` alike.

    The script is named to the project's own interpreter rather than run as a file, because a file
    run by `uv run` is resolved as a standalone script in an environment of its own and six of these
    read the package they generate from. The lockfile decides what a generator sees, the same way it
    decides what a test sees.
    """
    return (*UV, "python", f"scripts/{name}.py", CHECK)


def writing(command: tuple[str, ...]) -> tuple[str, ...] | None:
    """The command that writes what `command` checks, the command itself when it prepares, or None.

    A generator is a `scripts/build_*.py` run with `--check`, and its write is the same command with
    `--write`, so a generator that needs `uv run --with` to check needs it to write as well. A check
    that generates nothing, such as `check_docs_links.py` or `check_wheel.py`, has no write at all.
    """
    if command in PREPARES:
        return command
    generates = any(Path(part).name.startswith(GENERATES) for part in command)
    if not generates or command[-1] != CHECK:
        return None
    return (*command[:-1], WRITE)


def writer(group: Group) -> Group:
    """The same group with every generator writing, every preparation kept and every check left out."""
    commands = tuple(written for command in group.commands if (written := writing(command)) is not None)
    if all(command in PREPARES for command in commands):
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
    tools: tuple[str, ...]
    timeout: int  # minutes, which is the CI job's timeout-minutes
    when: tuple[str, ...]
    wall_seconds: int  # measured on the author's machine, and 0 where nobody has measured it yet
    env: tuple[tuple[str, str], ...] = ()  # what this group's commands need in the environment


REPORT_TIMING = "--timing=report"
"""What a leg whose compositor is not trustworthy passes to a suite that measures a cue.

A hosted macOS or Windows runner composites through a stack DeckTalk does not own, and a hosted
Linux runner that renders in software presents a frame tens of milliseconds after the paint it
answers. Either way the measurement moves and the deck did not, so these legs report a late reveal
and Linux stays the one that gates it. This weakens nothing else: `tests/support/timing_policy.py`
tolerates a late landing alone, and a cue that never changed the picture still fails every runner.
"""


def reports_timing(command: tuple[str, ...]) -> tuple[str, ...]:
    """The same command with cue timing reported, which only a suite has an opinion about.

    The flag is `tests/conftest.py`'s own option, so it is added to the suites and to nothing else.
    A tool that never collected a test would exit on an argument it has never heard of.
    """
    return (*command, REPORT_TIMING) if "pytest" in command else command


def elsewhere(group: Group) -> Group:
    """The same group on macOS and Windows, gating a merge and a release rather than a pull request.

    A group that drives a real tool is the only kind a second platform can fail on its own, and that
    happens a few times a year. Running all three on every push would make every change wait for
    three legs to buy one difference, so the Linux leg gates the change and this one gates the merge,
    which is still before a user meets it.

    These two runners are also the ones whose compositor is not trustworthy, so cue timing is
    reported here and gated on the Linux row of the same group.
    """
    return replace(
        group,
        name=f"{group.name}-platforms",
        why=f"{group.why} This row is macOS and Windows, which gate a merge rather than a pull request.",
        commands=tuple(reports_timing(command) for command in group.commands),
        runners=(MACOS, WINDOWS),
        when=("main", "release"),
    )


ON_A_REAL_TOOL: tuple[Group, ...] = (
    Group(
        name="browser",
        why="Everything that needs layout or a compositor, in the Chromium `decktalk install` fetches.",
        commands=((*UV, "pytest", "-q", "-m", "browser", *MEASURE),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("uv", "chromium"),
        timeout=25,
        when=("pr", "main", "release"),
        wall_seconds=52,
        env=measured("browser"),
    ),
    Group(
        name="media",
        why="Frame and audio measurement against the real ffmpeg, on synthetic files the tests build.",
        commands=((*UV, "pytest", "-q", "-m", "media", *MEASURE),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("uv", "ffmpeg"),
        timeout=25,
        when=("pr", "main", "release"),
        wall_seconds=9,
        env=measured("media"),
    ),
    Group(
        name="e2e",
        why="The pipeline fixture built end to end, which samples the joint behaviour of every tool.",
        commands=((*UV, "pytest", "-q", "-m", "e2e", *MEASURE),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("uv", "chromium", "ffmpeg"),
        timeout=30,
        when=("pr", "main", "release"),
        wall_seconds=117,
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
            NPM_CI,
            ("npm", "exec", "--no", "--", "biome", "ci", "."),
            ("uvx", "--from", f"shellcheck-py=={TOOLS['shellcheck']}", "shellcheck", "-s", "sh", "install.sh"),
            ("uvx", f"zizmor@{TOOLS['zizmor']}", ".github/workflows"),
        ),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("uv", "npm"),
        timeout=10,
        when=("pr", "main", "release"),
        wall_seconds=0,
    ),
    Group(
        name="unit",
        why="Every test that needs no tool, which the collection hook makes the default suite.",
        commands=((*UV, "pytest", "-q", *MEASURE),),
        runners=(LINUX,),
        pythons=EVERY_PYTHON,
        tools=("uv",),
        timeout=15,
        when=("pr", "main", "release"),
        wall_seconds=17,
        # The floor is one number over every suite, and this is the suite that reaches most of the
        # package, so a floor combined without it is a floor no complete run could meet.
        env=measured("unit"),
    ),
    Group(
        name="node",
        why="The runtime's pure functions and the release's next version, under node --test, with no framework.",
        commands=(NPM_CI, ("node", "--test", RUNTIME_TESTS, SCRIPT_TESTS)),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("npm",),
        timeout=10,
        when=("pr", "main", "release"),
        wall_seconds=0,
    ),
    *ON_A_REAL_TOOL,
    *(elsewhere(group) for group in ON_A_REAL_TOOL),
    Group(
        name="platform",
        why="The short list only macOS or Windows can prove, plus the two commands every machine runs.",
        commands=(
            (*UV, "pytest", "-q", "-m", "platform"),
            (*UV, "decktalk", "install"),
            (*UV, "decktalk", "doctor"),
        ),
        runners=EVERY_PLATFORM,
        pythons=(FLOOR,),
        tools=("uv", "chromium", "ffmpeg"),
        timeout=20,
        when=("pr", "main", "release"),
        wall_seconds=0,
    ),
    Group(
        name="generated",
        why="Every generated file held to the source it is generated from, and every link in them.",
        commands=(
            # The runtime bundles are compiled by the pinned TypeScript, so the group that judges
            # them installs it first, the way every other group that lists npm does.
            NPM_CI,
            # `build_assets.py` measures the hero's word widths in the real Chromium with the real
            # font, so this group needs a browser as much as the browser group does. The suites
            # fetch their own through `media/browser.py`, and a generator that launches Playwright
            # directly reaches nothing that would, so this row fetches it the way that module does.
            # Playwright resolves the revision from its own version and the call is a no-op on a
            # machine that already has it.
            FETCH_CHROMIUM,
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
        tools=("uv", "npm", "chromium"),
        timeout=20,
        when=("pr", "main", "release"),
        wall_seconds=0,
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
        commands=(NPM_CI, (*UV, "python", "scripts/rehearse_release.py")),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("uv", "npm", "chromium", "history"),
        timeout=20,
        when=("pr", "main", "release"),
        wall_seconds=15,
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
        tools=("uv",),
        timeout=10,
        when=("pr", "main", "release"),
        wall_seconds=0,
    ),
    Group(
        name="wheel",
        why="What `uv build` writes, opened on a machine that has only the wheel and the tag.",
        commands=(
            ("uv", "build"),
            (*UV, "pytest", "-q", "tests/contract/test_wheel.py"),
            (*UV, "scripts/check_wheel.py", "--check"),
        ),
        runners=EVERY_PLATFORM,
        pythons=(FLOOR,),
        tools=("uv",),
        timeout=15,
        when=("pr", "main", "release"),
        wall_seconds=0,
    ),
    Group(
        name="scaffold",
        why="Every packaged project recorded and verified without a voice, which is the scaffold's promise.",
        # This row records five projects in one job, so the runner renders in software throughout and
        # presents a reveal tens of milliseconds after the frame it belongs on. The promise being
        # judged is that a project out of the wheel builds and verifies, which the cue timing of the
        # machine it was built on is no part of, so this row reports a late landing and fails on
        # every other finding exactly as the gated rows do.
        commands=(reports_timing((*UV, "pytest", "-q", "-m", "scaffold")),),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("uv", "chromium", "ffmpeg"),
        timeout=30,
        when=("main", "schedule"),
        wall_seconds=0,
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
        tools=("docker",),
        timeout=25,
        when=("main", "schedule"),
        wall_seconds=0,
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
    return all(run(command, group.env) for command in group.commands)


def table() -> str:
    """Every group with its first command, what it needs, its measured wall time and the job that calls it."""
    rows = []
    for group in GROUPS:
        measured = f"about {group.wall_seconds}s" if group.wall_seconds else "not measured yet"
        more = f" and {len(group.commands) - 1} more" if len(group.commands) > 1 else ""
        rows.append(
            f"  {group.name}\n"
            f"      {shell(group.commands[0])}{more}\n"
            f"      needs {', '.join(group.tools)} on {', '.join(group.runners)}, {measured}, "
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


def main() -> int:
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
    parser.add_argument(
        WRITE,
        action="store_true",
        help="run each named group's generators with --write instead of --check, and its other checks not at all",
    )
    parser.add_argument("--list", action="store_true", help="print the table and run nothing")
    parser.add_argument("--json", action="store_true", help="print the matrix a workflow consumes, and run nothing")
    parser.add_argument(
        "--when",
        choices=("pr", "main", "release", "schedule"),
        help=f"the groups that gate at this moment, default {DEFAULT_WHEN}",
    )
    args = parser.parse_args()

    names = [name for value in args.group for name in value.split(",") if name]
    if args.fast:
        names = [*FAST, *names]
    if args.write and not names:
        parser.error("--write rewrites committed files, so it runs only the groups --group names.")
    groups = selected(names, args.when)

    # `--list` and `--json` are the same answer in two renderings, so asking for both is asking for
    # the listing a workflow reads rather than for the table and then nothing.
    if args.list or args.json:
        print(json.dumps(legs(groups)) if args.json else epilog())
        return 0

    if args.write:
        writers = tuple(writer(group) for group in groups)
        started = time.monotonic()
        if not all(run_group(group, f" {WRITE}") for group in writers):
            return 1
        count = f"{len(writers)} group" + ("s" if len(writers) != 1 else "")
        print(f"\nwrote every generated file of {count} in {time.monotonic() - started:.0f}s", flush=True)
        return 0

    started = time.monotonic()
    for group in groups:
        if not run_group(group):
            return 1
    count = f"{len(groups)} group" + ("s" if len(groups) != 1 else "")
    print(f"\n{count} passed in {time.monotonic() - started:.0f}s", flush=True)
    for group in GROUPS:
        if group not in groups:
            print(f"not run: {group.name:<10} {group.why}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
