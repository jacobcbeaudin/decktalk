"""How the suite selects what it runs, which is the one rule every directory below shares.

Location answers what a test is about and the marker answers what it needs, so the marker decides
selection and nothing else does. A bare `pytest` runs everything that needs no tool, and each of the
four suite markers is reached by naming it: `pytest -m browser`, `pytest -m media`, `pytest -m e2e`,
`pytest -m scaffold`. The rule lives in a hook rather than in `addopts` because an `-m` written in
`addopts` is replaced whole by the `-m` a person types, so `-m "not e2e"` used to admit the
five-minute scaffold build and `-m unit` used to select nothing and exit green.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]

SUITE_MARKERS = ("browser", "media", "e2e", "scaffold")
"""The markers that name a tool a test needs. Every one is registered in `pyproject.toml`."""


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--gate-timing",
        action="store_true",
        default=False,
        help="fail the pipeline test on OFF CUE on every platform, as it does on Linux by itself",
    )


def selected_suites(markexpr: str) -> frozenset[str]:
    """The suite markers a `-m` expression names, whether it asks for them or refuses them."""
    return frozenset(name for name in SUITE_MARKERS if name in markexpr)


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Drop every item whose tool no `-m` expression named, and refuse an empty run.

    This runs last so that `items` is already what `-m` itself selected, which is why an expression
    that both names a marker and refuses it leaves nothing behind rather than everything.
    """
    unwanted = frozenset(SUITE_MARKERS) - selected_suites(config.option.markexpr or "")
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        needs = {mark.name for mark in item.iter_markers()} & unwanted
        (dropped if needs else kept).append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept
    if not items:
        raise pytest.UsageError(
            "no test was selected, which is a failure rather than a pass. A bare `pytest` runs the "
            f"tests that need no tool, and each suite is reached by naming its marker: {', '.join(SUITE_MARKERS)}."
        )
