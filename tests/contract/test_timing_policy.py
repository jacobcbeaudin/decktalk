"""When a build that exited non-zero still counts as finished, case by case, and what a budget is.

`tests/support/timing_policy.py` holds the rule every suite that drives a real build follows, and
this holds the rule to each case without a build, because the rule is where a mistake costs most: a
late reveal on a hosted macOS runner would fail a test whose subject is which sections get recorded
again.

Each case here is one decision the rule makes. The message `tolerated` returns is read only for the
code it names and never for its wording, so the sentence can be rewritten without touching these.

The middle section holds the seam, which is the one reading every suite that drives a real build
gets its errors from. A rule applied test by test lets a second test fail a merge on the same
reveal, so the rule being right matters less than every test asking it.

The last section holds the other half of the rule, which is that a leg reaches the suite with it.
A rule that reads `--timing` correctly does nothing unless a row of `GROUPS` passes that flag,
because without it every hosted runner gates and the decision lives only in a docstring.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import check
from decktalk.findings import Code, Severity
from support.timing_policy import (
    BASE_BUDGET_SECONDS,
    LATE_FRAME,
    PLATFORM_FACTOR,
    UNGATED_EXTRA_FRAMES,
    budget,
    faults,
    held_to,
    judged,
    offset_limit_ms,
    tolerated,
)

STATED_LIMIT_MS = 80.0
"""A project's own cue offset limit, which stands here for whatever a real project states."""


REPORTS_TIMING = (
    "browser-platforms",
    "media-platforms",
    "e2e-platforms",
    "scaffold",
    *(() if check.LINUX_GATES_TIMING else ("e2e",)),
)
"""Every leg whose compositor is not trustworthy, which is the decision written as names.

The three `-platforms` rows are the hosted macOS and Windows runners, which composite through a
stack DeckTalk does not own. `scaffold` is a hosted Linux runner rendering five whole projects in
software, where a frame is presented tens of milliseconds after the paint it answers. The Linux
`e2e` row reports too until `LINUX_GATES_TIMING` says three runs in a row have trusted it. Every
other leg gates, which is what keeps the Linux row of each pair the one that holds a deck to its limit.
"""


WARNING = next(code for code in Code if code.severity is Severity.WARNING)
"""One finding a run is not sure of, for the rows where a warning rides along."""


@pytest.mark.parametrize(
    ("code", "codes", "gate", "named"),
    [
        # The rule reads the exit code, not the rows: a reported row that did not fail the build is news.
        pytest.param(0, [Code.CUE_OFF], True, None, id="an exit code of zero settles it"),
        # A runner whose compositor is not trustworthy reports a late reveal and does not fail on it.
        pytest.param(1, [Code.CUE_OFF], False, None, id="late reveals alone where timing is not gated"),
        # Gating is the default everywhere, so the same build is a failure unless a step asked otherwise.
        pytest.param(1, [Code.CUE_OFF], True, Code.CUE_OFF.name, id="late reveals where timing is gated"),
        # The tolerance is for late reveals only. A stalled page is the near miss: it is also a timing
        # fault and it is deliberately not tolerated, because it means the recorder stopped presenting
        # frames rather than the runner being slow.
        pytest.param(1, [Code.CUE_OFF, Code.CUT_SPEECH], False, Code.CUT_SPEECH.name, id="cut speech"),
        pytest.param(1, [Code.CUE_OFF, Code.RECORD_STALLED], False, Code.RECORD_STALLED.name, id="a stalled page"),
        # Only an error exits a build that is not strict, so a warning explains nothing.
        pytest.param(1, [Code.CUE_OFF, WARNING], False, None, id="a warning riding along"),
        # An exit code with no error row is a bug in the command, not a slow runner.
        pytest.param(1, [WARNING], False, "", id="a non-zero exit with nothing to explain it"),
    ],
)
def test_a_build_is_tolerated_only_for_late_reveals_where_timing_is_not_gated(
    code: int, codes: list[Code], gate: bool, named: str | None
) -> None:
    why = tolerated(code, codes, gate=gate)
    assert why is None if named is None else why is not None and named in why


def test_a_reading_with_no_exit_code_is_judged_on_the_same_rule() -> None:
    """`verify --fail-on never` always exits 0, so its rows are judged rather than its code."""
    assert faults([Code.CUE_OFF], gate=True) == [Code.CUE_OFF]
    assert faults([Code.CUE_OFF], gate=False) == []


@pytest.mark.parametrize("other", [Code.CUE_NO_CHANGE, Code.PAGE_WORDS_NOT_FOUND, Code.PAGE_RENDER_THREW])
def test_a_finding_that_is_not_a_late_landing_is_judged_wherever_it_is_read(other: Code) -> None:
    """A cue that never changed, a phrase the page never found and a page that threw are the deck."""
    assert faults([Code.CUE_OFF, other], gate=False) == [other]
    assert judged([Code.CUE_OFF, other], gate=False) == [other]


def test_the_starter_rule_reads_every_row_and_not_only_the_errors() -> None:
    """The starter may publish no finding at all, so a warning row is judged there as well."""
    assert judged([WARNING], gate=True) == [WARNING]
    assert judged(LATE_FRAME, gate=False) == []


def test_a_gated_run_holds_the_project_to_the_limit_it_states() -> None:
    """The limit is a settings key, so the gated run reads it and adds nothing of its own."""
    assert offset_limit_ms(STATED_LIMIT_MS, gate=True) == STATED_LIMIT_MS


def test_an_ungated_run_adds_the_declared_slack_and_nothing_else() -> None:
    """The slack is the declared frames over the stated limit, so no literal in the suite restates it."""
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


# ---- the seam every suite reads a finding through ------------------------------------------------


class Leg:
    """A run of the suite with the timing option set one way, which is all the policy reads of one.

    This file's own run gates, because a contract may not depend on the flag the leg that collected
    it was given, so both readings are stood up here. The leg is its own terminal reporter, which is
    how the sentences it printed are read back.
    """

    def __init__(self, timing: str) -> None:
        self.timing = timing
        self.printed: list[str] = []
        self.pluginmanager = SimpleNamespace(get_plugin=lambda name: self)

    def getoption(self, name: str, **_: object) -> object:
        """The option this leg was given, whatever default the caller offered for a run without one."""
        assert name == "--timing", f"the policy reads one option and asked for {name}"
        return self.timing

    def write_line(self, line: str) -> None:
        self.printed.append(line)


def row(code: Code, severity: Severity | None = None) -> dict[str, Any]:
    """One finding as `--json` publishes it, which is the shape the seam reads a run's findings in."""
    return {
        "code": code.value,
        "severity": (severity or code.severity).value,
        "message": f"{code.value} was reported by the run",
    }


def seam(timing: str, *findings: dict[str, Any]) -> tuple[list[Any], list[str]]:
    """What a leg with this timing option holds a deck to, and what it printed instead."""
    leg = Leg(timing)
    held = held_to(cast(pytest.Config, leg), "pipeline", findings)
    return list(held), leg.printed


def test_a_late_landing_is_printed_rather_than_returned_where_timing_is_not_gated() -> None:
    """The seam is what makes a leg's decision reach a test that is about something else entirely."""
    held, printed = seam("report", row(Code.CUE_OFF))
    assert held == []
    assert any(Code.CUE_OFF.value in line for line in printed), printed


def test_a_late_landing_is_returned_where_timing_is_gated() -> None:
    """Gating is the default, so the same run read on a trusted runner hands the row back to the test."""
    held, printed = seam("gate", row(Code.CUE_OFF))
    assert held == [row(Code.CUE_OFF)]
    assert printed == []


@pytest.mark.parametrize("other", [Code.CUE_NO_CHANGE, Code.PAGE_WORDS_NOT_FOUND, Code.PAGE_RENDER_THREW])
def test_every_other_error_is_returned_on_either_leg(other: Code) -> None:
    """A deck's own fault fails everywhere, which is what a leg that reports timing does not touch."""
    assert seam("report", row(Code.CUE_OFF), row(other))[0] == [row(other)]
    assert seam("gate", row(other))[0] == [row(other)]


def test_a_warning_row_is_not_an_error_on_either_leg() -> None:
    """The seam answers what a run is sure about, so a row it is unsure of is neither held nor news."""
    assert seam("gate", row(WARNING))[0] == []
    assert seam("report", row(WARNING, Severity.WARNING)) == ([], [])


def test_a_run_with_no_reporter_still_holds_the_deck_to_every_other_finding() -> None:
    """A run under `-p no:terminal` has nowhere to print the news, which may not lose a fault."""
    leg = Leg("report")
    leg.pluginmanager = SimpleNamespace(get_plugin=lambda name: None)
    held = held_to(cast(pytest.Config, leg), "pipeline", [row(Code.CUE_OFF), row(Code.CUE_NO_CHANGE)])
    assert list(held) == [row(Code.CUE_NO_CHANGE)]


def test_the_budget_is_a_ceiling_that_grows_with_the_runner() -> None:
    """A budget catches a hung page. It is generous on Linux and has to be more generous elsewhere."""
    assert budget() >= BASE_BUDGET_SECONDS
    assert min(PLATFORM_FACTOR.values()) == 1.0
    assert budget(1.0) == PLATFORM_FACTOR.get("linux") or budget(1.0) in set(PLATFORM_FACTOR.values())


def test_every_platform_the_project_runs_on_has_a_factor() -> None:
    """A platform with no factor would take the widest one, which is safe and is not a design."""
    assert set(PLATFORM_FACTOR) == {"linux", "darwin", "win32"}


# ---- the leg that carries the decision ---------------------------------------------------------


def suites(group: check.Group) -> list[tuple[str, ...]]:
    """Every command of a group that runs the suite, which is the only kind `--timing` reaches."""
    return [command for command in group.commands if "pytest" in command]


def test_the_table_passes_the_flag_on_every_named_leg_and_on_no_other() -> None:
    """One assertion in both directions, because a flag on a trusted runner is as wrong as none here."""
    for group in check.GROUPS:
        for command in suites(group):
            reports = check.REPORT_TIMING in command
            assert reports == (group.name in REPORTS_TIMING), f"{group.name}: {' '.join(command)}"


def test_a_second_platform_row_can_never_be_added_without_the_flag() -> None:
    """`elsewhere()` makes these rows, so a group added to `ON_A_REAL_TOOL` is covered by being added."""
    hosted = {group.name for group in check.GROUPS if suites(group) and check.LINUX not in group.runners}
    assert hosted, "the table names no suite on macOS or Windows, so this rule guards nothing"
    assert hosted <= set(REPORTS_TIMING), sorted(hosted - set(REPORTS_TIMING))


def test_the_flag_the_table_passes_is_the_option_the_suite_registers() -> None:
    """A flag spelled in one file and read in another is two spellings until something holds them."""
    name, _, value = check.REPORT_TIMING.partition("=")
    assert name == "--timing"
    assert value == "report"
