"""The watched run every stage directory shares, so a test reads what a stage reported one way.

A directory whose projects need a credential on the machine overrides `run_environ` in its own
conftest, and every run it opens is then made on a machine that holds it.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from decktalk.events import Event
from decktalk.inputs import Inputs
from support.runs import Watched, a_run


@pytest.fixture
def run_environ() -> dict[str, str]:
    """What the machine of a watched run holds, which is nothing unless a directory says otherwise."""
    return {}


@pytest.fixture
def make_run(run_environ: dict[str, str]) -> Callable[..., Watched]:
    """A run on a machine that holds nothing but a stream, with every line it emits kept."""

    def build(project: Inputs, *, spend: bool = False, max_cost: float | None = None) -> Watched:
        lines: list[Event] = []
        return Watched(a_run(project.root, spend=spend, max_cost=max_cost, lines=lines, **run_environ), lines)

    return build


@pytest.fixture
def watched(inputs: Inputs, make_run: Callable[..., Watched]) -> Watched:
    return make_run(inputs)
