"""How late this machine presents a frame, measured on a page DeckTalk owns."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.media import bias, browser
from support.recorder import FakeBrowser, FakeContext, FakePage


def measuring(monkeypatch, presented: list[float]) -> None:
    """A launched browser whose one page answers the bias measurement with these milliseconds."""
    fake = FakeBrowser()

    @contextmanager
    def chromium(_browser_path: str = "", *, policy: str, spend: bool) -> Iterator[FakeBrowser]:
        assert policy == browser.TRUSTED, "the bias page is DeckTalk's own"
        assert not spend, "a measurement of the machine is never a run that buys"
        yield fake

    def new_page(**_kwargs: object) -> FakePage:
        page = FakePage(FakeContext(Path(".")))
        page.presented = presented
        return page

    monkeypatch.setattr(fake, "new_page", new_page)
    monkeypatch.setattr(bias, "chromium", chromium)


def test_the_bias_is_the_middle_frame_rather_than_the_worst_one(monkeypatch):
    """One frame the machine held up moves the mean and moves nothing here, which is why it is the median."""
    measuring(monkeypatch, [11.0, 12.0, 13.0, 14.0, 900.0])
    assert bias.measure_presentation_bias() == 13.0


def test_a_browser_that_reports_no_presentation_time_is_told_rather_than_guessed_for(monkeypatch):
    measuring(monkeypatch, [])
    with pytest.raises(ToolError) as raised:
        bias.measure_presentation_bias()
    assert "reports no presentation times" in str(raised.value)


def test_the_measurement_is_written_from_the_two_constants_it_is_declared_with():
    script = bias.bias_script(bias.MEASURED_FRAMES, bias.MEASURED_FRAME_MS)
    assert f"let left = {bias.MEASURED_FRAMES};" in script
    assert f"performance.now() + {bias.MEASURED_FRAME_MS};" in script
    assert "FRAMES" not in script and "HOLD_MS" not in script


BIAS_LIMIT_MS = 200
"""How far from zero a real presentation bias sits, well past any display's own refresh interval."""


@pytest.mark.browser
def test_this_machine_either_measures_a_bias_inside_the_published_range_or_says_it_cannot():
    """A browser with nothing to say refuses rather than guesses."""
    try:
        measured = bias.measure_presentation_bias()
    except ToolError as refused:
        assert "reports no presentation times" in str(refused)
        return
    assert -BIAS_LIMIT_MS <= measured <= BIAS_LIMIT_MS
