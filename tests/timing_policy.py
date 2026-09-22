"""When a late frame is a failure and when it is only news, in one place.

A hosted Linux runner presents frames on time, so a reveal that lands off its word there is a real
fault. The hosted macOS and Windows runners do not, so `cross-platform.yml` reports a late reveal
and asserts the wider limits instead of failing on it.

That policy used to live only inside the two tests that measure cue offsets, and a build's exit
code carries verify's verdict, so any test that wrote `assert code == 0` after a build quietly
gated itself on the same timing as well. `test_build_only_rerecords_section_4` did, and it failed
three times on the hosted runners over builds whose only findings were reveals 90 ms late, while
its own subject, which sections were recorded again, was never in doubt. `tolerated` is the rule
itself, with no pytest in it, so `tests/unit/test_timing_policy.py` can hold it to each case.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

import pytest

from decktalk.verdicts import Verdict

LATE_FRAME = (Verdict.OFF_CUE,)
"""What a runner that presents frames late produces, and the only verdict an ungated platform tolerates."""


def gates_timing(request: pytest.FixtureRequest) -> bool:
    """Timing gates on Linux, where the hosted runner renders on time, and reports on macOS and Windows."""
    return sys.platform == "linux" or bool(request.config.getoption("--gate-timing"))


def tolerated(code: int, verdicts: Iterable[Verdict], gate: bool) -> str | None:
    """None when a build finished acceptably, else the one sentence saying why it did not.

    A build exits 0 when it found nothing. Where timing is not gated, a build that exited on late
    reveals alone is acceptable too. Every other fault fails on every platform, and so does a late
    reveal wherever timing is gated.
    """
    if code == 0:
        return None
    # Only a certain verdict exits a build that is not strict, so an uncertain one rides along and
    # says nothing about why this build exited. The fixture's own section 5 carries SLATE for good.
    faults = [v for v in verdicts if v.certain]
    if gate:
        return f"the build exited {code} and timing is gated here: {[v.name for v in faults] or 'no finding row'}"
    if others := [v for v in faults if v not in LATE_FRAME]:
        return f"the build exited {code} on more than late reveals: {[v.name for v in others]}"
    if not faults:
        return f"the build exited {code} with no finding row to explain it"
    return None


def assert_build_finished(code: int, verdicts: Iterable[Verdict], detail: str, request: pytest.FixtureRequest) -> None:
    """Assert a build finished acceptably on this platform, and say what it tolerated when it did."""
    rows = list(verdicts)
    why = tolerated(code, rows, gates_timing(request))
    assert why is None, f"{why}\n{detail}"
    if code != 0:
        print(f"build exited {code} on late reveals only, which {sys.platform} reports and does not gate")
