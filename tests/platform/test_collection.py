"""The platform fact: two test files with one basename collide unless the importer keeps them apart.

The suite mirrors `src/decktalk` one file per module, so `test_takes.py` exists under `artifacts/`
and again under `stages/narrate/`, and there are more pairs than that. Under pytest's default
import mode two files with one basename and no `__init__.py` between them are one module name, and
the second one collected wins or errors. `--import-mode=importlib` is what keeps them apart, and a
case-insensitive filesystem multiplies the pairs, because `test_Takes.py` and `test_takes.py` would
be one file as well.

This is an environment test because the collision is the importer meeting this filesystem. The
policy half is `tests/contract/test_layout.py`, which holds the mirror itself.
"""

from __future__ import annotations

import collections

from support.paths import TESTS

MIRROR = TESTS / "decktalk"


def test_the_mirrored_tree_really_does_hold_duplicate_basenames() -> None:
    """The risk is only worth a test while it is live, so this names the pairs that make it live."""
    counts = collections.Counter(path.name for path in MIRROR.rglob("test_*.py"))
    repeated = sorted(name for name, count in counts.items() if count > 1)
    assert repeated, "no basename repeats any more, so this file and its import mode can be reconsidered"


def test_two_files_with_one_basename_are_both_collected(pytester) -> None:
    """Both must run. One of them silently winning is the failure this import mode exists to prevent."""
    pytester.makepyfile(**{"one/test_takes.py": "def test_first():\n    assert True\n"})
    pytester.makepyfile(**{"two/test_takes.py": "def test_second():\n    assert True\n"})
    result = pytester.runpytest("--import-mode=importlib", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=2)


def test_the_project_asks_for_the_import_mode_that_keeps_them_apart() -> None:
    """A mirror this wide cannot be collected under the default mode, so the setting is part of the design."""
    import tomllib  # noqa: PLC0415  (one read of one file, which no other test in this directory needs)

    config = tomllib.loads((TESTS.parent / "pyproject.toml").read_text(encoding="utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    assert "--import-mode=importlib" in " ".join(options.get("addopts", "").split())


def test_no_two_mirrored_files_differ_only_in_case() -> None:
    """On a folding filesystem those two files are one file, so the mirror could never hold both."""
    names = [path.relative_to(MIRROR).as_posix() for path in MIRROR.rglob("test_*.py")]
    folded = collections.Counter(name.lower() for name in names)
    clashing = sorted(name for name, count in folded.items() if count > 1)
    assert clashing == [], clashing
