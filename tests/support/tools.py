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
from pathlib import Path

import pytest

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
