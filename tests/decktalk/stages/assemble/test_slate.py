"""The slate: the titled frame a section whose clip is missing is cut from."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import ToolError
from decktalk.inputs import Inputs
from decktalk.media import browser
from decktalk.media.browser import Chromium
from decktalk.pictures import pictures
from decktalk.stages.assemble import slate
from support.fakes import BareBrowser, FakePage
from support.pages import a_project
from support.runs import a_run


def drawing(monkeypatch: pytest.MonkeyPatch, launched: list[tuple[str, bool]]) -> list[FakePage]:
    """A launched browser whose pages are kept, so a test can read what each one was given to draw."""
    fake = BareBrowser()
    drawn: list[FakePage] = []

    @contextmanager
    def chromium(_executable: str = "", *, policy: str, spend: bool) -> Iterator[Chromium]:
        launched.append((policy, spend))
        yield fake.opened()

    def new_page(**_kwargs: object) -> FakePage:
        drawn.append(FakePage())
        return drawn[-1]

    monkeypatch.setattr(fake, "new_page", new_page)
    monkeypatch.setattr(browser, "chromium", chromium)
    return drawn


def draw(inputs: Inputs, out: Path, *, spend: bool = False) -> Path:
    """One slate drawn on the pictures a run opens on `inputs`."""
    with pictures(inputs, a_run(inputs.root, spend=spend)) as made:
        return slate.render_slate(made, out, title="<b>Missing</b> & gone", eyebrow="Section 2", background="0x101010")


def test_a_slate_escapes_what_the_project_wrote_and_is_drawn_under_the_projects_policy(monkeypatch, tmp_path):
    """A title is the author's text, so it is shown as text and never read as markup."""
    launched: list[tuple[str, bool]] = []
    drawn = drawing(monkeypatch, launched)
    a_project(tmp_path)
    inputs = Inputs.load(tmp_path, environ={}, machine={"record": {"page_policy": "untrusted"}})
    out = draw(inputs, tmp_path / "slate.png", spend=True)
    assert out.read_bytes() == b"png"
    assert launched == [("untrusted", True)], "the slate is drawn under the run's own policy and spend"
    assert "&lt;b&gt;Missing&lt;/b&gt; &amp; gone" in drawn[0].html
    assert "background:#101010" in drawn[0].html
    assert "width:1920px;height:1080px" in drawn[0].html, "the slate is drawn on the run's canvas"


def test_a_slate_the_browser_will_not_draw_is_a_tool_failure_and_not_a_bug(monkeypatch, tmp_path):
    """A browser that closes under the slate is the tool failing, so it is never reported as INTERNAL."""

    def refuses(_page: FakePage, _html: str) -> None:
        raise PlaywrightError("Target page, context or browser has been closed\nCall log:")

    drawing(monkeypatch, [])
    monkeypatch.setattr(FakePage, "set_content", refuses)
    with pytest.raises(ToolError, match=r"^could not open a page \(Target page"):
        draw(a_project(tmp_path), tmp_path / "slate.png")
