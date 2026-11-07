"""`tests/house_rules.py` and `tests/support/house.py`, which run the house rules over the files prek passes.

A commit passes the files it stages, so the rules read those alone and a docs-only commit does not
wait on a walk of the whole tree. The lint row passes every tracked file, so CI still reads it all.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import house_rules
from support import house, tools
from support.paths import REPO


@pytest.fixture
def naming(pytestconfig: pytest.Config) -> Iterator[pytest.Config]:
    """This run's config, which a test makes name its own files and which is left as it was after."""
    yield pytestconfig
    del pytestconfig.stash[house.NAMED]


def test_a_run_that_names_no_files_reads_every_file(pytestconfig: pytest.Config) -> None:
    paths = [REPO / "README.md", REPO / "docs" / "quickstart.mdx"]
    assert house.within(pytestconfig, paths) == paths
    assert house.within(None, paths) == paths


def test_a_run_that_names_files_reads_those_alone(naming: pytest.Config) -> None:
    naming.stash[house.NAMED] = frozenset({(REPO / "README.md").resolve()})
    assert house.within(naming, [REPO / "README.md", REPO / "docs" / "quickstart.mdx"]) == [REPO / "README.md"]


def test_a_baseline_entry_whose_file_is_gone_is_judged_whatever_a_run_names(naming: pytest.Config) -> None:
    """A deleted file is never one a commit passes, so its entry would otherwise outlive it unseen."""
    naming.stash[house.NAMED] = frozenset({(REPO / "README.md").resolve()})
    baseline = {"README.md": 1, "src/decktalk/gone.py": 2, "src/decktalk/__init__.py": 3}
    assert house.excused(naming, baseline) == {"README.md": 1, "src/decktalk/gone.py": 2}


def test_the_script_runs_the_lint_files_alone_and_names_the_files_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[tuple[list[str], list[object]]] = []
    monkeypatch.setattr(house_rules.pytest, "main", lambda args, plugins: ran.append((args, plugins)) or 0)
    assert house_rules.main(["docs/help/faq.mdx"]) == 0
    [(args, [plugin])] = ran
    assert args[: len(house_rules.FLAGS)] == list(house_rules.FLAGS) and "-m" in args and tools.LINT in args
    linted = {path for path, marker in tools.MARKED_BY_PATH.items() if marker == tools.LINT}
    assert {Path(arg).relative_to(REPO / "tests").as_posix() for arg in args[len(house_rules.FLAGS) :]} == linted
    assert isinstance(plugin, house_rules.Named)
    assert plugin.files == frozenset({(REPO / "docs" / "help" / "faq.mdx").resolve()})
