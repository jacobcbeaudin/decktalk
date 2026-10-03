"""The tools a suite needs beyond Python, and the failure a run gets when this machine lacks one.

A suite that needs a tool is only collected when a run names its marker, so a run that reaches one
of these checks asked for that suite by name. Skipping it then would report a green run that proved
nothing, which is how the browser and e2e rows passed in CI with every test skipped. So a missing
tool fails, and the failure names the one command that fetches it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

INSTALLED = ("browser", "media", "e2e", "scaffold", "platform")
"""The markers of the suites that need what `decktalk install` fetches.

`platform` is the machine itself rather than a tool: those tests assert what this filesystem and
this fetched toolchain really do, so a runner that has fetched nothing would fail them and the
default suite may not collect them.
"""

BUILT = ("wheel",)
"""The marker of the suite that reads the wheel `uv build` wrote into `dist/`, which no fetch provides."""

SUITE_MARKERS = (*INSTALLED, *BUILT)
"""The markers that name what a test needs beyond Python. Every one is registered in `pyproject.toml`.

`tests/conftest.py` selects by these and nothing else is one.
"""

MARKED_BY_PATH = {"contract/test_wheel.py": "wheel"}
"""The suite marker of each test file that carries none of its own, keyed by its path under `tests/`.

The wheel test reads a file only `uv build` makes, so the collection hook marks it by where it is
and a fresh clone's bare run never collects a test of a wheel it has not built.
"""

FETCHED = ("browser", "media", "platform")
"""The markers of the tests that run a fetched tool outside a run, so `tests/conftest.py` binds one for each.

A browser test needs the binding too, because the driver looks for Chromium in the machine's tool cache.
"""

FETCH = "uv run decktalk install"
"""The command that fetches every tool a suite needs, which is the command a person runs."""


def absent(tool: str, reason: str) -> str:
    """The sentence a run fails with when `tool` is not usable here, naming why and what fetches it."""
    return f"{tool} is not usable on this machine ({reason}), and this run named the suite that needs it. Run {FETCH}."


def missing(tools: tuple[str, ...], cwd: Path) -> list[str]:
    """Every tool in `tools` this machine does not hold, asked of DeckTalk through its own doctor command.

    A tool is held when `doctor` reports its version. The browser has no path to report, because it is
    found by launching it rather than by looking for a file, so a path is never what decides.
    """
    done = subprocess.run(
        [sys.executable, "-m", "decktalk", "doctor", "--json"], capture_output=True, text=True, check=False, cwd=cwd
    )
    held = {row["tool"]: row for row in json.loads(done.stdout)["tools"]}
    return [tool for tool in tools if not (held.get(tool) or {}).get("version")]


def require(tools: tuple[str, ...], cwd: Path) -> None:
    """Fail the run when this machine lacks any of `tools`, naming each one and the command that fetches it."""
    if lacking := missing(tools, cwd):
        pytest.fail(absent(", ".join(lacking), "doctor reports no version"))


@contextmanager
def machine_tools() -> Iterator[None]:
    """Bind this process's machine's cache and tools, as a run does.

    A call to ffmpeg or a Chromium launch outside a run has no machine to say where the fetched build
    is kept, so a test, or a fixture that runs the real tool, binds the toolchain of the machine this
    process would build.
    """
    from decktalk.machine import Machine  # noqa: PLC0415  (a bare run that fetches nothing never loads it)

    with Machine.from_environment().toolchain.bound():
        yield
