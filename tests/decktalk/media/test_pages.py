"""Driving a page nobody wrote for a recorder: the deadline on every call, the seal and the report."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import InputError, ToolError
from decktalk.media import browser, pages
from decktalk.media.origin import Allowed, page_url
from decktalk.settings import COLOR_SCHEMES
from support.recorder import BARE, FakeBrowser, FakeContext, FakePage, Sink, deck_of, record


def test_page_error_text_keeps_the_message_and_the_file_and_line():
    class Err:
        name = "SyntaxError"
        message = "Identifier 'SCENES_TOTAL' has already been declared"
        stack = (
            "SyntaxError: Identifier 'SCENES_TOTAL' has already been declared\n    at file:///p/deck/index.html:120:7"
        )

    told = pages.page_error_text(Err())
    assert told == "SyntaxError: Identifier 'SCENES_TOTAL' has already been declared (index.html:120)"

    class Bare:
        message = "boom"
        stack = ""

    assert pages.page_error_text(Bare()) == "boom"


def test_every_call_into_the_page_carries_a_deadline(tmp_path):
    """A deck's own script runs in the page's one thread, so a call with no deadline is a build with none."""
    out = tmp_path / "01.webm"
    fake = FakeBrowser()
    record(tmp_path, Sink(out), out=out, fake=fake, seconds=0.1)
    page = fake.contexts[0].page
    assert page is not None and page.scripts
    for script in page.scripts:
        assert "Promise.race" in script, script
        assert f"{pages.DEADLINE_SECONDS * 1000:.0f}" in script, script


class Hangs(FakePage):
    """A page every call into fails the way Playwright fails it, with `said`."""

    def __init__(self, said: str) -> None:
        super().__init__(FakeContext(Path(".")))
        self.said = said

    def evaluate(self, _script: str, /) -> object:
        raise PlaywrightError(self.said)


UNANSWERED = "Error: the page did not answer within 15 seconds"


def test_a_call_the_page_never_answered_is_a_tool_failure():
    with pytest.raises(ToolError) as raised:
        pages.evaluate(Hangs(UNANSWERED).page(), pages.REPORT_JS)
    assert str(raised.value).startswith("the page could not answer")


def test_a_page_that_answers_neither_ready_nor_painted_is_taken_as_it_stands_and_says_so(caplog):
    """Both waits carry on rather than fail a frame, so the log is where a mistimed frame is explained."""
    with caplog.at_level("DEBUG", logger="decktalk.media.pages"):
        pages.await_painted(Hangs(UNANSWERED).page())
    said = [record.getMessage() for record in caplog.records if record.name == "decktalk.media.pages"]
    assert len(said) == 2
    assert "did not answer __dtprobe.ready()" in said[0] and "painted no frame" in said[1]


def test_a_page_that_cannot_report_leaves_a_report_that_says_so():
    report = pages.read_report(Hangs("Execution context was destroyed").page(), "01")
    assert report.warnings == () and report.unreadable


def test_the_probe_is_sealed_onto_the_window_before_the_page_runs(tmp_path):
    """A deck shares its window with the recorder's instrument, so the instrument is not the deck's to take."""
    fake = FakeBrowser()
    out = tmp_path / "01.webm"
    record(tmp_path, Sink(out), out=out, fake=fake)
    scripts = fake.contexts[0].scripts
    assert scripts[0] == pages.PROBE_JS
    assert scripts[1] == pages.SEAL_JS
    assert "writable: false" in pages.SEAL_JS and "configurable: false" in pages.SEAL_JS


def test_a_colour_scheme_chromium_does_not_know_is_refused_rather_than_passed_on():
    with pytest.raises(InputError) as raised:
        pages.scheme("sepia")
    assert "no-preference" in str(raised.value)
    assert pages.scheme("dark") == "dark"


def test_the_colour_schemes_this_module_accepts_are_the_ones_the_setting_publishes():
    """Two spellings of one closed set, held equal here, because only one of them can be a type."""
    assert set(get_args(pages.ColorScheme)) == set(COLOR_SCHEMES)


@pytest.mark.browser
def test_a_deck_cannot_take_the_probes_name_on_a_real_page(tmp_path):
    """The seal is a property of a real window, so it is proved against one rather than against a string."""
    deck = tmp_path / "deck"
    deck.mkdir()
    deck.joinpath("index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>t</title>"
        "<script>window.__dtprobe = { report: () => ({ taken: true }) };</script>",
        encoding="utf-8",
    )
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        page, _assets = pages.open_page(real, Allowed.of(tmp_path, ["deck"]), width=400, height=300)
        page.goto(page_url("deck/index.html"), wait_until="load")
        assert page.evaluate("() => typeof window.__dtprobe.report") == "function"
        assert page.evaluate("() => window.__dtprobe.report().taken") is None
        report = pages.read_report(page, "deck")
        assert report.unreadable == () and report.warnings == () and report.catalog == ()


@pytest.mark.browser
def test_the_probe_travels_with_a_page_across_every_url_it_is_driven_through(tmp_path):
    """An init script belongs to the page and not to a navigation, which is what `screenshots` relies on."""
    allowed = deck_of(tmp_path, {"one.html": BARE, "two.html": BARE})
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        page, _assets = pages.open_page(real, allowed, width=320, height=240)
        for name in ("one.html", "two.html"):
            page.goto(page_url(f"deck/{name}"), wait_until="load")
            assert page.evaluate("() => typeof window.__dtprobe.report") == "function", name


def test_a_frozen_frame_is_taken_once_the_page_is_ready_and_has_painted_twice(tmp_path):
    """A fixed 400 ms settle was most of `check`, and ready() and two frames are what a frame needs."""
    waits: list[float] = []

    class Timed(FakePage):
        def wait_for_timeout(self, ms: float, /) -> None:
            waits.append(ms)

    page = Timed(FakeContext(tmp_path))
    pages.screenshot(page.page(), page_url("deck/index.html"), tmp_path / "a.png")
    order = [script for script in page.scripts if pages.READY_JS in script or pages.PAINTED_JS in script]
    assert [pages.READY_JS in script for script in order] == [True, False]
    assert waits == [], "no fixed settle is spent on top of ready() and two painted frames"
