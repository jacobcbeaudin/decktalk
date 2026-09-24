"""The platform fact: APFS and NTFS make `deck/Index.html` and `deck/index.html` one file and two strings.

The origin allowlist is a comparison between the path a request asked for, resolved, and the paths a
project declared. On a case-sensitive filesystem those two spellings are two files and the second is
refused, so a Linux runner can never fail the rule this file holds. Here they open the same bytes,
and the measured surprise is that `Path.resolve()` keeps the spelling it was given rather than the
one on disk, so one file still reaches the comparison under two names.

That is why the allowlist compares containment against declared directories rather than names: a
declared directory holds both spellings, while a declared file is reached only by the spelling that
was declared. The policy half of the pair is `tests/decktalk/media/test_origin.py`, which injects
the resolver and holds everywhere, and this half holds the assumption the policy rests on.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from decktalk.media.origin import UNDECLARED, Allowed

pytestmark = pytest.mark.platform
"""Every test here is about this machine, so only the group that names this platform runs one."""


def folds_case(root: Path) -> bool:
    """Whether this filesystem reads two spellings of one name as one file, asked rather than assumed."""
    probe = root / "Probe.marker"
    probe.write_text("x", encoding="utf-8")
    return (root / "probe.marker").exists()


def deck_with_a_page(root: Path) -> Path:
    deck = root / "deck"
    deck.mkdir(exist_ok=True)
    (deck / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return deck


def test_a_declared_directory_answers_both_spellings_of_one_file(tmp_path: Path) -> None:
    """A deck is declared as a directory, so a folding filesystem serves the page under either name."""
    deck_with_a_page(tmp_path)
    allowed = Allowed.of(tmp_path, ["deck"])

    served = allowed.target("deck/index.html")
    assert served.path is not None and not served.refused

    other = allowed.target("deck/Index.html")
    if folds_case(tmp_path):
        assert other.path is not None and not other.refused
        assert os.path.samefile(other.path, served.path), "the two spellings open one file on this filesystem"
    else:
        assert other.refused == UNDECLARED, "this filesystem is case sensitive, so the second spelling is no file"


def test_a_declared_file_is_reached_only_by_the_spelling_that_was_declared(tmp_path: Path) -> None:
    """The comparison is over strings, so a declaration names a spelling and a directory names a place."""
    deck = deck_with_a_page(tmp_path)
    allowed = Allowed.of(tmp_path, [deck / "index.html"])

    assert allowed.target("deck/index.html").path is not None
    assert allowed.target("deck/Index.html").refused == UNDECLARED


def test_folding_case_never_widens_the_allowlist(tmp_path: Path) -> None:
    """Folding may not reach a file the project never declared, which is the failure this pair catches."""
    deck_with_a_page(tmp_path)
    (tmp_path / "Script.md").write_text("## 1. A", encoding="utf-8")
    allowed = Allowed.of(tmp_path, ["deck"])

    for spelling in ("script.md", "Script.md", "SCRIPT.MD"):
        assert allowed.target(spelling).refused == UNDECLARED, spelling
