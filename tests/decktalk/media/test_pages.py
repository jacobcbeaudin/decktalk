"""Driving a page nobody wrote for a recorder: the deadline on every call, the seal and the report."""

from __future__ import annotations

import threading
import time
from typing import get_args

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import InputError, ToolError
from decktalk.media import browser, pages, stepping
from decktalk.media.origin import Allowed, page_url
from decktalk.settings import COLOR_SCHEMES, MotionConfig
from support.fakes import FakePage


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


def test_every_call_into_the_page_carries_a_deadline():
    """A deck's own script runs in the page's one thread, so a call with no deadline is a build with none."""
    page = FakePage()
    pages.await_painted(page.page())
    pages.read_report(page.page(), "01")
    pages.page_errors(page.page(), [], "01")
    assert page.scripts
    for script in page.scripts:
        assert script.startswith("() => window.__decktalkProbe.within("), script
        assert script.endswith(f", {pages.DEADLINE_SECONDS:g})"), script


class Hangs(FakePage):
    """A page every call into fails the way Playwright fails it, with `said`."""

    def __init__(self, said: str) -> None:
        super().__init__()
        self.said = said

    def evaluate(self, script: str, *_args: object) -> object:
        del script
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
    assert "did not answer __decktalkProbe.ready()" in said[0] and "painted no frame" in said[1]


def test_a_page_that_cannot_report_leaves_a_report_that_says_so():
    report = pages.read_report(Hangs("Execution context was destroyed").page(), "01")
    assert report.warnings == () and report.unreadable


def test_the_probe_is_sealed_onto_the_window_before_the_page_runs():
    """A deck shares its window with the recorder's instrument, so the instrument is not the deck's to take."""
    page = FakePage()
    pages.instrument(page.page())
    scripts = page.init_scripts
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
        "<script>window.__decktalkProbe = { report: () => ({ taken: true }) };</script>",
        encoding="utf-8",
    )
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        page, _assets = pages.open_page(real, Allowed.of(tmp_path, ["deck"]), width=400, height=300)
        page.goto(page_url("deck/index.html"), wait_until="load")
        assert page.evaluate("() => typeof window.__decktalkProbe.report") == "function"
        assert page.evaluate("() => window.__decktalkProbe.report().taken") is None
        report = pages.read_report(page, "deck")
        assert report.unreadable == () and report.warnings == () and report.catalog == ()


BARE = "<!doctype html><meta charset=utf-8><title>t</title><p>no runtime here</p>"
"""A page with no runtime on it, which the probe has to reach all the same."""


@pytest.mark.browser
def test_the_probe_travels_with_a_page_across_every_url_it_is_driven_through(tmp_path):
    """An init script belongs to the page and not to a navigation, which is what `screenshots` relies on."""
    deck = tmp_path / "deck"
    deck.mkdir()
    for name in ("one.html", "two.html"):
        deck.joinpath(name).write_text(BARE, encoding="utf-8")
    allowed = Allowed.of(tmp_path, ["deck"])
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        page, _assets = pages.open_page(real, allowed, width=320, height=240)
        for name in ("one.html", "two.html"):
            page.goto(page_url(f"deck/{name}"), wait_until="load")
            assert page.evaluate("() => typeof window.__decktalkProbe.report") == "function", name


def test_a_frozen_frame_is_taken_once_the_page_is_ready_and_has_painted_twice(tmp_path):
    """A fixed 400 ms settle was most of `check`, and ready() and two frames are what a frame needs."""
    waits: list[float] = []

    class Timed(FakePage):
        def wait_for_timeout(self, _ms: float) -> None:
            waits.append(_ms)

    page = Timed()
    pages.screenshot(page.page(), page_url("deck/index.html"), tmp_path / "a.png")
    order = [script for script in page.scripts if pages.READY_JS in script or pages.PAINTED_JS in script]
    assert [pages.READY_JS in script for script in order] == [True, False]
    assert waits == [], "no fixed settle is spent on top of ready() and two painted frames"


def test_a_frame_the_browser_will_not_write_is_a_tool_failure_and_not_a_bug(tmp_path, monkeypatch):
    """A page that closed under the screenshot is the tool failing, so it is never reported as INTERNAL."""

    def refuses(_page: FakePage, **_kwargs: object) -> None:
        raise PlaywrightError("Target page, context or browser has been closed\nCall log:")

    monkeypatch.setattr(FakePage, "screenshot", refuses)
    page = FakePage().page()
    with pytest.raises(ToolError, match=r"^could not write 01\.png \(Target page"):
        pages.screenshot(page, page_url("deck/index.html"), tmp_path / "01.png")


@pytest.mark.browser
@pytest.mark.timeout(60)
def test_a_call_into_a_page_on_the_held_clock_still_meets_its_deadline(tmp_path):
    """The deadline raced the page's own `setTimeout`, which a held clock never fires, so a hung call hung a build."""
    deck = tmp_path / "deck"
    deck.mkdir()
    deck.joinpath("index.html").write_text(BARE, encoding="utf-8")
    allowed = Allowed.of(tmp_path, ["deck"])
    with (
        browser.chromium(policy=browser.TRUSTED, spend=False) as real,
        stepping.stepping(real, allowed, width=320, height=240, color_scheme="dark", motion=MotionConfig()) as (
            page,
            _assets,
        ),
    ):
        page.goto(page_url("deck/index.html"), wait_until="load")
        stepping.Stepper(page, 25).hold()
        began = time.monotonic()
        with pytest.raises(ToolError, match="did not answer within 1 seconds"):
            pages.evaluate(page, "() => new Promise(() => {})", deadline_seconds=1.0)
        assert time.monotonic() - began < 10


# ---- the watchdog holds a call the page cannot race from inside ----------------------------------


WATCHED_SECONDS = 0.2
"""A call deadline short enough that a watchdog test waits a moment, and long enough that a prompt call meets it."""


class Closing:
    """A page's close, which a stuck call waits on and which counts how often the watchdog asked for it."""

    def __init__(self) -> None:
        self.closed = threading.Event()
        self.times = 0

    def __call__(self) -> None:
        self.times += 1
        self.closed.set()

    def stuck(self) -> None:
        """A call into the page that answers only once the page is closed under it, as Playwright's does."""
        assert self.closed.wait(WATCHED_SECONDS * 50), "the watchdog never closed the page"
        raise PlaywrightError("Target page, context or browser has been closed")


def test_a_call_that_outlives_its_deadline_closes_the_page_and_names_the_recording_and_the_frame():
    """A page spinning its thread answers nothing, so the watchdog closes it and the call says why it ended."""
    close = Closing()
    with (
        pages.Watchdog.watching(close, name="section 3", call_seconds=WATCHED_SECONDS, whole_seconds=60) as dog,
        pytest.raises(ToolError) as raised,
        dog.call("frame 7 (second 0.28)"),
    ):
        close.stuck()
    assert close.times == 1
    assert str(raised.value) == (
        f"section 3 stopped answering at frame 7 (second 0.28): no answer in {WATCHED_SECONDS:g} seconds, "
        "so its page was closed."
    )


def test_calls_that_answer_in_time_never_close_the_page():
    """Each call has its own deadline, so many prompt calls together outlast one deadline and close nothing."""
    close = Closing()
    with pages.Watchdog.watching(close, name="section 1", call_seconds=WATCHED_SECONDS, whole_seconds=60) as dog:
        for _ in range(4):
            with dog.call("a prompt call"):
                time.sleep(WATCHED_SECONDS / 4)
    assert close.times == 0


def test_a_recording_past_its_whole_deadline_is_stopped_at_its_next_check():
    """A page that answers every call can still never get on, so the recording as a whole has a deadline too."""
    close = Closing()
    with pages.Watchdog.watching(close, name="section 2", call_seconds=60, whole_seconds=WATCHED_SECONDS) as dog:
        dog.check()
        time.sleep(WATCHED_SECONDS * 2)
        with pytest.raises(ToolError) as raised:
            dog.check()
    assert str(raised.value) == f"section 2 ran past its deadline of {WATCHED_SECONDS:g} seconds."
    assert close.times == 0


def test_a_call_in_flight_when_the_recording_runs_out_closes_the_page_naming_the_whole_deadline():
    """The call's own deadline is cut to the recording's end, which is the sentence it fails with."""
    close = Closing()
    with (
        pages.Watchdog.watching(close, name="section 4", call_seconds=60, whole_seconds=WATCHED_SECONDS) as dog,
        pytest.raises(ToolError) as raised,
        dog.call("its load"),
    ):
        close.stuck()
    assert str(raised.value).startswith(f"section 4 ran past its deadline of {WATCHED_SECONDS:g} seconds at its load")
