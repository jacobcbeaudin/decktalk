# /// script
# requires-python = ">=3.12"
# ///
"""Every check DeckTalk runs, in one table, spelled once.

    uv run scripts/check.py                  # every group a pull request runs, in order
    uv run scripts/check.py --fast           # lint and unit alone, in a few seconds
    uv run scripts/check.py --group browser  # one group, by name, repeatable and comma-separated
    uv run scripts/check.py --list           # the table, for a person
    uv run scripts/check.py --json --when pr # the matrix, for a workflow

`GROUPS` below is the only place any check is written down. A workflow reads this table at runtime and
names no command of its own, so a workflow cannot disagree with it. There is no switch that skips a
check and no way to mark one advisory, because a knob that exists becomes permanent. A run that
selects fewer groups than the full set prints the rows it did not run and why, so a short run is never
mistaken for a complete one.

The first run may download headless Chromium and ffmpeg through `decktalk install`, once per machine.
No check needs an ElevenLabs key, and after `decktalk install` no check needs the network.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UV = ("uv", "run")
"""Every Python command runs in the project environment, so the lockfile decides what it runs."""

LINUX, MACOS, WINDOWS = "ubuntu-latest", "macos-latest", "windows-latest"
EVERY_PLATFORM = (LINUX, MACOS, WINDOWS)

FLOOR = "3.12"
"""The lowest Python DeckTalk supports, and the only one macOS and Windows carry."""

EVERY_PYTHON = (FLOOR, "3.13", "3.14")

TOOLS = {
    "ruff": "0.16.8",
    "biome": "2.5.13",
    "shellcheck": "0.11.0.1",
}
"""Every tool pinned outside the lockfile, and the version every other file must agree on.

`ruff` is the dev group's floor and the `ruff-pre-commit` rev, `biome` is `biome.json`'s `$schema`
and the `@biomejs/biome` entry in `package-lock.json`, and `shellcheck` is the `shellcheck-py` rev.
`tests/contract/test_checks.py` holds all four spellings equal.
"""

PYPI_DECKTALK = "https://pypi.org/pypi/decktalk/json"

# What `site/install.sh` has to survive: an image with nothing but curl on it. The installer's own
# promise is that a machine that has never had DeckTalk ends with `decktalk --version` printing one,
# so the whole check is that line, run in a shell the installer did not write.
INSTALL_IN_A_BARE_IMAGE = """
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq && apt-get install -y -qq curl >/dev/null
    else
      dnf install -y -q curl >/dev/null
    fi
    sh /site/install.sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    decktalk --version
"""

# Playwright publishes no musllinux wheels, so the resolver fails on musl whatever the installer does.
# The check is that the installer says so and stops before installing anything, not that it fails late.
REFUSE_MUSL = """
    apk add --no-cache curl >/dev/null
    set +e
    out="$(sh /site/install.sh 2>&1)"
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
    out="$(sh /site/install.sh)"
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
    sh /site/install.sh
    PATH="$HOME/.local/bin:$PATH"
    export PATH
    got="$(decktalk --version)"
    if [ "$got" != "decktalk $DECKTALK_VERSION" ]; then
      echo "pinned $DECKTALK_VERSION, installed $got"
      exit 1
    fi
"""


def in_image(image: str, script: str) -> tuple[str, ...]:
    """`script` run by POSIX sh inside `image`, with `site/` mounted read only and nothing else."""
    return ("docker", "run", "--rm", "-v", f"{ROOT / 'site'}:/site:ro", image, "sh", "-euc", script)


def generator(name: str) -> tuple[str, ...]:
    """A generated file held to its source. Every generator takes `--check` and `--write` alike."""
    return (*UV, f"scripts/{name}.py", "--check")


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


GROUPS: tuple[Group, ...] = (
    Group(
        name="lint",
        why="Style, types and shell held to one set of rules, so no review spends a comment on them.",
        commands=(
            ("uv", "lock", "--check"),
            (*UV, "ruff", "check", "src", "tests", "scripts"),
            (*UV, "ruff", "format", "--check", "src", "tests", "scripts"),
            (*UV, "ty", "check", "src"),
            ("npm", "ci"),
            ("npm", "exec", "--no", "--", "biome", "ci", "."),
            ("uvx", "--from", f"shellcheck-py=={TOOLS['shellcheck']}", "shellcheck", "-s", "sh", "site/install.sh"),
            ("uvx", "zizmor", ".github/workflows"),
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
        commands=((*UV, "pytest", "-q"),),
        runners=(LINUX,),
        pythons=EVERY_PYTHON,
        tools=("uv",),
        timeout=15,
        when=("pr", "main", "release"),
        wall_seconds=17,
    ),
    Group(
        name="node",
        why="The runtime's pure functions over strings, under node --test, so no test framework is added.",
        commands=(("npm", "ci"), ("node", "--test", "tests/decktalk/runtime/src/")),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("npm",),
        timeout=10,
        when=("pr", "main", "release"),
        wall_seconds=0,
    ),
    Group(
        name="browser",
        why="Everything that needs layout or a compositor, in the Chromium `decktalk install` fetches.",
        commands=((*UV, "pytest", "-q", "-m", "browser", "--cov", "--cov-report="),),
        runners=EVERY_PLATFORM,
        pythons=(FLOOR,),
        tools=("uv", "chromium"),
        timeout=25,
        when=("pr", "main", "release"),
        wall_seconds=52,
    ),
    Group(
        name="media",
        why="Frame and audio measurement against the real ffmpeg, on synthetic files the tests build.",
        commands=((*UV, "pytest", "-q", "-m", "media", "--cov", "--cov-report="),),
        runners=EVERY_PLATFORM,
        pythons=(FLOOR,),
        tools=("uv", "ffmpeg"),
        timeout=25,
        when=("pr", "main", "release"),
        wall_seconds=9,
    ),
    Group(
        name="e2e",
        why="The pipeline fixture built end to end, which samples the joint behaviour of every tool.",
        commands=((*UV, "pytest", "-q", "-m", "e2e", "--cov", "--cov-report="),),
        runners=EVERY_PLATFORM,
        pythons=(FLOOR,),
        tools=("uv", "chromium", "ffmpeg"),
        timeout=30,
        when=("pr", "main", "release"),
        wall_seconds=117,
    ),
    Group(
        name="platform",
        why="The short list only macOS or Windows can prove, plus the two commands every machine runs.",
        commands=(
            (*UV, "pytest", "-q", "tests/platform"),
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
            generator("build_homepage_data"),
            ("uv", "run", "--with", "fonttools[woff]>=4.50", "python", "scripts/build_assets.py", "--check"),
            generator("check_docs_links"),
        ),
        runners=(LINUX,),
        pythons=(FLOOR,),
        tools=("uv", "npm", "chromium"),
        timeout=20,
        when=("pr", "main", "release"),
        wall_seconds=0,
    ),
    Group(
        name="coverage",
        why="One floor, measured on Linux, failing when a suite it combines never reported.",
        commands=(
            (*UV, "coverage", "combine"),
            (*UV, "coverage", "report"),
            (*UV, "scripts/check_coverage.py", "--check"),
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
        commands=((*UV, "pytest", "-q", "-m", "scaffold"),),
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


def run(command: tuple[str, ...]) -> bool:
    print(f"\n$ {shell(command)}", flush=True)
    started = time.monotonic()
    # uv runs this script in an environment of its own, and a nested `uv run` would warn about it.
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    code = subprocess.call(command, cwd=ROOT, env=env)
    print(f"{'ok' if code == 0 else f'FAILED (exit {code})'} in {time.monotonic() - started:.1f}s", flush=True)
    return code == 0


def run_group(group: Group) -> bool:
    """Run one group, opening with the name and the one local command that reproduces it."""
    print(f"\n== {group.name}: uv run scripts/check.py --group {group.name}", flush=True)
    print(f"   {group.why}", flush=True)
    return all(run(command) for command in group.commands)


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
    groups = selected(names, args.when)

    if args.list:
        print(epilog())
        return 0
    if args.json:
        print(json.dumps(legs(groups)))
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
