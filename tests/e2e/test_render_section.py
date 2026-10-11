"""A section a program draws: the voice keeps going through it, and its cues are measured or said to be unmeasured.

    uv run pytest -m e2e tests/e2e/test_render_section.py

The motion example's third section names a `render_command`, a short script that composes an ffmpeg
filter graph from the motion document `record` writes. This builds the example with no spend, once
as it ships, where the script writes its landings and every bar's departure is measured on its word,
and once with the landings left out, where verify measures none of the section's cues and says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from decktalk.results import SkipReason
from support.commands import HOSTILE_DIRECTORY, RUNS_PROGRAMS, run, written
from support.timing_policy import EVERY_PACKAGED_PROJECT_SECONDS, FIRST_FETCH_SECONDS, budget

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(budget(EVERY_PACKAGED_PROJECT_SECONDS + FIRST_FETCH_SECONDS))]

EXAMPLE = "motion"
RENDERED = 3
"""The section of the motion example a program draws."""


def decktalk(*args: str, cwd: Path) -> tuple[dict[str, Any], str]:
    """One command under `--json` with its events on stderr, and both."""
    ran = run(*args, "--json", "--events", cwd=cwd)
    assert ran.stdout.strip(), ran.stderr
    return json.loads(ran.stdout), ran.stderr


def built(tmp_path: Path, *, landings: bool) -> tuple[Path, dict[str, Any], str]:
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = written(home, EXAMPLE)
    if not landings:
        toml = root / "decktalk.toml"
        toml.write_text(
            # A program runs in a scratch folder, so the file it is sent its landings to is named in full.
            toml.read_text(encoding="utf-8").replace('"{landings}"', json.dumps(str(tmp_path / "kept.json"))),
            encoding="utf-8",
        )
    decktalk("--project", str(root), "build", "--no-spend", *RUNS_PROGRAMS, cwd=home)
    verified, said = decktalk("--project", str(root), "verify", cwd=home)
    return root, verified, said


def test_the_command_is_handed_the_section_s_words_and_cues_and_its_landings_are_measured(tmp_path: Path) -> None:
    root, verified, _said = built(tmp_path, landings=True)
    document = json.loads((root / "build" / "recordings" / "render" / "03" / "motion.json").read_text())
    assert set(document["cues"]) == {"3.1:one", "3.1:two", "3.1:three"}
    assert [word["word"] for word in document["words"]][:3] == ["This", "picture", "is"]
    departures = [row for row in verified["motions"] if row["section"] == RENDERED]
    assert [row["cue"] for row in departures] == ["3.1:one", "3.1:two", "3.1:three"]
    assert all(row["offset_seconds"] is not None for row in departures)
    placements = json.loads((root / "build" / "final" / "placements.json").read_text())
    starts = {row["section"]: row for row in placements["sections"]}
    assert starts[RENDERED]["start"] == pytest.approx(starts[RENDERED - 1]["end"], abs=0.05)


def test_a_command_that_writes_no_landings_leaves_its_cues_unmeasured_and_says_so(tmp_path: Path) -> None:
    _root, verified, said = built(tmp_path, landings=False)
    rows = [row for row in verified["cues"] if row["section"] == RENDERED]
    assert rows and all(row["skipped"] == SkipReason.RENDERED.value for row in rows)
    assert "wrote no landings" in said
