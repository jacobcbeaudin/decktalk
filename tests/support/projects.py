"""The smallest `decktalk.toml` a project can have, and the one call that writes one.

Several mirrored modules judge the same document from different sides: the project loader, the cue
file, the cut flags the document derives. They share this writer so that a change to the minimal
shape is made once rather than in each of them.
"""

from __future__ import annotations

from pathlib import Path

MINIMAL_TOML = """
[project]
name = "t"

[[section]]
number = 0
clip = "media/open.mp4"

[[section]]
number = 1
page = "deck/index.html"
scene = 1

[[section]]
number = 2
page = "deck/index.html"
hold_seconds = 1.5
"""


def write_project(root: Path, toml: str = MINIMAL_TOML) -> Path:
    """Write `toml` as the project file in `root` and give the directory back."""
    (root / "decktalk.toml").write_text(toml, encoding="utf-8")
    return root
