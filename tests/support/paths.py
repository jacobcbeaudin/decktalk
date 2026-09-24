"""Where the repository is, spelled once.

A test that names the repository from its own depth in the tree breaks the day the file moves, and
the mirrored layout moves files. These three are the only paths a test needs from outside `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"
DATA = TESTS / "data"
