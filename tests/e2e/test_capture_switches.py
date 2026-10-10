"""Every shipped page section records the same frames with the capture switches as without them.

    uv run pytest -m scaffold tests/e2e/test_capture_switches.py

`[record] capture_switches` launches the recorder's Chromium with switches that stop its compositor
waiting on a display refresh, which halves the time a frame takes. The switches are a speed-up and
never a different picture, and this is the test that holds them to it: every page section of every
packaged project is recorded twice without the switches and twice with them, and every decoded rgb24
frame of every recording must equal the frame of the first. The equivalence is exact. No frame, and
no pixel of a frame, may differ, so the two recordings without the switches also show that each mode
alone records the same frames every time.

It builds every packaged project with no voice, so it carries the `scaffold` marker with the other
suites that do.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from decktalk.inputs import Inputs
from decktalk.media import ffmpeg
from decktalk.settings import CAPTURE_SWITCHES, Settings
from decktalk.template import EXAMPLES
from support.commands import HOSTILE_DIRECTORY, clean_environ
from support.timing_policy import BASE_BUDGET_SECONDS, FIRST_FETCH_SECONDS, budget
from support.tools import machine_tools

RECORDINGS_PER_MODE = 2
"""How many times each section is recorded in each mode after the build's own recording without the switches.

Two in each mode, taken in turn, show each mode alone records the same frames twice and that neither
mode's frames depend on which one ran before it.
"""

pytestmark = [
    pytest.mark.scaffold,
    pytest.mark.timeout(budget(FIRST_FETCH_SECONDS + (1 + 2 * RECORDINGS_PER_MODE) * BASE_BUDGET_SECONDS)),
]

PROJECTS = ["", *(example.name for example in EXAMPLES if example.shipped)]
"""Every project `decktalk init` writes: the starter, named by no example, and each shipped example."""

MODES = {"without": "", "with": ",".join(CAPTURE_SWITCHES)}
"""The two ways the recorder's Chromium is launched, as the `--set` value that asks for each."""


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
    said = json.loads(ran.stdout)
    assert said["ok"], said
    return said


def frame_digests(video: Path) -> list[str]:
    """One digest per decoded rgb24 frame of `video`, decoded as a stream so a long section is never held whole."""
    video_settings = Settings().video
    size = video_settings.width * video_settings.height * 3
    digests: list[str] = []
    pending = bytearray()

    def taken(chunk: bytes) -> None:
        pending.extend(chunk)
        while len(pending) >= size:
            digests.append(hashlib.sha256(pending[:size]).hexdigest())
            del pending[:size]

    sink: Callable[[bytes], None] = taken
    with machine_tools():
        ffmpeg.stream(*ffmpeg.source(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-", into=sink)
    assert not pending, f"{video.name} decoded to a part of a frame"
    return digests


@pytest.mark.parametrize("example", PROJECTS, ids=[name or "starter" for name in PROJECTS])
def test_the_capture_switches_record_the_same_frames_on_every_shipped_page_section(
    tmp_path: Path, example: str
) -> None:
    home = tmp_path / HOSTILE_DIRECTORY
    home.mkdir(parents=True)
    root = home / (example or "starter")
    decktalk("init", str(root), *(("--example", example) if example else ()), "--no-skills", cwd=home)
    off = f"record.capture_switches={MODES['without']}"
    decktalk("--project", str(root), "build", "--set", off, "--no-spend", cwd=home)
    drawn = [section.number for section in Inputs.load(root, environ={}).document.drawn_sections]
    assert drawn, "every packaged project has a page section"
    for number in drawn:
        video = root / "build" / "recordings" / f"{number:02d}.mp4"
        first = frame_digests(video)
        runs: dict[str, list[list[str]]] = {mode: [] for mode in MODES}
        for _ in range(RECORDINGS_PER_MODE):
            for mode, switches in MODES.items():
                setting = f"record.capture_switches={switches}"
                section = ("--section", str(number), "--force")
                decktalk("--project", str(root), "record", "--set", setting, *section, cwd=home)
                runs[mode].append(frame_digests(video))
        differing = {
            mode: [[k for k, (a, b) in enumerate(zip(first, run, strict=True)) if a != b] for run in recorded]
            for mode, recorded in runs.items()
        }
        assert differing == {mode: [[]] * RECORDINGS_PER_MODE for mode in MODES}, f"section {number}"
