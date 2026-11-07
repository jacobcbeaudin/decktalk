"""Run the house rules for prose, vocabulary and numbers over the files named on the command line.

    pixi run python tests/house_rules.py FILE...    # what the house-rules hook runs, with the files prek passes

The rules are the three test files the collection hook marks as lint, run in one pytest process
that collects those files alone. tests/support/house.py says how a rule reads only the named files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Run as a plain script, where pytest's own `pythonpath` is not in force yet, as the rules' `--write` is.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import house, tools

ROOT = Path(__file__).resolve().parent.parent

FLAGS = ("-q", "-rs", "-p", "no:cacheprovider", "-m", tools.LINT)
"""The run every lint rule gets: quiet, each skip's reason printed, nothing cached, the lint suite alone."""


class Named:
    """The pytest plugin that hands the rules the files this run names."""

    def __init__(self, files: frozenset[Path]) -> None:
        self.files = files

    def pytest_configure(self, config: pytest.Config) -> None:
        config.stash[house.NAMED] = self.files


def rules() -> list[str]:
    """The test files of the house rules, as the collection hook marks them."""
    return [str(ROOT / "tests" / path) for path, marker in tools.MARKED_BY_PATH.items() if marker == tools.LINT]


def main(argv: list[str]) -> int:
    files = frozenset((ROOT / name).resolve() for name in argv)
    return int(pytest.main([*FLAGS, *rules()], plugins=[Named(files)]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
