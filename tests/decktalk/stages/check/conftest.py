"""One project and one browser that draws nothing, shared by the four modules of this stage.

`check` reaches for three things a test must not have: a voice, a browser and an encoder. The voice
refuses itself, because a project with no credential is exactly what a check is run on. The browser
is replaced at the two attributes the stage reads, so the stage itself runs whole and what it asked
a page for is what these tests read back.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import decktalk.stages.check as stage
from decktalk.errors import Cancel
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.media.pagereport import PageReport
from decktalk.stages.check import scan

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
"""One element's box, which no case here measures."""


def a_run(root: Path, **environ: str) -> Run:
    """One run, opened straight on a machine, because nothing here writes an events file."""
    machine = Machine(environ=environ, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    return Run(machine, id="r1", cancel=Cancel(), root=root)


def a_project(tmp_path: Path, *, toml: str = TOML, script: str = SCRIPT, cues: dict | None = None) -> Inputs:
    """A project with a deck, a script and the cue file the case asks for."""
    (tmp_path / "deck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deck" / "index.html").write_text("<div data-scene='1'></div>", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(toml, encoding="utf-8")
    (tmp_path / "script.md").write_text(script, encoding="utf-8")
    if cues is not None:
        (tmp_path / "cues.json").write_text(json.dumps({"sections": cues}, indent=2), encoding="utf-8")
    return Inputs.load(tmp_path, environ={})


def catalog(scene: str, moments: dict[str, list[str]], **extra: object) -> dict[str, Any]:
    """One scene of a catalog, with one element per moment each slide declares."""
    elements = {
        slide: [
            {"attrs": {"data-in": wire.split(":", 1)[-1]}, "moments": {"data-in": wire}, "text": "x", "box": BOX}
            for wire in wires
        ]
        for slide, wires in moments.items()
    }
    return {"scene": scene, "elements": elements, "slides": list(moments), "cues": dict(moments), **extra}


def a_report(*scenes: dict[str, Any], warnings: Sequence[dict[str, Any]] = ()) -> PageReport:
    """What one page says about itself, as the reader at the boundary would have read it."""
    return PageReport.model_validate({"catalog": list(scenes), "warnings": list(warnings)})


@dataclass
class FakePage:
    """A Chromium page that answers every call and remembers which URLs it was pointed at."""

    urls: list[str] = field(default_factory=list)

    def goto(self, url: str, **_kwargs: object) -> None:
        self.urls.append(url)

    def evaluate(self, _script: str, *_args: object) -> object:
        return None

    def wait_for_timeout(self, _ms: float) -> None:
        """A page a test drives has nothing to settle, so waiting on it does nothing."""


@dataclass
class FakeAssets:
    """What the router recorded, which is the other origins a page reached for."""

    external: list[str] = field(default_factory=list)


@dataclass
class Drawn:
    """Every frozen state a run asked for, with the share each comparison was told to read."""

    page: FakePage
    assets: FakeAssets
    reports: dict[str, PageReport] = field(default_factory=dict)
    shots: list[str] = field(default_factory=list)
    share: float = 5.0

    def report(self, page: str, *scenes: dict[str, Any], warnings: Sequence[dict[str, Any]] = ()) -> None:
        """Declare what one page of the project publishes when it is opened."""
        self.reports[page] = a_report(*scenes, warnings=warnings)


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> Drawn:
    """The browser seams `check` reads, replaced so the stage runs whole and opens nothing."""
    made = Drawn(page=FakePage(), assets=FakeAssets())

    @contextmanager
    def chromium(_browser_path: str = "") -> Iterator[object]:
        yield object()

    def open_page(*_args: object, **_kwargs: object) -> tuple[FakePage, FakeAssets]:
        return made.page, made.assets

    def reports_of(_page: object, _inputs: Inputs, files: Sequence[str]) -> dict[str, PageReport]:
        return {name: made.reports[name] for name in dict.fromkeys(files) if name in made.reports}

    def screenshot(_page: object, url: str, out: Path, **_kwargs: object) -> None:
        made.shots.append(url)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    monkeypatch.setattr(stage, "chromium", chromium)
    monkeypatch.setattr(stage, "open_page", open_page)
    monkeypatch.setattr(stage, "reports_of", reports_of)
    monkeypatch.setattr(scan, "screenshot", screenshot)
    monkeypatch.setattr(scan.frames, "changed_images_percent", lambda *_a, **_k: made.share)
    return made
