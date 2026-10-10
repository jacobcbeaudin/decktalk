"""A shipped section recorded again and again decodes to the same frames every time.

    uv run pytest -m scaffold tests/e2e/test_same_frames.py

The stepped recorder promises that two recordings of one page are the same frames, and every later
speed-up of the recorder is judged by that promise. The starter's closing section is the page that
broke it: its end card pops in, and a frame captured before the page had drawn the step showed the
card already settled on one run in three. So the section is recorded several times on a fresh
project and every decoded frame of every run is compared, as rgb24, with the first.

It builds a packaged project with no voice, so it carries the `scaffold` marker with the other suites
that do.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from decktalk.media import ffmpeg
from decktalk.settings import Settings
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import FIRST_FETCH_SECONDS, budget
from support.tools import machine_tools

RECORDINGS = 5
"""How many times the section is recorded.

A frame captured before the page had drawn it disagreed in about one recording in three on a loaded
machine, so five recordings would all agree by chance about one time in six.
"""

SECTION = 3
"""The starter's closing section, whose end card pops in."""

RECORDING_SECONDS = 60
"""A ceiling on one build or one recording of the section, so a hung page fails as a timeout."""

pytestmark = [pytest.mark.scaffold, pytest.mark.timeout(budget(FIRST_FETCH_SECONDS + RECORDINGS * RECORDING_SECONDS))]


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


def frame_digests(video: Path) -> list[str]:
    """One digest per decoded rgb24 frame of `video`, at the size the project records at."""
    video_settings = Settings().video
    size = video_settings.width * video_settings.height * 3
    with machine_tools():
        raw = ffmpeg.raw(*ffmpeg.source(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-")
    return [hashlib.sha256(raw[at : at + size]).hexdigest() for at in range(0, len(raw), size)]


def test_the_starters_closing_section_records_the_same_frames_every_time(tmp_path: Path) -> None:
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = home / "starter"
    decktalk("init", str(root), "--no-skills", cwd=home)
    decktalk("--project", str(root), "build", "--no-spend", cwd=home)
    video = root / "build" / "recordings" / f"{SECTION:02d}.mp4"
    runs = [frame_digests(video)]
    for _ in range(RECORDINGS - 1):
        decktalk("--project", str(root), "record", "--section", str(SECTION), "--force", cwd=home)
        runs.append(frame_digests(video))
    differing = [[k for k, (a, b) in enumerate(zip(runs[0], run, strict=True)) if a != b] for run in runs[1:]]
    assert differing == [[]] * (RECORDINGS - 1)
