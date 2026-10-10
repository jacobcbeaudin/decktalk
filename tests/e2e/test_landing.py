"""The v3 p1 prototype, the waveform landing, as a real DeckTalk page that verifies with no finding.

    uv run pytest -m e2e tests/e2e/test_landing.py

The prototype drew a voice line that scrolls under a fixed playhead at the speed of speech, with
each picture landing on its word. Here the voice line is a frame hook drawn from the section second
on a canvas the page declares as its region, the envelope is the real take's, and the landings are
the page's own reveals on the cues the placeholder voice puts on their words. The scroll never
stops, so without the declared region every reveal would read early, and with it verify masks the
voice line and measures each landing on its frame.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from decktalk.findings import Code
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import BASE_BUDGET_SECONDS, FIRST_FETCH_SECONDS, budget

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(BASE_BUDGET_SECONDS + FIRST_FETCH_SECONDS))]

FIXTURE = Path(__file__).resolve().parent / "landing"
"""The ported prototype: its page, the real take's envelope, one frame of the real film, and its cues."""

PLACEHOLDER = {Code.TAKE_MISSING.value}
"""What a build with no spend reports, which is news about the voice and not about the picture."""


def decktalk(*args: str, cwd: Path) -> dict[str, Any]:
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


def test_the_waveform_landing_verifies_with_no_finding_and_its_voice_line_masked(tmp_path: Path) -> None:
    home = tmp_path / HOSTILE_DIRECTORY
    root = home / "landing"
    shutil.copytree(FIXTURE, root)
    built = decktalk("--project", str(root), "build", "--no-spend", cwd=home)
    assert {row["code"] for row in built["findings"]} <= PLACEHOLDER, built["findings"]
    verified = decktalk("--project", str(root), "verify", cwd=home)
    assert {row["code"] for row in verified["findings"]} <= PLACEHOLDER, verified["findings"]
    cues = {row["cue"]: row for row in verified["cues"]}
    assert set(cues) == {"1.1:click", "1.1:client"}
    assert all(row["offset_seconds"] is not None for row in cues.values())
    assert all(row["masked_percent"] for row in cues.values()), "the voice line was masked out of every landing"
    log = json.loads((root / "build" / "recordings" / "01.json").read_text(encoding="utf-8"))
    (lane,) = log["recording"]["report"]["motions"]
    assert lane["kind"] == "region" and lane["box"]["y"] == 760
