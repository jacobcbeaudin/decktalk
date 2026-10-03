"""Recording a page section: the resources a recording owns, the order its log is written in, and a stop."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from decktalk.errors import Cancelled, ToolError
from decktalk.media import browser, pagereport, pages, recording
from decktalk.media.origin import ORIGIN, Allowed, Assets, page_url
from decktalk.settings import MotionConfig
from support import recorder as recording_fakes
from support.logs import data_of
from support.recorder import BARE, THROWS, FakeBrowser, FakeContext, Said, Sink, deck_of, record


def test_the_log_is_cleared_before_anything_is_captured_and_written_once_the_webm_is_in_place(tmp_path):
    """A webm replaced under an older log keeps its digest and moves t=0, which is what this order prevents."""
    out = tmp_path / "build" / "recordings" / "01.webm"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"the recording from yesterday")
    sink = Sink(out)
    recording = record(tmp_path, sink, out=out)
    assert [name for name, _ in sink.moments] == ["clear", "write"]
    assert sink.moments[0] == ("clear", True)  # the old pair is still whole when its log goes
    assert sink.moments[1] == ("write", True)  # and the log is written only once the new webm is there
    assert out.read_bytes() == b"webm"
    assert sink.written is recording


def test_the_recording_carries_what_the_page_said_and_what_it_loaded(tmp_path, caplog):
    """The page's console.error and console.warn lines have no field on the recording, so the log is theirs."""
    out = tmp_path / "01.webm"
    fake = FakeBrowser(said=(Said("log", "chatter"), Said("error", "no cue 2.1")))
    with caplog.at_level("DEBUG", logger="decktalk"):
        recording = record(tmp_path, Sink(out), out=out, fake=fake)
    assert recording.url.startswith(ORIGIN)
    assert recording.report.warnings[0].code.name == "PAGE_CUE_UNKNOWN"
    assert recording.report.frame_gaps[0].ms == 150
    assert recording.page_errors == ()
    assert recording.requested_seconds == 0.5
    kept = [record for record in caplog.records if "console" in record.getMessage()]
    assert [data_of(record) for record in kept] == [{"kind": "error", "text": "no cue 2.1"}]
    assert [record.levelname for record in kept] == ["WARNING"]


def test_the_temporary_directory_and_the_context_go_however_the_recording_ends(tmp_path):
    """A page that never loads leaves no context or webm behind, and is not reported as a bug in DeckTalk."""
    fake = FakeBrowser()
    allowed = Allowed.of(tmp_path, ["deck"])
    with pytest.raises(PlaywrightError):
        with recording.capturing(
            fake.opened(), allowed, width=960, height=540, color_scheme="dark", motion=MotionConfig()
        ) as capture:
            directory = capture.directory
            assert directory.is_dir()
            raise PlaywrightError("Target page, context or browser has been closed")
    assert fake.contexts[0].closed
    assert not directory.exists()


def test_a_browser_that_will_not_do_something_is_a_tool_failure_and_not_a_bug(tmp_path):
    class Refuses(FakeBrowser):
        def new_context(self, **_kwargs: object) -> FakeContext:
            raise PlaywrightError("Browser closed\nCall log:\n  - launching")

    with pytest.raises(ToolError) as raised:
        with recording.capturing(
            Refuses().opened(),
            Allowed.of(tmp_path, []),
            width=960,
            height=540,
            color_scheme="dark",
            motion=MotionConfig(),
        ):
            pytest.fail("the context opened after all")
    assert str(raised.value).startswith("could not open a recording context (Browser closed")


def test_a_context_the_recording_already_closed_is_passed_over_and_logged(caplog):
    """`place` closes the context on the way out, so a second close is expected and only noted."""
    with caplog.at_level("DEBUG", logger="decktalk.media.recording"), recording.suppressing_a_closed_context():
        raise PlaywrightError("Target page, context or browser has been closed\nCall log:")
    [record] = [record for record in caplog.records if record.name == "decktalk.media.recording"]
    assert (
        record.getMessage()
        == "the recording context was already closed (Target page, context or browser has been closed)"
    )


def test_reduced_motion_is_asked_of_chromium_rather_than_written_into_the_page(tmp_path):
    """It is a media feature, so a preview on a machine that asks for it gets the render the build made."""
    fake = FakeBrowser()
    out = tmp_path / "01.webm"
    record(tmp_path, Sink(out), out=out, fake=fake, motion=MotionConfig(reduce=True))
    assert fake.asked[0]["reduced_motion"] == "reduce"

    plain = FakeBrowser()
    record(tmp_path, Sink(out), out=out, fake=plain, motion=MotionConfig())
    assert plain.asked[0]["reduced_motion"] == "no-preference"


def test_the_motion_scale_reaches_the_page_as_one_declaration_on_the_root(tmp_path):
    """It multiplies every length the sheet plays, which no query key could carry."""
    fake = FakeBrowser()
    out = tmp_path / "01.webm"
    record(tmp_path, Sink(out), out=out, fake=fake, motion=MotionConfig(scale=1.5))
    [declaring] = [s for s in fake.contexts[0].scripts if "--dt-motion-scale" in s]
    assert ":root{--dt-motion-scale:1.5}" in declaring
    # The tag goes in once the page's own sheets are there, so the setting decides and not the deck.
    assert "DOMContentLoaded" in declaring

    unscaled = FakeBrowser()
    record(tmp_path, Sink(out), out=out, fake=unscaled, motion=MotionConfig())
    assert not [s for s in unscaled.contexts[0].scripts if "--dt-motion-scale" in s]


def test_a_document_the_caller_answers_itself_is_never_a_recorded_asset(tmp_path):
    """The cue times a run resolved are its own output, so a recording keyed on them keys on itself."""
    fake = FakeBrowser()
    out = tmp_path / "01.webm"
    record(tmp_path, Sink(out), out=out, fake=fake, seconds=0.1, documents={"/__decktalk/cue-times.json": b"{}"})
    assert fake.contexts[0].routes == ["**/*"]


@pytest.mark.browser
def test_a_page_that_throws_and_a_page_with_no_runtime_both_leave_page_errors(tmp_path):
    """The two ways a deck fails silently, which the recording log names so `verify` can judge them."""
    allowed = deck_of(tmp_path, {"broken.html": THROWS, "bare.html": BARE})
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        thrown = record_real(real, allowed, tmp_path, "broken.html")
        assert any("ReferenceError: notDefinedAnywhere" in said for said in thrown.page_errors), thrown.page_errors
        assert any("(broken.html:3)" in said for said in thrown.page_errors), thrown.page_errors

        bare = record_real(real, allowed, tmp_path, "bare.html")
        assert bare.page_errors == (pages.NO_CATALOG,)
        assert (tmp_path / "bare.html.webm").exists(), "a page that failed is still recorded"


def record_real(real, allowed: Allowed, tmp_path: Path, page: str) -> pagereport.Recording:
    """One short real recording of a project page, with its log kept where a stage would keep it."""
    out = tmp_path / f"{page}.webm"
    return recording.record_page(
        real, page_url(f"deck/{page}"), 0.3, out, allowed=allowed, log_sink=Sink(out),
        settle_seconds=0.0, cover_min_seconds=0.0, width=320, height=240,
        color_scheme="no-preference", motion=MotionConfig(),
    )  # fmt: skip


def test_a_recording_asks_whether_to_stop_at_least_once_a_second_and_stops_whole(tmp_path):
    """A recording slept its whole span, so a stopped run waited for every section already recording."""
    asked: list[int] = []

    def check() -> None:
        asked.append(1)
        if len(asked) == 3:
            raise Cancelled("the caller stopped this run")

    out = tmp_path / "01.webm"
    sink = Sink(out)
    with pytest.raises(Cancelled):
        record(tmp_path, sink, out=out, seconds=10.0, check=check)
    assert len(asked) == 3
    assert not out.exists() and sink.moments == [("clear", False)], "nothing is placed and no log is written"


def test_the_slices_of_a_wait_add_up_to_the_span_exactly():
    waits: list[float] = []

    class Clock:
        def wait_for_timeout(self, ms: float) -> None:
            waits.append(ms)

    recording.waited(cast("Page", Clock()), 2.5, lambda: None)
    assert waits == [1000.0, 1000.0, 500.0]


def test_a_recorded_pages_own_errors_and_warnings_are_collected_and_its_chatter_is_not(tmp_path) -> None:
    """A page's console.error and console.warn are the author's diagnostics, and nothing recorded them."""
    said = (Said("log", "chatter"), Said("warning", "a slow font"), Said("error", "no cue 2.1"))
    context = FakeContext(tmp_path, said)
    capture = recording.Capture(context=context.context(), assets=Assets(tmp_path), directory=tmp_path, opened=0.0)
    console: list[tuple[str, str]] = []
    capture.open("http://project.localhost/deck/", [], console)
    assert console == [("warning", "a slow font"), ("error", "no cue 2.1")]


def test_a_page_that_stops_while_it_is_recorded_is_a_tool_failure_and_not_a_bug(tmp_path, monkeypatch):
    """A tab that crashes mid-recording fails the wait, and the reader is sent to the page, not to DeckTalk."""

    def crashed(_page: object, _ms: float, /) -> None:
        raise PlaywrightError("Target crashed\nCall log:")

    monkeypatch.setattr(recording_fakes.FakePage, "wait_for_timeout", crashed)
    out = tmp_path / "01.webm"
    with pytest.raises(ToolError, match=r"^the page stopped while 01\.webm was recorded \(Target crashed\)"):
        record(tmp_path, Sink(out), out=out)
