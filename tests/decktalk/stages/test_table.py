"""The stage table: one row per stage, naming exactly the options its function takes."""

from __future__ import annotations

import inspect

import pytest

from decktalk.pipeline import Stage
from decktalk.stages.table import CALLS


def test_every_stage_has_one_row() -> None:
    assert list(CALLS) == list(Stage)


@pytest.mark.parametrize("stage", list(Stage), ids=lambda stage: stage.value)
def test_a_row_names_the_keywords_its_function_takes(stage: Stage) -> None:
    """A row that named an option its stage does not take would fail the call, and one it left out would drop a flag."""
    row = CALLS[stage]
    taken = inspect.signature(row.call).parameters.values()
    assert row.options == tuple(p.name for p in taken if p.kind is inspect.Parameter.KEYWORD_ONLY)


@pytest.mark.parametrize("stage", list(Stage), ids=lambda stage: stage.value)
def test_a_row_answers_with_the_result_its_function_returns(stage: Stage) -> None:
    row = CALLS[stage]
    assert inspect.signature(row.call, eval_str=True).return_annotation is row.result
