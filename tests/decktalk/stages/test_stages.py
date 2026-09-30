"""What every stage shares: how a selection is read.

These helpers are the only code the stage package holds above its own stages, so what they
promise is held here rather than in each of the twelve modules that call them.
"""

from __future__ import annotations

from decktalk.stages import selects


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
