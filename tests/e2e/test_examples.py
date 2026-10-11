"""Every shipped example, recorded by the stepped recorder, verifies clean with every cue on its own frame.

    uv run pytest -m e2e tests/e2e/test_examples.py

The recorder steps the section clock one frame at a time, so a reveal shows on the first frame at or
after its cue whatever the machine was doing. That is a promise a real-time capture could never make,
and this is the test that holds it: no finding but the placeholder's own, and every cue and every
landing read on the frame it was due on, or on the next one for an entrance that had barely begun
on its first, as `ENTRANCE_READ_FRAMES` says.
Nothing here needs a key, a network or a voice, so every section plays a placeholder and says so.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from decktalk.findings import Code
from decktalk.results import SkipReason
from decktalk.settings import Settings
from decktalk.template import EXAMPLES
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import (
    ENTRANCE_READ_FRAMES,
    EVERY_PACKAGED_PROJECT_SECONDS,
    FIRST_FETCH_SECONDS,
    budget,
    due_seconds,
    frames_late,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS))]

PLACEHOLDER = {Code.TAKE_MISSING.value}
"""What a build with no spend reports on every section, which is news about the voice and not about the picture."""

SHIPPED = [example.name for example in EXAMPLES if example.shipped]
"""Every example `decktalk init --example` writes today."""


def decktalk(*args: str, cwd: Path) -> dict[str, Any]:
    """One `decktalk` command under `--json`, with no key, no voice variable and no machine file in reach."""
    ran = subprocess.run(
        [sys.executable, "-m", "decktalk", *args, "--json"],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environ(cwd),
        cwd=cwd,
    )
    assert ran.stdout.strip(), ran.stderr
    return json.loads(ran.stdout)


@pytest.mark.parametrize("example", SHIPPED)
def test_a_shipped_example_verifies_clean_with_every_cue_on_its_frame(tmp_path: Path, example: str) -> None:
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = home / example
    decktalk("init", str(root), "--example", example, "--no-skills", cwd=home)
    built = decktalk("--project", str(root), "build", "--no-spend", cwd=home)
    assert {row["code"] for row in built["findings"]} <= PLACEHOLDER, built["findings"]
    verified = decktalk("--project", str(root), "verify", cwd=home)
    assert {row["code"] for row in verified["findings"]} <= PLACEHOLDER, verified["findings"]
    elsewhere = {SkipReason.OPTED_OUT.value, SkipReason.MOTION_LANDING.value}
    asked = [row for row in verified["cues"] if row["skipped"] not in elsewhere]
    measured = [row for row in asked if row["offset_seconds"] is not None]
    assert measured == asked, "every cue of a shipped example that asks to be measured is measured"
    # A motion's own landings are measured on its lane, and they are held to the same frame.
    landed = [row for row in verified["motions"] if row["offset_seconds"] is not None]
    assert len(landed) == len(verified["motions"]), verified["motions"]
    due = due_seconds(root / "build")
    fps = Settings().video.fps

    def due_at(row: dict[str, Any]) -> tuple[float, float]:
        # A landing inside a pane clip lands on the clip's own cue, due at the film second its row says.
        return (0.0, row["spoken"]) if row.get("clip") else due[(row["section"], row["cue"])]

    late = {
        f"{row['section']}:{row.get('clip') or ''}{row['cue']}:{row.get('kind', 'reveal')}": frames_late(
            row["shown"], *due_at(row), fps
        )
        for row in [*measured, *landed]
    }
    assert {cue: frames for cue, frames in late.items() if frames not in ENTRANCE_READ_FRAMES} == {}, late
