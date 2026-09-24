"""When a build that exited non-zero still counts as finished, case by case, and what a budget is.

`tests/support/timing_policy.py` holds the rule every suite that drives a real build follows, and
this holds the rule to each case without a build, because the rule is the thing that went wrong: a
late reveal on a hosted macOS runner failed a test whose subject was which sections got recorded
again.

Each case here is one decision the rule makes. The message `tolerated` returns is read only for the
code it names and never for its wording, so the sentence can be rewritten without touching these.

The last section holds the other half of the rule, which is that a leg reaches the suite with it.
The rule read `--timing` correctly from the day it was written and no row of `GROUPS` ever passed
that flag, so every hosted runner gated and the founder's decision lived only in a docstring.
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType
from typing import Protocol

import pytest

from decktalk.findings import Certainty, Code
from support.paths import REPO
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


def check_table() -> ModuleType:
    """`scripts/check.py` as a module, because `GROUPS` is the one place a leg is written down.

    The script is loaded from its path rather than imported by name, because `scripts/` is not a
    package and putting it on the path would make every check script importable from every test.
    """
    spec = importlib.util.spec_from_file_location("check_table", REPO / "scripts" / "check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECK = check_table()

REPORTS_TIMING = ("browser-platforms", "media-platforms", "e2e-platforms", "scaffold")
"""Every leg whose compositor is not trustworthy, which is the founder's decision written as names.

The three `-platforms` rows are the hosted macOS and Windows runners, which composite through a
stack DeckTalk does not own. `scaffold` is a hosted Linux runner rendering five whole projects in
software, where a frame is presented tens of milliseconds after the paint it answers. Every other
leg gates, which is what keeps the Linux row of each pair the one that holds a deck to its limit.
"""


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


# ---- the leg that carries the decision ---------------------------------------------------------


class Row(Protocol):
    """What this file reads of a `GROUPS` row, which is a structural type because the table is a
    module loaded from a path and its own dataclass is therefore not a name this file can import."""

    name: str
    runners: tuple[str, ...]
    commands: tuple[tuple[str, ...], ...]


def suites(group: Row) -> list[tuple[str, ...]]:
    """Every command of a group that runs the suite, which is the only kind `--timing` reaches."""
    return [command for command in group.commands if "pytest" in command]


def test_the_table_passes_the_flag_on_every_leg_the_founder_named_and_on_no_other() -> None:
    """One assertion in both directions, because a flag on a trusted runner is as wrong as none here."""
    for group in CHECK.GROUPS:
        for command in suites(group):
            reports = CHECK.REPORT_TIMING in command
            assert reports == (group.name in REPORTS_TIMING), f"{group.name}: {' '.join(command)}"


def test_a_second_platform_row_can_never_be_added_without_the_flag() -> None:
    """`elsewhere()` makes these rows, so a group added to `ON_A_REAL_TOOL` is covered by being added."""
    hosted = {group.name for group in CHECK.GROUPS if suites(group) and CHECK.LINUX not in group.runners}
    assert hosted, "the table names no suite on macOS or Windows, so this rule guards nothing"
    assert hosted <= set(REPORTS_TIMING), sorted(hosted - set(REPORTS_TIMING))


def test_the_flag_the_table_passes_is_the_option_the_suite_registers() -> None:
    """A flag spelled in one file and read in another is two spellings until something holds them."""
    name, _, value = CHECK.REPORT_TIMING.partition("=")
    assert name == "--timing"
    assert value == "report"
