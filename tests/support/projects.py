"""The smallest `decktalk.toml` a project can have, and the calls that write a project and load it.

Several mirrored modules judge the same document from different sides: the project loader, the cue
file, the cut flags the document derives. They share this writer so that a change to the minimal
shape is made once rather than in each of them, and every stage test lays its project out through
`load_project`, naming only the files its case needs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from decktalk.inputs import Inputs

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


def load_project(
    root: Path,
    toml: str = MINIMAL_TOML,
    *,
    page: str | None = None,
    script: str | None = None,
    cues: dict[Any, Any] | None = None,
    media: tuple[str, ...] = (),
    environ: Mapping[str, str] | None = None,
    machine: Mapping[str, Any] | None = None,
) -> Inputs:
    """Write a project into `root` with the files a case names, and load it as a stage is handed it.

    `page` is the deck's `deck/index.html`, `script` is `script.md`, `cues` is the sections of
    `cues.json`, and each of `media` is an empty file at that path, which is all a clip row asks of it.
    """
    root.mkdir(parents=True, exist_ok=True)
    write_project(root, toml)
    if page is not None:
        (root / "deck").mkdir(exist_ok=True)
        (root / "deck" / "index.html").write_text(page, encoding="utf-8")
    if script is not None:
        (root / "script.md").write_text(script, encoding="utf-8")
    if cues is not None:
        (root / "cues.json").write_text(json.dumps({"sections": cues}, indent=2), encoding="utf-8")
    for name in media:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"")
    return Inputs.load(root, environ=environ or {}, machine=machine)
