"""Driving a page nobody wrote for a recorder: the resources it owns, the deadline, and the seal."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import InputError, ToolError
from decktalk.media import browser
from decktalk.media.origin import ORIGIN, page_url

REPORTED = {
    "version": "0.5.0",
    "mode": "cue",
    "scene": "intro",
    "slide": "1.1",
    "warnings": [{"code": "PAGE_CUE_UNKNOWN", "message": "no such cue", "cue": "1.1:nope"}],
    "catalog": [{"scene": "intro"}],
    "cues": [],
    "words": [],
    "frameGaps": [{"at": 0.4, "ms": 150}],
    "longFrames": [],
}
"""What the page answers `window.__dtprobe.report()` with, in the shape the contract names."""


class FakeVideo:
    def __init__(self, path: Path) -> None:
        self._path = path

    def path(self) -> str:
        return str(self._path)


class FakePage:
    """A page that answers the four calls the recorder makes and remembers what it was asked."""

    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.closed = False
        self.video = FakeVideo(context.directory / "page.webm")

    def on(self, event: str, handler: object) -> None:
        self.context.handlers.append((event, handler))

    def goto(self, url: str, **_kwargs: object) -> None:
        self.urls.append(url)

    def evaluate(self, script: str) -> object:
        self.scripts.append(script)
        if browser.REPORT_JS in script:
            return dict(REPORTED)
        return True if browser.HAS_CATALOG_JS in script else None

    def wait_for_timeout(self, _ms: float) -> None:
        """A recorder waits in real time and a test does not, so this passes the time by not spending it."""

    def screenshot(self, *, path: str) -> None:
        Path(path).write_bytes(b"png")

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.scripts: list[str] = []
        self.routes: list[str] = []
        self.handlers: list[tuple[str, object]] = []
        self.page: FakePage | None = None
        self.closed = False

    def add_init_script(self, script: str) -> None:
        self.scripts.append(script)

    def route(self, pattern: str, _handler: object) -> None:
        self.routes.append(pattern)

    def new_page(self) -> FakePage:
        self.page = FakePage(self)
        return self.page

    def close(self) -> None:
        """Playwright writes the video when the context closes, which is the order this stands in for."""
        if not self.closed and self.page:
            Path(self.page.video.path()).write_bytes(b"webm")
        self.closed = True


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []

    def new_context(self, **kwargs: object) -> FakeContext:
        context = FakeContext(Path(str(kwargs["record_video_dir"])))
        self.contexts.append(context)
        return context

    def new_page(self, **_kwargs: object) -> FakePage:
        return FakePage(FakeContext(Path(".")))


class Sink:
    """A recording log, standing in for the artifact writer, which remembers when it was called."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.moments: list[tuple[str, bool]] = []  # each call, and whether the webm was in place by then
        self.written: browser.Recording | None = None

    def clear(self) -> None:
        self.moments.append(("clear", self.out.exists()))

    def write(self, recording: browser.Recording) -> None:
        self.moments.append(("write", self.out.exists()))
        self.written = recording


def record(tmp_path: Path, sink: Sink, *, out: Path, fake: FakeBrowser | None = None) -> browser.Recording:
    return browser.record_page(
        fake or FakeBrowser(),
        page_url("deck/index.html"),
        0.5,
        out,
        root=tmp_path,
        log_sink=sink,
        settle_seconds=0.0,
        min_cover_seconds=0.0,
        width=960,
        height=540,
        color_scheme="dark",
    )


def test_page_error_text_keeps_the_message_and_the_file_and_line():
    class Err:
        name = "SyntaxError"
        message = "Identifier 'SCENES_TOTAL' has already been declared"
        stack = (
            "SyntaxError: Identifier 'SCENES_TOTAL' has already been declared\n    at file:///p/deck/index.html:120:7"
        )

    told = browser.page_error_text(Err())
    assert told == "SyntaxError: Identifier 'SCENES_TOTAL' has already been declared (index.html:120)"

    class Bare:
        message = "boom"
        stack = ""

    assert browser.page_error_text(Bare()) == "boom"


def test_the_log_is_cleared_before_anything_is_captured_and_written_once_the_webm_is_in_place(tmp_path):
    """A webm replaced under an older log keeps its hash and moves t=0, which is what this order prevents."""
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


def test_the_recording_carries_what_the_page_said_and_what_it_loaded(tmp_path):
    out = tmp_path / "01.webm"
    recording = record(tmp_path, Sink(out), out=out)
    assert recording.url.startswith(ORIGIN)
    assert recording.report.warnings[0].code.name == "PAGE_CUE_UNKNOWN"
    assert recording.report.frame_gaps[0].ms == 150
    assert recording.page_errors == ()
    assert recording.requested_seconds == 0.5


def test_the_temporary_directory_and_the_context_go_however_the_recording_ends(tmp_path):
    """A page that never loads used to leave a context and a webm behind and surface as a bug in DeckTalk."""
    fake = FakeBrowser()
    with pytest.raises(PlaywrightError):
        with browser.capturing(fake, tmp_path, width=960, height=540, color_scheme="dark") as capture:
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
        with browser.capturing(Refuses(), tmp_path, width=960, height=540, color_scheme="dark"):
            pytest.fail("the context opened after all")
    assert str(raised.value).startswith("could not open a recording context (Browser closed")


def test_every_call_into_the_page_carries_a_deadline(tmp_path):
    """A deck's own script runs in the page's one thread, so a call with no deadline is a build with none."""
    out = tmp_path / "01.webm"
    fake = FakeBrowser()
    browser.record_page(
        fake, page_url("deck/index.html"), 0.1, out, root=tmp_path, log_sink=Sink(out),
        settle_seconds=0.0, min_cover_seconds=0.0, width=960, height=540, color_scheme="dark",
    )  # fmt: skip
    page = fake.contexts[0].page
    assert page is not None and page.scripts
    for script in page.scripts:
        assert "Promise.race" in script, script
        assert f"{browser.DEADLINE_SECONDS * 1000:.0f}" in script, script


def test_a_call_the_page_never_answered_is_a_tool_failure():
    class Hangs(FakePage):
        def evaluate(self, _script: str) -> object:
            raise PlaywrightError("Error: the page did not answer within 15 seconds")

    page = Hangs(FakeContext(Path(".")))
    with pytest.raises(ToolError) as raised:
        browser.evaluate(page, browser.REPORT_JS)
    assert str(raised.value).startswith("the page could not answer")


def test_a_page_that_cannot_report_leaves_a_report_that_says_so():
    class Hangs(FakePage):
        def evaluate(self, _script: str) -> object:
            raise PlaywrightError("Execution context was destroyed")

    report = browser.read_report(Hangs(FakeContext(Path("."))), "01")
    assert report.warnings == () and report.unreadable


def test_the_probe_is_sealed_onto_the_window_before_the_page_runs(tmp_path):
    """A deck shares its window with the recorder's instrument, so the instrument is not the deck's to take."""
    fake = FakeBrowser()
    out = tmp_path / "01.webm"
    record(tmp_path, Sink(out), out=out, fake=fake)
    scripts = fake.contexts[0].scripts
    assert scripts[0] == browser.PROBE_JS
    assert scripts[1] == browser.SEAL_JS
    assert "writable: false" in browser.SEAL_JS and "configurable: false" in browser.SEAL_JS


def test_a_colour_scheme_chromium_does_not_know_is_refused_rather_than_passed_on():
    with pytest.raises(InputError) as raised:
        browser.scheme("sepia")
    assert "no-preference" in str(raised.value)
    assert browser.scheme("dark") == "dark"


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
    with browser.chromium() as real:
        page, _assets = browser.open_page(real, tmp_path, width=400, height=300)
        page.goto(page_url("deck/index.html"), wait_until="load")
        assert page.evaluate("() => typeof window.__dtprobe.report") == "function"
        assert page.evaluate("() => window.__dtprobe.report().taken") is None
        report = browser.read_report(page, "deck")
        assert report.unreadable == () and report.warnings == () and report.catalog == ()
