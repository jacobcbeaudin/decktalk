"""A two-section project on one page, the catalog that page publishes and the log its recording left.

`check`, `storyboard`, `cue`, `verify` and `assemble` each judge what a page declared about itself.
They share this project, this catalog builder, this page report and this log writer, so a change
to any of those shapes is made once.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from decktalk.artifacts import RecordingLog
from decktalk.findings import Finding
from decktalk.inputs import Inputs
from decktalk.media.pagereport import PageReport, Recording
from support.projects import load_project

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

TWO_SCENE_PAGE = """<!doctype html><html><body>
<div data-scene="1"><template data-slide="1.1"><p data-in="open">one</p></template></div>
<div data-scene="2"><template data-slide="2.1"><p data-in="open">two</p></template></div>
</body></html>
"""
"""The two-scene page the recorder tests open, each scene with one slide and one reveal."""

SCENE_ONE = "<div data-scene='1'></div>"
"""A deck page with one scene on it, which is all a project that is never recorded needs."""

BOX = {"x": 0, "y": 0, "w": 10, "h": 10}
"""One element's box, which no case measures."""


def a_project(tmp_path: Path, *, toml: str = TOML, script: str = SCRIPT, cues: dict | None = None) -> Inputs:
    """A project with a deck, a script and the cue file the case asks for."""
    return load_project(tmp_path, toml, page=SCENE_ONE, script=script, cues=cues)


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


def a_recording(**fields: object) -> Recording:
    """What the recorder knew about one section's webm, with a short page and nothing it loaded."""
    base: dict[str, object] = {
        "url": "http://project.localhost/deck/index.html",
        "assets": (),
        "external": (),
        "requested_seconds": 2.0,
        "load_seconds": 0.1,
        "settle_seconds": 0.1,
        "clock_start_seconds": 0.2,
        "page_errors": (),
        "report": PageReport(),
    }
    return Recording.model_validate({**base, **fields})


def a_report(**fields: object) -> PageReport:
    """What the page says about its first scene, with every field a case names in place of the default."""
    return PageReport.model_validate({"version": "0.5.0", "mode": "cue", "scene": "1", "slide": "1.1", **fields})


def write_log(
    inputs: Inputs,
    section: int,
    *,
    report: PageReport | None = None,
    findings: Sequence[Finding] = (),
    requested_seconds: float = 2.0,
) -> None:
    """The log one section's recording left, carrying what its page said and what the recorder judged."""
    RecordingLog(
        section=section,
        input_hash="abc",
        recording=a_recording(requested_seconds=requested_seconds, report=report or PageReport()),
        findings=tuple(findings),
    ).write(inputs.workspace.recording_log(f"{section:02d}"))
