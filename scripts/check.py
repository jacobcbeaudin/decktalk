# /// script
# requires-python = ">=3.12"
# ///
"""Every check a pull request must pass, in one command.

    uv run scripts/check.py           # lint, types, every test suite but the scaffold build, the generated files
    uv run scripts/check.py --fast    # lint, types and the unit tests alone, in a few seconds

The first run may download headless Chromium, ffmpeg and KaTeX through `decktalk setup`, once per machine.
No check needs an ElevenLabs key, and after `decktalk setup` no check needs the network. Each step prints
its command and its time, and the script exits 1 after the first step that fails.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UV = ["uv", "run"]

LINT: list[list[str]] = [
    ["uv", "lock", "--check"],
    [*UV, "ruff", "check", "src", "tests", "scripts"],
    [*UV, "ruff", "format", "--check", "src", "tests", "scripts"],
    [*UV, "ty", "check", "src"],
    ["npx", "--yes", "@biomejs/biome@2.5.13", "ci", "."],
]
UNIT: list[list[str]] = [[*UV, "pytest", "-q"]]
FULL: list[list[str]] = [
    [*UV, "decktalk", "setup"],
    # Every suite but the scaffold build. The unit tests run here too, so coverage counts the whole net.
    [*UV, "pytest", "-q", "-m", "not scaffold", "--cov", "--cov-report=term", "--durations=10"],
    [*UV, "scripts/build_config_reference.py", "--check"],
    [*UV, "scripts/build_changelog.py", "--check"],
    # In the project environment, so the check draws with the Chromium that `decktalk setup` installed rather
    # than with whatever playwright the script's own header would resolve.
    [*UV, "--with", "fonttools[woff]>=4.50", "python", "scripts/build_assets.py", "--check"],
]


def run(cmd: list[str]) -> bool:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    started = time.monotonic()
    # uv runs this script in an environment of its own, and a nested `uv run` would warn about it.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    code = subprocess.call(cmd, cwd=ROOT, env=env, shell=sys.platform == "win32" and cmd[0] == "npx")
    print(f"{'ok' if code == 0 else f'FAILED (exit {code})'} in {time.monotonic() - started:.1f}s", flush=True)
    return code == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true", help="lint, types and the unit tests alone")
    args = ap.parse_args()
    steps = LINT + (UNIT if args.fast else FULL)
    started = time.monotonic()
    for step in steps:
        if not run(step):
            return 1
    print(f"\nall checks passed in {time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
