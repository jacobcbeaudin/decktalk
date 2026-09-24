"""When a build that exited non-zero still counts as finished, case by case.

`tests/support/timing_policy.py` holds the rule the cross-platform runners follow, and this holds the rule
to each case without a build, because the rule is the thing that went wrong: a late reveal on a
hosted macOS runner failed a test whose subject was which sections got recorded again.

Each case here is one decision the rule makes. The message `tolerated` returns is read only for the
verdict it names, never for its wording, so the sentence can be rewritten without touching these.
"""

from __future__ import annotations

import pytest

from decktalk.verdicts import Verdict
from support.timing_policy import tolerated


def test_an_exit_code_of_zero_settles_it_whatever_was_reported() -> None:
    """The rule reads the exit code, not the rows: a reported row that did not fail the build is news."""
    assert tolerated(0, [Verdict.OFF_CUE], gate=True) is None


def test_late_reveals_alone_are_tolerated_where_timing_is_not_gated() -> None:
    """The hosted macOS and Windows runners present frames late, and that alone is not a fault."""
    assert tolerated(1, [Verdict.OFF_CUE, Verdict.CHANGED, Verdict.OK], gate=False) is None


def test_late_reveals_fail_where_timing_is_gated() -> None:
    """On Linux the runner renders on time, so the same build is a failure there."""
    why = tolerated(1, [Verdict.OFF_CUE], gate=True)
    assert why is not None and Verdict.OFF_CUE.name in why


@pytest.mark.parametrize("other", [Verdict.SPEECH_AT_CUT, Verdict.STALLED])
def test_any_other_fault_fails_even_where_timing_is_not_gated(other: Verdict) -> None:
    """The tolerance is for late reveals only. STALLED is here because it is the near miss: a stalled
    compositor is also a timing fault, and it is deliberately not tolerated, because it means the
    recorder stopped presenting frames rather than the runner being slow."""
    why = tolerated(1, [Verdict.OFF_CUE, other], gate=False)
    assert why is not None and other.name in why


def test_an_uncertain_finding_riding_along_is_not_a_fault() -> None:
    """Only a certain verdict exits a build that is not strict. The e2e fixture's section 5 carries
    SLATE on purpose, because its media file is deliberately missing, and it said nothing about why
    a build that also reported a late reveal exited."""
    assert tolerated(1, [Verdict.OFF_CUE, Verdict.SLATE], gate=False) is None


def test_a_non_zero_exit_with_nothing_to_explain_it_fails() -> None:
    """An exit code with no finding row is a bug in the command, not a slow runner."""
    why = tolerated(1, [Verdict.CHANGED], gate=False)
    assert why is not None
