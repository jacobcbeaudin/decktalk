"""When a late frame is a failure and when it is only news, and how long a slow runner may take.

Three numbers used to be typed into the tests that needed them, and each one was a magic number
inside the suite that enforces the no-magic-numbers rule. They live here instead, each derived from
the project's own settings or written once with the sentence that says why.

**The timing default inverts.** It used to read `sys.platform`, which is the wrong predicate: the
property is that this runner's compositor is not trustworthy, not that this is macOS, so the
founder's own Mac was permanently weaker than a Linux runner. `--timing=gate` is the default
everywhere now, and the one workflow step that owns a weak runner passes `--timing=report` itself,
so the weakening lives in the file that owns it rather than in every test that measures a cue.

**A budget is a ceiling and never a measurement.** A module timeout is a base budget times a factor
for this platform, because a hundred and eighty seconds is generous on Linux and tight on a cold
Windows runner. This module is one of the two places the suite may read `sys.platform`, which is
what `tests/contract/test_layout.py` allows and what keeps that read out of every other file.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

import pytest

from decktalk.findings import Certainty, Code
from decktalk.page import FRAME_STEP_MS

LATE_FRAME = (Code.CUE_OFF,)
"""What a runner that presents frames late produces, and the only code an ungated platform tolerates."""

UNGATED_EXTRA_FRAMES = 2
"""How many extra frames of slack a runner whose compositor is not trustworthy is given.

Two frames is what the recorded failures needed: every one of them was a reveal under a hundred
milliseconds late, and the capture runs at twenty five frames a second. It is written once here and
added to the project's own limit, because the `4` and the `5` this replaces were the settings plus
two, spelled as literals inside the suite that is supposed to enforce the rule.
"""

ROUNDING_SLACK_FRAMES = 0.5
"""Half a frame, which is the most a measured time may pass a frame boundary by and still name it."""

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


def budget(base: float = BASE_BUDGET_SECONDS) -> float:
    """How long a module may take on this platform, which is the base budget times this runner's factor."""
    return base * PLATFORM_FACTOR.get(sys.platform, max(PLATFORM_FACTOR.values()))


def gates_timing(config: pytest.Config) -> bool:
    """Whether a late reveal fails here, which is everywhere unless this run asked for a report.

    The option is read defensively because a run that does not register it is a run with the default
    in force, and a missing option may never quietly turn the gate off.
    """
    try:
        return str(config.getoption("--timing", default="gate")) != "report"
    except ValueError:
        return True


def offset_limit_ms(stated_ms: float, gate: bool) -> float:
    """The offset a cue may miss its word by here, which is the project's own limit plus the slack.

    The slack is added rather than typed, so a project that widens its own limit widens this one too
    and the two can never disagree about what late means.
    """
    if gate:
        return stated_ms
    return stated_ms + UNGATED_EXTRA_FRAMES * FRAME_STEP_MS


def tolerated(code: int, codes: Iterable[Code], gate: bool) -> str | None:
    """None when a build finished acceptably, else the one sentence saying why it did not.

    A build exits 0 when it found nothing certain. Where timing is not gated, a build that exited on
    late reveals alone is acceptable too. Every other fault fails on every platform, and so does a
    late reveal wherever timing is gated.
    """
    if code == 0:
        return None
    faults = [found for found in codes if found.certainty is Certainty.CERTAIN]
    if gate:
        return f"the build exited {code} and timing is gated here: {[found.name for found in faults] or 'no row'}"
    if others := [found for found in faults if found not in LATE_FRAME]:
        return f"the build exited {code} on more than late reveals: {[found.name for found in others]}"
    if not faults:
        return f"the build exited {code} with no finding row to explain it"
    return None


def assert_build_finished(code: int, codes: Iterable[Code], detail: str, config: pytest.Config) -> None:
    """Assert a build finished acceptably on this runner, and say what it tolerated when it did."""
    rows = list(codes)
    why = tolerated(code, rows, gates_timing(config))
    assert why is None, f"{why}\n{detail}"
