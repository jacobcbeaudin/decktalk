"""One browser that draws nothing, shared by the four modules of this stage, on the project in `support.pages`.

`check` reaches for three things a test must not have: a voice, a browser and an encoder. The voice
refuses itself, because a project with no credential is exactly what a check is run on. The browser
is replaced where `decktalk.pictures` reaches it, so the stage itself runs whole and what it asked a
page for is what these tests read back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from decktalk.stages.check import scan
from support.fakes import Browsed
from support.pages import a_report


@dataclass
class Drawn:
    """Every frozen state a run asked for, with the share each comparison was told to read."""

    browsed: Browsed
    share: float = 5.0

    def report(self, page: str, *scenes: dict[str, Any], warnings: Sequence[dict[str, Any]] = ()) -> None:
        """Declare what one page of the project publishes when it is opened."""
        self.browsed.reports[page] = a_report(catalog=list(scenes), warnings=list(warnings))

    @property
    def shots(self) -> list[str]:
        """Every URL a still was drawn at."""
        return self.browsed.stills

    @property
    def policies(self) -> list[str]:
        """The page policy of every browser the run launched."""
        return self.browsed.policies


@pytest.fixture
def drawn(monkeypatch: pytest.MonkeyPatch) -> Drawn:
    """The browser seams `check` reaches, replaced so the stage runs whole and opens nothing."""
    made = Drawn(Browsed().install(monkeypatch))
    monkeypatch.setattr(scan.frames, "changed_images_percent", lambda *_a, **_k: made.share)
    return made
