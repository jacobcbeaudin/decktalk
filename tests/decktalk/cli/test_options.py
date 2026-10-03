"""The flag families, and the rule that derives them from what a command answers with."""

from __future__ import annotations

from typing import get_args

import pytest
import typer

from decktalk.cli.options import GLOBALS, FailOn, Force, one_section, pairs, restated, sections_of, shared_for
from decktalk.findings import Certainty
from decktalk.results import BuildResult, StatusResult, WordsResult


def test_a_section_selection_is_parsed_by_the_library_and_kept_in_order() -> None:
    assert sections_of(["3", "5-7", "3"]) == (3, 5, 6, 7)


def test_no_section_flag_means_every_section() -> None:
    assert sections_of(None) is None
    assert sections_of([]) is None


def test_a_selection_that_names_nothing_is_a_usage_refusal() -> None:
    with pytest.raises(typer.BadParameter) as refused:
        sections_of(["three"])
    assert refused.value.param_hint == "--section"


def test_a_command_that_cuts_one_piece_needs_exactly_one_section() -> None:
    assert one_section(["3"]) == 3
    with pytest.raises(typer.BadParameter):
        one_section(["3,4"])
    with pytest.raises(typer.BadParameter):
        one_section(None)


def test_an_override_that_is_not_a_pair_is_refused_before_anything_loads() -> None:
    assert pairs(["video.crf=20"]) == ("video.crf=20",)
    with pytest.raises(typer.BadParameter) as refused:
        pairs(["video.crf"])
    assert refused.value.param_hint == "--set"


def test_the_globals_are_derived_onto_every_command() -> None:
    names = {param.name for param in shared_for(StatusResult)}
    assert {name for name, _, _ in GLOBALS} <= names


def test_a_result_that_judges_gains_the_two_finding_flags() -> None:
    assert {"fail_on", "allow"} <= {param.name for param in shared_for(BuildResult)}
    assert "fail_on" not in {param.name for param in shared_for(StatusResult)}


def test_a_result_that_buys_gains_the_two_spending_flags() -> None:
    assert {"spend", "max_cost"} <= {param.name for param in shared_for(BuildResult)}
    assert "no_voice" not in {param.name for param in shared_for(BuildResult)}
    assert "spend" not in {param.name for param in shared_for(WordsResult)}


def test_a_result_that_is_not_a_result_gains_the_globals_alone() -> None:
    assert {param.name for param in shared_for(dict)} == {name for name, _, _ in GLOBALS}


def test_a_command_can_restate_a_flag_without_touching_the_shared_one() -> None:
    """A flag that means something narrower on one command says so there and nowhere else."""
    mine = restated(Force, help="Buy it.", hidden=True)
    assert _help(mine) == "Buy it."
    assert _help(Force) != "Buy it."
    assert not getattr(get_args(Force)[1], "hidden", False)


def _help(annotation: object) -> str:
    """The help sentence an Annotated flag carries, which is what its help row prints."""
    return next(meta.help for meta in get_args(annotation)[1:] if hasattr(meta, "help"))


def test_a_threshold_names_the_least_certain_finding_a_build_stops_on() -> None:
    assert FailOn.CERTAIN.stops_on is Certainty.CERTAIN
    assert FailOn.ANY.stops_on is Certainty.UNCERTAIN
    assert FailOn.NEVER.stops_on is None
