"""When a build that exited non-zero still counts as finished, case by case, and what a budget is.

`tests/support/timing_policy.py` holds the rule every suite that drives a real build follows, and
this holds the rule to each case without a build, because the rule is the thing that went wrong: a
late reveal on a hosted macOS runner failed a test whose subject was which sections got recorded
again.

Each case here is one decision the rule makes. The message `tolerated` returns is read only for the
code it names and never for its wording, so the sentence can be rewritten without touching these.
"""

from __future__ import annotations

import pytest

from decktalk.findings import Certainty, Code
from support.timing_policy import (
    BASE_BUDGET_SECONDS,
    PLATFORM_FACTOR,
    UNGATED_EXTRA_FRAMES,
    budget,
    offset_limit_ms,
    tolerated,
)

STATED_LIMIT_MS = 80.0
"""A project's own cue offset limit, which stands here for whatever a real project states."""


def test_an_exit_code_of_zero_settles_it_whatever_was_reported() -> None:
    """The rule reads the exit code, not the rows: a reported row that did not fail the build is news."""
    assert tolerated(0, [Code.CUE_OFF], gate=True) is None


def test_late_reveals_alone_are_tolerated_where_timing_is_not_gated() -> None:
    """A runner whose compositor is not trustworthy reports a late reveal and does not fail on it."""
    assert tolerated(1, [Code.CUE_OFF], gate=False) is None


def test_late_reveals_fail_where_timing_is_gated() -> None:
    """Gating is the default everywhere, so the same build is a failure unless a step asked otherwise."""
    why = tolerated(1, [Code.CUE_OFF], gate=True)
    assert why is not None and Code.CUE_OFF.name in why


@pytest.mark.parametrize("other", [Code.CUT_SPEECH, Code.PAGE_STALLED])
def test_any_other_fault_fails_even_where_timing_is_not_gated(other: Code) -> None:
    """The tolerance is for late reveals only. A stalled page is the near miss: it is also a timing
    fault and it is deliberately not tolerated, because it means the recorder stopped presenting
    frames rather than the runner being slow."""
    why = tolerated(1, [Code.CUE_OFF, other], gate=False)
    assert why is not None and other.name in why


def test_an_uncertain_finding_riding_along_is_not_a_fault() -> None:
    """Only a certain finding exits a build that is not strict, so an uncertain one explains nothing."""
    uncertain = next(code for code in Code if code.certainty is Certainty.UNCERTAIN)
    assert tolerated(1, [Code.CUE_OFF, uncertain], gate=False) is None


def test_a_non_zero_exit_with_nothing_to_explain_it_fails() -> None:
    """An exit code with no certain finding row is a bug in the command, not a slow runner."""
    uncertain = next(code for code in Code if code.certainty is Certainty.UNCERTAIN)
    why = tolerated(1, [uncertain], gate=False)
    assert why is not None


def test_a_gated_run_holds_the_project_to_the_limit_it_states() -> None:
    """The limit is a settings key, so the gated run reads it and adds nothing of its own."""
    assert offset_limit_ms(STATED_LIMIT_MS, gate=True) == STATED_LIMIT_MS


def test_an_ungated_run_adds_the_declared_slack_and_nothing_else() -> None:
    """The `4` and the `5` this replaces were the settings plus two, written as literals in the suite."""
    widened = offset_limit_ms(STATED_LIMIT_MS, gate=False)
    assert widened > STATED_LIMIT_MS
    assert (widened - STATED_LIMIT_MS) / UNGATED_EXTRA_FRAMES == pytest.approx(
        (offset_limit_ms(0.0, gate=False)) / UNGATED_EXTRA_FRAMES
    )


def test_a_project_that_widens_its_own_limit_widens_the_ungated_one_too() -> None:
    """The slack is added rather than typed, so the two limits can never disagree about late."""
    narrow = offset_limit_ms(STATED_LIMIT_MS, gate=False)
    wide = offset_limit_ms(STATED_LIMIT_MS * 2, gate=False)
    assert wide - narrow == pytest.approx(STATED_LIMIT_MS)


def test_the_budget_is_a_ceiling_that_grows_with_the_runner() -> None:
    """A budget catches a hung page. It is generous on Linux and has to be more generous elsewhere."""
    assert budget() >= BASE_BUDGET_SECONDS
    assert min(PLATFORM_FACTOR.values()) == 1.0
    assert budget(1.0) == PLATFORM_FACTOR.get("linux") or budget(1.0) in set(PLATFORM_FACTOR.values())


def test_every_platform_the_project_runs_on_has_a_factor() -> None:
    """A platform with no factor would take the widest one, which is safe and is not a design."""
    assert set(PLATFORM_FACTOR) == {"linux", "darwin", "win32"}
