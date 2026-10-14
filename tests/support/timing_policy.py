"""Which frame a reveal shows on, which is its own on every runner, and how long a slow runner may take.

A number typed into each test that needs it would be a magic number inside the suite that enforces
the no-magic-numbers rule. So the numbers live here, each derived from the project's own settings or
written once with the sentence that says why.

**A reveal is on its frame or the test fails.** The recorder steps the section clock one frame at a
time and draws frame `k` as the page at second `k / fps`, so a cue shows on the first frame at or
after its second whatever the machine was doing. `frames_late` says how far a measured reveal sits
from that frame, and every suite that reads a landing asks for zero. There is no option that
loosens it and no leg of `GROUPS` that is held to less, because a runner's speed moves how long a
recording takes and never which frame a picture is on.

**A budget is a ceiling and never a measurement.** A module timeout is a base budget times a factor
for this platform, because a hundred and eighty seconds is generous on Linux and tight on a cold
Windows runner. This module is one of the two places the suite may read `sys.platform`, which is
what `tests/contract/test_layout.py` allows and what keeps that read out of every other file.
"""

from __future__ import annotations

import math
import sys
from fractions import Fraction
from pathlib import Path

from decktalk.artifacts import CueTimes, Placements
from decktalk.media.frames import frame_index
from decktalk.pipeline import Artifact

BASE_BUDGET_SECONDS = 180
"""How long a module that drives a real build may take on a warm Linux runner, as a ceiling."""

FIRST_FETCH_SECONDS = 420
"""What the first build on a machine adds while it downloads Chromium and ffmpeg, once per machine."""

EVERY_PACKAGED_PROJECT_SECONDS = 720
"""What a run of every project `decktalk init` writes takes, which is several builds one after another."""

PLATFORM_FACTOR = {"linux": 1.0, "darwin": 2.0, "win32": 3.0}
"""How much longer the same work takes on each hosted runner, measured as a ceiling and never a target.

A cold Windows runner unpacks an archive, starts a browser and encodes on a slower disk, so the same
module needs three times the ceiling. Nothing is asserted about these numbers. They exist so that a
hung page fails as a timeout rather than as a six hour job.
"""


ENTRANCE_READ_FRAMES = frozenset((0, 1))
"""How many frames after its due one verify may read a reveal's onset on, which is that frame or the next.

The reveal shows on its due frame on every runner. An entrance that eases in has moved for less than
one frame by then, and on the frame where it has moved less than verify's onset rise the change is
not yet an onset, so verify reads it one frame later. That is where the measurement reads a frame,
not where the frame is, so an instant reveal is held to its due frame alone and a reveal two frames
out is a failure everywhere.
"""


def budget(base: float = BASE_BUDGET_SECONDS) -> float:
    """How long a module may take on this platform, which is the base budget times this runner's factor."""
    return base * PLATFORM_FACTOR.get(sys.platform, max(PLATFORM_FACTOR.values()))


def frames_late(shown: float, section_start: float, cue_seconds: float, fps: int) -> int:
    """How many frames after the one it was due on a reveal first showed, which is zero on every runner.

    `shown` is the film's second of the frame the reveal first changed, `section_start` the film's
    second the section's clock starts at, and `cue_seconds` the cue's second on that clock. The
    frame a cue is due on is the first one at or after its second, which is the same rule the
    film's readers name a frame by. A negative answer is a reveal that showed before its frame.
    """
    due = frame_index(cue_seconds, Fraction(fps), math.inf)
    return round((shown - section_start) * fps) - due


def due_seconds(build: Path) -> dict[tuple[int, str], tuple[float, float]]:
    """Each resolved cue's section start in the film and its second on the section clock, by section and cue id.

    These are the two seconds `frames_late` needs besides the reveal's own, read from what the build
    wrote: the cue times the page was stepped against and the placements the film was cut from.
    """
    cue_times = CueTimes.read(build / Artifact.CUE_TIMES.value)
    placements = Placements.read(build / Artifact.FINAL.value / "placements.json")
    assert cue_times is not None and placements is not None, "a finished build writes its cue times and placements"
    starts = {row.key: row.start for row in placements.sections}
    return {
        (row.section, cue.id): (starts[row.key], cue.seconds)
        for row in cue_times.sections
        for cue in row.cues
        if cue.seconds is not None
    }
