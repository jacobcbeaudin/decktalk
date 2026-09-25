"""What every stage shares: how a judgement is built, how a selection is read and how a stage is timed.

These four helpers are the only code the stage package holds above its own stages, so what they
promise is held here rather than in each of the twelve modules that call them.
"""

from __future__ import annotations

import pytest

from decktalk.findings import Applicability, Certainty, Code, Edit, EditFix, Location
from decktalk.pipeline import Stage
from decktalk.stages import judge, selects, since


def test_a_judgement_takes_its_certainty_and_its_page_from_its_code() -> None:
    """A raiser names the code and the code owns the rest, so no stage spells one fact twice."""
    found = judge(Code.CUE_OFF, "the reveal lands 0.42s after its word, past the 0.08s limit.", Location(where="3:a"))
    assert found.certainty is Certainty.CERTAIN
    assert found.url == Code.CUE_OFF.url


def test_a_judgement_carries_the_stage_that_raised_it() -> None:
    """`check` predicts and `verify` measures, and the stage is what tells the two apart."""
    found = judge(Code.CUE_NO_CHANGE, "nothing changed at 1.20s.", Location(where="3:a"), stage=Stage.VERIFY)
    assert found.stage is Stage.VERIFY


def test_a_judgement_carries_the_fix_it_was_given() -> None:
    fix = EditFix(
        title="Add the missing cue row.",
        applicability=Applicability.SAFE,
        edits=(Edit(file="cues.json", line=2, new='{"cue": "3.1:a", "on": ""}'),),
    )
    found = judge(Code.CUE_MISSING, "the page declares 3.1:a and cues.json lists 0 rows for it.",
                  Location(where="3.1:a"), fix=fix)  # fmt: skip
    assert found.fix is fix


def test_a_run_that_names_no_section_selects_every_one() -> None:
    wanted = selects(None)
    assert wanted(1) and wanted(9)


def test_a_run_that_names_sections_selects_those_alone() -> None:
    wanted = selects([3, 5])
    assert wanted(3) and wanted(5)
    assert not wanted(4)


def test_an_empty_selection_still_selects_every_section() -> None:
    """An empty run of numbers is a caller that named none, which is every section and not no section."""
    assert selects([])(2)


def test_a_stage_is_timed_in_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The clock is held still, because two readings of a running clock differ by the tick between them."""
    monkeypatch.setattr("decktalk.stages.time.monotonic", lambda: 12.3456789)
    assert since(2.0) == 10.346
