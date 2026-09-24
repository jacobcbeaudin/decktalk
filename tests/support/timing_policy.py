"""When a late frame is a failure and when it is only news, and how long a slow runner may take.

Three numbers used to be typed into the tests that needed them, and each one was a magic number
inside the suite that enforces the no-magic-numbers rule. They live here instead, each derived from
the project's own settings or written once with the sentence that says why.

**The timing default inverts.** It used to read `sys.platform`, which is the wrong predicate: the
property is that this runner's compositor is not trustworthy, not that this is macOS, so the
founder's own Mac was permanently weaker than a Linux runner. `--timing=gate` is the default
everywhere now, and the legs that own a weak runner pass `--timing=report` themselves, so the
weakening lives in the `GROUPS` table that owns them rather than in every test that measures a cue.

**The policy is one seam rather than a habit.** A suite that drives a real build reads its certain
findings through `held_to`, so a test written next year is on the policy by reading a run the way
every other test reads one. Applying the rule test by test left a test whose subject was which
sections got recorded again failing a whole merge on a reveal that this runner was told to report.

**A report is printed rather than swallowed.** A leg that does not gate timing still measures it,
so `note_late_reveals` writes what it tolerated into the run's own log. A test that reported
nothing and a test that reported a reveal a frame late read the same otherwise, which is how a
runner that quietly drifted would go unnoticed for a release.

**A budget is a ceiling and never a measurement.** A module timeout is a base budget times a factor
for this platform, because a hundred and eighty seconds is generous on Linux and tight on a cold
Windows runner. This module is one of the two places the suite may read `sys.platform`, which is
what `tests/contract/test_layout.py` allows and what keeps that read out of every other file.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from typing import Any

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


def holds(code: Code, gate: bool) -> bool:
    """Whether this runner still holds a deck to one reported code, which every reading asks first.

    A late reveal is the one code a runner whose compositor is not trustworthy may report without
    being held to it, because a frame presented late moves a measurement and nothing else. Every
    other code is a property of the deck rather than of the machine: a cue that never changed the
    picture, a phrase the page never found and a page that threw are faults on every runner.
    """
    return gate or code not in LATE_FRAME


def judged(codes: Iterable[Code], gate: bool) -> list[Code]:
    """Every reported code this runner still judges, which is all of them unless a late reveal is news."""
    return [found for found in codes if holds(found, gate)]


def faults(codes: Iterable[Code], gate: bool) -> list[Code]:
    """Every reported code this runner fails on, which is the judged ones it is certain about."""
    return [found for found in judged(codes, gate) if found.certainty is Certainty.CERTAIN]


def tolerated(code: int, codes: Iterable[Code], gate: bool) -> str | None:
    """None when a build finished acceptably, else the one sentence saying why it did not.

    A build exits 0 when it found nothing certain. Where timing is not gated, a build that exited on
    late reveals alone is acceptable too. Every other fault fails on every platform, and so does a
    late reveal wherever timing is gated.
    """
    if code == 0:
        return None
    rows = list(codes)
    if failing := faults(rows, gate):
        return f"the build exited {code} on a fault this runner judges: {[found.name for found in failing]}"
    if not any(found.certainty is Certainty.CERTAIN for found in rows):
        return f"the build exited {code} with no finding row to explain it"
    return None


def note_late_reveals(config: pytest.Config, subject: str, news: Iterable[str]) -> None:
    """Print what a runner that does not gate timing tolerated, because news nobody prints is lost.

    The terminal reporter is written to rather than a warning raised, because the suite turns every
    warning into an error and a report that fails the run is a gate under a second name. A leg that
    has no reporter is a run under `-p no:terminal`, where there is no log to write the news into.
    """
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    reporter.write_line(f"\n{subject}: this runner reports cue timing rather than gating it, and it tolerated:")
    for line in news:
        reporter.write_line(f"  {line}")


def held_to(config: pytest.Config, subject: str, findings: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Every certain finding this runner holds a deck to, with the ones it tolerated written to the log.

    This is the one seam a suite reads a run's certain findings through, so a leg that reports cue
    timing reports it in every test that leg runs rather than in the tests somebody remembered to
    change. A row is a finding as `--json` publishes it, and the code it names is what decides
    whether this runner judges the row or only prints it.
    """
    gate = gates_timing(config)
    held: list[Mapping[str, Any]] = []
    news: list[str] = []
    for row in findings:
        if row["certainty"] != Certainty.CERTAIN.value:
            continue
        if holds(Code(row["code"]), gate):
            held.append(row)
        else:
            news.append(f"{row['code']}: {row['message']}")
    if news:
        note_late_reveals(config, subject, news)
    return held


def assert_build_finished(code: int, codes: Iterable[Code], detail: str, config: pytest.Config) -> None:
    """Assert a build finished acceptably on this runner, and say what it tolerated when it did."""
    rows = list(codes)
    why = tolerated(code, rows, gates_timing(config))
    assert why is None, f"{why}\n{detail}"
