"""One browser that draws nothing, shared by the four modules of this stage, on the project in `support.pages`.

`check` reaches for three things a test must not have: a voice, a browser and an encoder. The voice
refuses itself, because a project with no credential is exactly what a check is run on. The browser
is replaced at the two attributes the stage reads, so the stage itself runs whole and what it asked
a page for is what these tests read back.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import decktalk.stages.check as stage
from decktalk.inputs import Inputs
from decktalk.media.origin import Assets
from decktalk.media.pagereport import PageReport
from decktalk.stages import storyboard
from decktalk.stages.check import scan
from support.fakes import FakePage
from support.pages import a_report


@dataclass
class Drawn:
    """Every frozen state a run asked for, with the share each comparison was told to read."""

    page: FakePage
    assets: Assets
    reports: dict[str, PageReport] = field(default_factory=dict)
    shots: list[str] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)
    share: float = 5.0

    def report(self, page: str, *scenes: dict[str, Any], warnings: Sequence[dict[str, Any]] = ()) -> None:
        """Declare what one page of the project publishes when it is opened."""
        self.reports[page] = a_report(catalog=list(scenes), warnings=list(warnings))


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> Drawn:
    """The browser seams `check` reads, replaced so the stage runs whole and opens nothing."""
    made = Drawn(page=FakePage(), assets=Assets(Path()))

    @contextmanager
    def chromium(_executable: str = "", *, policy: str, **_launch: object) -> Iterator[object]:
        made.policies.append(policy)
        yield object()

    def open_project_page(*_args: object, **_kwargs: object) -> tuple[FakePage, Assets]:
        return made.page, made.assets

    def reports_of(_page: object, _inputs: Inputs, files: Sequence[str]) -> dict[str, PageReport]:
        return {name: made.reports[name] for name in dict.fromkeys(files) if name in made.reports}

    def screenshot(_page: object, url: str, out: Path, **_kwargs: object) -> None:
        made.shots.append(url)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    monkeypatch.setattr(stage, "chromium", chromium)
    monkeypatch.setattr(stage, "open_project_page", open_project_page)
    monkeypatch.setattr(stage, "reports_of", reports_of)
    monkeypatch.setattr(storyboard, "screenshot", screenshot)
    monkeypatch.setattr(scan.frames, "changed_images_percent", lambda *_a, **_k: made.share)
    return made
