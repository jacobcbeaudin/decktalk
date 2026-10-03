"""The titled placeholder frame a section whose clip is missing is cut from."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from decktalk.stages.assemble import slate
from support.recorder import FakeBrowser, FakeContext, FakePage


def drawing(monkeypatch: pytest.MonkeyPatch, launched: list[tuple[str, bool]]) -> list[FakePage]:
    """A launched browser whose pages are kept, so a test can read what each one was given to draw."""
    fake = FakeBrowser()
    drawn: list[FakePage] = []

    @contextmanager
    def chromium(_browser_path: str = "", *, policy: str, spend: bool) -> Iterator[FakeBrowser]:
        launched.append((policy, spend))
        yield fake

    def new_page(**_kwargs: object) -> FakePage:
        drawn.append(FakePage(FakeContext(Path("."))))
        return drawn[-1]

    monkeypatch.setattr(fake, "new_page", new_page)
    monkeypatch.setattr(slate, "chromium", chromium)
    return drawn


def test_a_slate_escapes_what_the_project_wrote_and_is_drawn_under_the_projects_policy(monkeypatch, tmp_path):
    """A title is the author's text, so it is shown as text and never read as markup."""
    launched: list[tuple[str, bool]] = []
    drawn = drawing(monkeypatch, launched)
    out = slate.render_slate(
        tmp_path / "slate.png",
        title="<b>Missing</b> & gone",
        eyebrow="Section 2",
        width=320,
        height=180,
        background="0x101010",
        browser_path="",
        policy="untrusted",
        spend=True,
    )
    assert out.read_bytes() == b"png"
    assert launched == [("untrusted", True)], "the slate is drawn under the run's own policy and spend"
    assert "&lt;b&gt;Missing&lt;/b&gt; &amp; gone" in drawn[0].html
    assert "background:#101010" in drawn[0].html


@pytest.mark.parametrize("name", ["policy", "spend"])
def test_no_slate_has_a_page_policy_or_a_spend_to_fall_back_on(name):
    """A default would be the guess of a caller that forgot one, which is the caller most likely to be wrong."""
    assert inspect.signature(slate.render_slate).parameters[name].default is inspect.Parameter.empty
