"""The platform fact: APFS and NTFS make `deck/Index.html` and `deck/index.html` one file and two strings.

The origin allowlist is a comparison between the names a request asked for and the names a project
declared. A declared directory is a place, so both spellings are inside it and both are allowed on
every filesystem, and what this platform decides is whether they open one file or one file and one
name for nothing. The measured surprise is that `Path` folds case on Windows and not elsewhere, so a
comparison made over paths rather than over names lets `deck/Index.html` be the declared
`deck/index.html` on one platform and nothing on the others.

That is why the allowlist compares the names: a declared directory holds both spellings, while a
declared file is reached only by the spelling that was declared, on every platform. The policy half
of the pair is `tests/decktalk/media/test_origin.py`, which holds everywhere, and this half holds the
assumption the policy rests on, which is what this machine really does with two spellings of a name.
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
    assert other.path is not None and not other.refused, "a declared directory holds every name under it"
    if folds_case(tmp_path):
        assert os.path.samefile(other.path, served.path), "the two spellings open one file on this filesystem"
    else:
        assert not other.path.exists(), "this filesystem is case sensitive, so the second spelling opens nothing"


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
