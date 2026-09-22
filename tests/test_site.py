"""The homepage in site/ shows nothing from the machine it was built on.

data.js carries a captured build log, and the build script writes every path in it relative to the
project. This reads the committed files the way a visitor's browser would and fails on any absolute
home or temp path, in any text file the site serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
TEXT = {".html", ".css", ".js", ".json", ".svg", ".vtt", ".md", ".txt", ".xml"}
HOME_PATH = re.compile(r"(/Users/|/home/|/root/|[A-Za-z]:\\Users\\|/private/tmp/|/tmp/)")


def site_text_files() -> list[Path]:
    return sorted(p for p in SITE.rglob("*") if p.is_file() and p.suffix in TEXT)


@pytest.mark.parametrize("path", site_text_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_machine_path_is_served(path: Path) -> None:
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        assert not HOME_PATH.search(line), f"{path.relative_to(ROOT)}:{n} names a machine path: {line.strip()[:160]}"


def test_cost_unit_matches_the_cli() -> None:
    """The page's cost sentence and the CLI's cost line name the same unit."""
    unit = "per 1,000 characters"
    assert unit in (ROOT / "src" / "decktalk" / "cli" / "output.py").read_text(encoding="utf-8")
    assert unit in (SITE / "app.js").read_text(encoding="utf-8")
