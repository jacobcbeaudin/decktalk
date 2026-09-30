"""A two-section project on one page, and the catalog that page publishes, for every stage that reads one.

`check`, `storyboard`, `cue` and `verify` each judge what a page declared about its elements. They
share this project and this catalog builder, so a change to the catalog's shape is made once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from decktalk.inputs import Inputs

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
"""

SCRIPT = """# Demo

## 1. One

Hello there again.

## 2. Two

Second section speaks as well.
"""

BOX = {"x": 0, "y": 0, "w": 10, "h": 10}
"""One element's box, which no case measures."""


def a_project(tmp_path: Path, *, toml: str = TOML, script: str = SCRIPT, cues: dict | None = None) -> Inputs:
    """A project with a deck, a script and the cue file the case asks for."""
    (tmp_path / "deck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deck" / "index.html").write_text("<div data-scene='1'></div>", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    (tmp_path / "script.md").write_text(script, encoding="utf-8")
    if cues is not None:
        (tmp_path / "cues.json").write_text(json.dumps({"sections": cues}, indent=2), encoding="utf-8")
    return Inputs.load(tmp_path, environ={})


def elements(moments: dict[str, list[str]], *, text: str = "x") -> dict[str, list[dict[str, Any]]]:
    """One element per moment each slide declares, named by its cue and saying `text`."""
    return {
        slide: [
            {"attrs": {"data-in": wire.split(":", 1)[-1]}, "moments": {"data-in": wire}, "text": text, "box": BOX}
            for wire in wires
        ]
        for slide, wires in moments.items()
    }


def catalog(scene: str, moments: dict[str, list[str]], *, text: str = "x", **extra: object) -> dict[str, Any]:
    """One scene of a catalog, with its elements, its slides and the cues each slide declares."""
    found = elements(moments, text=text)
    return {"scene": scene, "elements": found, "slides": list(moments), "cues": dict(moments), **extra}
