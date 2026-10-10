"""Where a reveal lands, which is its own frame on every runner, and what a budget is.

`tests/support/timing_policy.py` holds the rule every suite that drives a real build follows. The
recorder steps the section clock one frame at a time, so the frame a reveal shows on is a function
of its cue's second alone and a runner's speed cannot move it. A test that reads a reveal a frame
late is reading a broken deck or a broken recorder, never a slow machine.

The first section holds the frame rule to each case without a build. The second holds the other
half of it, which is that no leg of `GROUPS` and no option of the suite can loosen it. The last
section holds the budget, which is the one allowance a slow runner still gets, because it is about
how long a run takes and nothing about where a frame lands.
"""

from __future__ import annotations

import pytest

import check
from support.timing_policy import BASE_BUDGET_SECONDS, ENTRANCE_READ_FRAMES, PLATFORM_FACTOR, budget, frames_late

FPS = 25
"""The rate every case is written at, which is the rate a film is recorded at by default."""


# ---- the frame a reveal shows on ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shown", "start", "cue", "late"),
    [
        # A cue on a frame's own second shows on that frame.
        pytest.param(1.04, 0.0, 1.04, 0, id="a cue on a frame's second"),
        # A cue between two frames shows on the first frame after it.
        pytest.param(2.68, 0.0, 2.66, 0, id="a cue between two frames"),
        # The section clock starts at the section's first frame in the film.
        pytest.param(8.72, 6.6, 2.12, 0, id="a section that starts later in the film"),
        pytest.param(17.6, 15.68, 1.91, 0, id="a cue between two frames of a later section"),
    ],
)
def test_a_reveal_on_the_first_frame_at_or_after_its_cue_is_on_its_frame(
    shown: float, start: float, cue: float, late: int
) -> None:
    assert frames_late(shown, start, cue, FPS) == late


def test_a_reveal_one_frame_late_is_one_frame_late() -> None:
    """One frame is the whole difference a test may see, so one frame is a failure and not a rounding."""
    assert frames_late(2.72, 0.0, 2.66, FPS) == 1


def test_a_reveal_before_its_frame_is_early_rather_than_on_time() -> None:
    """A reveal that shows on the frame before its cue is as wrong as one a frame after it."""
    assert frames_late(2.64, 0.0, 2.66, FPS) == -1


def test_verify_may_read_an_entrance_on_its_frame_or_the_next_and_on_no_other() -> None:
    """An eased entrance can sit under the onset rise on its first frame, and on no frame before or after that."""
    assert ENTRANCE_READ_FRAMES == {0, 1}


# ---- nothing loosens the rule ----------------------------------------------------------------------


def test_no_row_of_the_table_passes_a_timing_option() -> None:
    """A leg that reported a late reveal instead of failing on it would hold a deck to less than its frame."""
    loosened = [
        f"{group.name}: {' '.join(command)}"
        for group in check.GROUPS
        for command in group.commands
        if any(part.startswith("--timing") for part in command)
    ]
    assert loosened == []


def test_the_suite_registers_no_timing_option(pytestconfig: pytest.Config) -> None:
    """There is one way a suite reads a frame, so there is no option that chooses another."""
    with pytest.raises(ValueError):
        pytestconfig.getoption("--timing")


# ---- the budget, which is about speed and never about frames ---------------------------------------


def test_the_budget_is_a_ceiling_that_grows_with_the_runner() -> None:
    """A budget catches a hung page. It is generous on Linux and has to be more generous elsewhere."""
    assert budget() >= BASE_BUDGET_SECONDS
    assert min(PLATFORM_FACTOR.values()) == 1.0
    assert budget(1.0) in set(PLATFORM_FACTOR.values())


def test_every_platform_the_project_runs_on_has_a_factor() -> None:
    """A platform with no factor would take the widest one, which is safe and is not a design."""
    assert set(PLATFORM_FACTOR) == {"linux", "darwin", "win32"}
