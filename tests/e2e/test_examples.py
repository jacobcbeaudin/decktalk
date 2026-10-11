"""Every packaged project, recorded by the stepped recorder, verifies clean with every cue on its own frame.

    uv run pytest -m e2e tests/e2e/test_examples.py

This is the promise the packaged projects make: a fresh project builds on the machine it was written
on, with no API key, no spend and no file of the author's, and the starter and each shipped example
are built here on every pull request. `tests/decktalk/template/test_template.py` judges the same
projects as data without building them.

The recorder steps the section clock one frame at a time, so a reveal shows on the first frame at or
after its cue whatever the machine was doing. That is a promise a real-time capture could never make,
and this is the test that holds it: no finding but the placeholder's own, nothing the runtime could
not honour and no other origin reached on any page, and every cue and every landing read on the
frame it was due on, or on the next one for an entrance that had barely begun on its first, as
`ENTRANCE_READ_FRAMES` says. The starter is the page every author copies, so verify finds nothing
in it at all. Nothing here needs a key, a network or a voice, so every section plays a placeholder
and says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from decktalk.artifacts import RecordingLog
from decktalk.findings import Code
from decktalk.results import SkipReason
from decktalk.settings import Settings
from decktalk.template import STARTER
from support.commands import HOSTILE_DIRECTORY, PACKAGED, RUNS_PROGRAMS, decktalk, written
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


@pytest.mark.parametrize("example", PACKAGED)
def test_a_packaged_project_verifies_clean_with_every_cue_on_its_frame(tmp_path: Path, example: str) -> None:
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = written(home, example)
    built = decktalk("--project", str(root), "build", "--no-spend", *RUNS_PROGRAMS, cwd=home)
    assert {row["code"] for row in built["findings"]} <= PLACEHOLDER, built["findings"]
    for log_path in sorted((root / "build" / "recordings").glob("*.json")):
        log = RecordingLog.read(log_path)
        assert log is not None, log_path
        assert list(log.findings) == [], (log_path.name, log.findings)
        assert list(log.recording.external) == [], (log_path.name, log.recording.external)
    verified = decktalk("--project", str(root), "verify", cwd=home)
    assert {row["code"] for row in verified["findings"]} <= PLACEHOLDER, verified["findings"]
    if example == STARTER:
        assert verified["findings"] == [], verified["findings"]
    elsewhere = {SkipReason.OPTED_OUT.value, SkipReason.MOTION_LANDING.value}
    asked = [row for row in verified["cues"] if row["skipped"] not in elsewhere]
    measured = [row for row in asked if row["offset_seconds"] is not None]
    assert measured == asked, "every cue of a packaged project that asks to be measured is measured"
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
