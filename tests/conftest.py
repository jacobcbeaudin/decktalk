"""How the suite selects what it runs, which is the one rule every directory below shares.

Location answers what a test is about and the marker answers what it needs, so the marker decides
selection and nothing else does. A bare `pytest` runs everything that needs no tool, and each of the
six suite markers is reached by naming it: `pytest -m browser`, `pytest -m media`, `pytest -m e2e`,
`pytest -m scaffold`, `pytest -m platform`, `pytest -m wheel`. A file that carries no marker of its
own and still needs something beyond Python is marked by its path as it is collected. The rule
lives in a hook rather than in `addopts`, because an `-m` written in `addopts` is replaced whole by
the `-m` a person types, and `-m "not e2e"` would then admit the five-minute scaffold build.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hypothesis import settings
from hypothesis.configuration import set_hypothesis_home_dir

from decktalk import machine
from support.tools import FETCHED, MARKED_BY_PATH, SUITE_MARKERS, machine_tools

pytest_plugins = ["pytester"]

HERE = Path(__file__).parent
"""The directory the paths in `MARKED_BY_PATH` are read under."""

LOOPBACK = frozenset(("127.0.0.1", "::1", "localhost", "0.0.0.0", "::", "", None))
"""The addresses a test may resolve, which are this machine's own and nothing a key could be sent to."""

# A property test draws its examples from a seed derived from the test itself and keeps no example
# database, so every machine and every CI run tries the same examples in the same order and a
# failure seen once is seen again. No deadline is set, because under `-n auto` a slow worker would
# turn the time an example took into a failure that says nothing about the code. What Hypothesis
# still caches, its unicode tables and the constants it reads from the code, goes under `tests/out/`
# rather than into the directory the run started in.
settings.register_profile("decktalk", derandomize=True, database=None, deadline=None, print_blob=True)
settings.load_profile("decktalk")
set_hypothesis_home_dir(HERE / "out" / "hypothesis")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--timing",
        choices=("gate", "report"),
        default="gate",
        help="whether a late reveal fails the run or is only reported, default gate",
    )


def selected_suites(markexpr: str) -> frozenset[str]:
    """The suite markers a `-m` expression names, whether it asks for them or refuses them."""
    return frozenset(name for name in SUITE_MARKERS if name in markexpr)


def pytest_itemcollected(item: pytest.Item) -> None:
    """Mark an item whose file is in `MARKED_BY_PATH`, before `-m` or the hook below reads its markers."""
    if item.path.is_relative_to(HERE) and (marker := MARKED_BY_PATH.get(item.path.relative_to(HERE).as_posix())):
        item.add_marker(marker)


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


@pytest.fixture(scope="session")
def httpserver_listen_address() -> tuple[str, int]:
    """Every loopback listener binds 127.0.0.1 by number, because a page under test is given that address.

    It also leaves `localhost` a second host on the same machine, which a redirect test needs.
    """
    return ("127.0.0.1", 0)


@pytest.fixture(autouse=True)
def only_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that resolves a host other than this machine, before a byte could leave it.

    It is the root's, so it holds in every directory and under every marker. The fake voice stands in
    under the shipped voice's name, so a test that forgot it would build the real adapter, and this is
    what stops that test reaching the service with whatever key it holds. No suite fetches in this
    process: one that needs a tool fails with the command that fetches it, and a subprocess the e2e
    suite starts carries a guard of its own.
    """
    resolve = socket.getaddrinfo

    def guarded(host: str | bytes | None, *args: int, **kwargs: int) -> list[Any]:
        if host not in LOOPBACK:
            raise AssertionError(f"a test tried to reach {host!r}, which is not this machine")
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


@pytest.fixture(autouse=True)
def no_real_take_store(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Keep every machine the suite builds from its environment off this user's real data folder.

    A machine keeps its take store in the per-user data folder by default, and a test that buys a take
    through `Machine.from_environment` would write it there. The machine reads that folder through one
    name, so each test hands it a fresh directory instead, the way a host hands its machine a cache.
    """
    data = tmp_path_factory.mktemp("data")
    monkeypatch.setattr(machine, "standard_data_dir", lambda _environ, _home: data)


@pytest.fixture(autouse=True)
def fetched_tools(request: pytest.FixtureRequest) -> Iterator[None]:
    """Bind this process's machine's tools for every test that runs a fetched tool, and for no other."""
    if not any(request.node.get_closest_marker(name) for name in FETCHED):
        yield
        return
    with machine_tools():
        yield
