"""Driving a page nobody wrote for a recorder: the resources it owns, the deadline, and the seal."""

from __future__ import annotations

import inspect
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import Cancelled, InputError, ToolError
from decktalk.media import browser
from decktalk.media.environment import child_environment
from decktalk.media.origin import ORIGIN, Allowed, page_url
from decktalk.settings import COLOR_SCHEMES, PAGE_POLICIES, MotionConfig
from support.fakes import BareBrowser, FakeChromium
from support.logs import data_of

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


@dataclass
class FakeVideo:
    """The webm Playwright writes when the recording context closes, which `Capture.place` moves."""

    path_: Path

    def path(self) -> str:
        return str(self.path_)


@dataclass
class Said:
    """One line a page wrote to its console, as Playwright hands it to a `console` handler."""

    type: str
    text: str


class FakePage:
    """A page that answers the four calls the recorder makes and remembers what it was asked."""

    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.closed = False
        self.html = ""
        self.presented: list[float] = []  # what this machine answers the bias measurement with
        self.video = FakeVideo(context.directory / "page.webm")

    def on(self, event: str, handler: object) -> None:
        self.context.handlers.append((event, handler))

    def goto(self, url: str, **_kwargs: object) -> None:
        self.urls.append(url)
        for event, handler in self.context.handlers:
            if event == "console":
                for line in self.context.said:
                    handler(line)  # type: ignore[operator]

    def evaluate(self, script: str) -> object:
        self.scripts.append(script)
        if browser.REPORT_JS in script:
            return dict(REPORTED)
        if "long-animation-frame" in script:
            return list(self.presented)
        return True if browser.HAS_CATALOG_JS in script else None

    def set_content(self, html: str) -> None:
        self.html = html

    def wait_for_timeout(self, _ms: float) -> None:
        """A recorder waits in real time and a test does not, so this passes the time by not spending it."""

    def screenshot(self, *, path: str) -> None:
        Path(path).write_bytes(b"png")

    def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, directory: Path, said: tuple[Said, ...] = ()) -> None:
        self.directory = directory
        self.said = said  # what every page of this context writes to its console once it has loaded
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
    def __init__(self, said: tuple[Said, ...] = ()) -> None:
        self.said = said
        self.contexts: list[FakeContext] = []
        self.asked: list[dict[str, object]] = []  # what each context was opened with

    def new_context(self, **kwargs: object) -> FakeContext:
        self.asked.append(kwargs)
        context = FakeContext(Path(str(kwargs["record_video_dir"])), self.said)
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


def record(
    tmp_path: Path, sink: Sink, *, out: Path, fake: FakeBrowser | None = None, seconds: float = 0.5, **extra: object
) -> browser.Recording:
    """One fake recording of the deck's page, with any option of `record_page` a test is about in `extra`."""
    options: dict[str, Any] = {
        "settle_seconds": 0.0,
        "min_cover_seconds": 0.0,
        "width": 960,
        "height": 540,
        "color_scheme": "dark",
        "motion": MotionConfig(),
        **extra,
    }
    allowed = Allowed.of(tmp_path, ["deck"])
    return browser.record_page(
        fake or FakeBrowser(), page_url("deck/index.html"), seconds, out, allowed=allowed, log_sink=sink, **options
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
    """A page that never loads used to leave a context and a webm behind and surface as a bug in DeckTalk."""
    fake = FakeBrowser()
    allowed = Allowed.of(tmp_path, ["deck"])
    with pytest.raises(PlaywrightError):
        with browser.capturing(
            fake, allowed, width=960, height=540, color_scheme="dark", motion=MotionConfig()
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
        with browser.capturing(
            Refuses(), Allowed.of(tmp_path, []), width=960, height=540, color_scheme="dark", motion=MotionConfig()
        ):
            pytest.fail("the context opened after all")
    assert str(raised.value).startswith("could not open a recording context (Browser closed")


def test_every_call_into_the_page_carries_a_deadline(tmp_path):
    """A deck's own script runs in the page's one thread, so a call with no deadline is a build with none."""
    out = tmp_path / "01.webm"
    fake = FakeBrowser()
    record(tmp_path, Sink(out), out=out, fake=fake, seconds=0.1)
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


def test_a_page_that_answers_neither_ready_nor_painted_is_taken_as_it_stands_and_says_so(caplog):
    """Both waits carry on rather than fail a frame, so the log is where a mistimed frame is explained."""

    class Hangs(FakePage):
        def evaluate(self, _script: str) -> object:
            raise PlaywrightError("Error: the page did not answer within 15 seconds")

    with caplog.at_level("DEBUG", logger="decktalk.media.browser"):
        browser.await_painted(Hangs(FakeContext(Path("."))))  # type: ignore[arg-type]
    said = [record.getMessage() for record in caplog.records if record.name == "decktalk.media.browser"]
    assert len(said) == 2
    assert "did not answer __dtprobe.ready()" in said[0] and "painted no frame" in said[1]


def test_a_context_the_recording_already_closed_is_passed_over_and_logged(caplog):
    """`place` closes the context on the way out, so a second close is expected and only noted."""
    with caplog.at_level("DEBUG", logger="decktalk.media.browser"), browser.suppressing_a_closed_context():
        raise PlaywrightError("Target page, context or browser has been closed\nCall log:")
    [record] = [record for record in caplog.records if record.name == "decktalk.media.browser"]
    assert (
        record.getMessage()
        == "the recording context was already closed (Target page, context or browser has been closed)"
    )


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


def test_the_colour_schemes_this_module_accepts_are_the_ones_the_setting_publishes():
    """Two spellings of one closed set, held equal here, because only one of them can be a type."""
    assert set(get_args(browser.ColorScheme)) == set(COLOR_SCHEMES)


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
    with browser.chromium(policy=browser.TRUSTED) as real:
        page, _assets = browser.open_page(real, Allowed.of(tmp_path, ["deck"]), width=400, height=300)
        page.goto(page_url("deck/index.html"), wait_until="load")
        assert page.evaluate("() => typeof window.__dtprobe.report") == "function"
        assert page.evaluate("() => window.__dtprobe.report().taken") is None
        report = browser.read_report(page, "deck")
        assert report.unreadable == () and report.warnings == () and report.catalog == ()


# ---- what a render is asked for -------------------------------------------------------------------


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


# ---- against a real browser -----------------------------------------------------------------------


def deck_of(tmp_path: Path, pages: dict[str, str]) -> Allowed:
    """A deck directory holding each named page, and the rule that serves it and nothing else."""
    deck = tmp_path / "deck"
    deck.mkdir(exist_ok=True)
    for name, body in pages.items():
        deck.joinpath(name).write_text(body, encoding="utf-8")
    return Allowed.of(tmp_path, ["deck"])


THROWS = "<!doctype html><meta charset=utf-8><title>t</title>\n<script>\nnotDefinedAnywhere();\n</script>"
BARE = "<!doctype html><meta charset=utf-8><title>t</title><p>no runtime here</p>"


@pytest.mark.browser
def test_a_page_that_throws_and_a_page_with_no_runtime_both_leave_page_errors(tmp_path):
    """The two ways a deck fails silently, which the recording log names so `verify` can judge them."""
    allowed = deck_of(tmp_path, {"broken.html": THROWS, "bare.html": BARE})
    with browser.chromium(policy=browser.TRUSTED) as real:
        thrown = record_real(real, allowed, tmp_path, "broken.html")
        assert any("ReferenceError: notDefinedAnywhere" in said for said in thrown.page_errors), thrown.page_errors
        assert any("(broken.html:3)" in said for said in thrown.page_errors), thrown.page_errors

        bare = record_real(real, allowed, tmp_path, "bare.html")
        assert bare.page_errors == (browser.NO_CATALOG,)
        assert (tmp_path / "bare.html.webm").exists(), "a page that failed is still recorded"


def record_real(real, allowed: Allowed, tmp_path: Path, page: str) -> browser.Recording:
    """One short real recording of a project page, with its log kept where a stage would keep it."""
    out = tmp_path / f"{page}.webm"
    return browser.record_page(
        real, page_url(f"deck/{page}"), 0.3, out, allowed=allowed, log_sink=Sink(out),
        settle_seconds=0.0, min_cover_seconds=0.0, width=320, height=240,
        color_scheme="no-preference", motion=MotionConfig(),
    )  # fmt: skip


@pytest.mark.browser
def test_the_probe_travels_with_a_page_across_every_url_it_is_driven_through(tmp_path):
    """An init script belongs to the page and not to a navigation, which is what `screenshots` relies on."""
    allowed = deck_of(tmp_path, {"one.html": BARE, "two.html": BARE})
    with browser.chromium(policy=browser.TRUSTED) as real:
        page, _assets = browser.open_page(real, allowed, width=320, height=240)
        for name in ("one.html", "two.html"):
            page.goto(page_url(f"deck/{name}"), wait_until="load")
            assert page.evaluate("() => typeof window.__dtprobe.report") == "function", name


def measuring(monkeypatch, presented: list[float]) -> None:
    """A launched browser whose one page answers the bias measurement with these milliseconds."""
    fake = FakeBrowser()

    @contextmanager
    def chromium(_browser_path: str = "", *, policy: str) -> Iterator[FakeBrowser]:
        assert policy == browser.TRUSTED, "the bias page is DeckTalk's own"
        yield fake

    def new_page(**_kwargs: object) -> FakePage:
        page = FakePage(FakeContext(Path(".")))
        page.presented = presented
        return page

    monkeypatch.setattr(fake, "new_page", new_page)
    monkeypatch.setattr(browser, "chromium", chromium)


def test_the_bias_is_the_middle_frame_rather_than_the_worst_one(monkeypatch):
    """One frame the machine held up moves the mean and moves nothing here, which is why it is the median."""
    measuring(monkeypatch, [11.0, 12.0, 13.0, 14.0, 900.0])
    assert browser.measure_presentation_bias() == 13.0


def test_a_browser_that_reports_no_presentation_time_is_told_rather_than_guessed_for(monkeypatch):
    measuring(monkeypatch, [])
    with pytest.raises(ToolError) as raised:
        browser.measure_presentation_bias()
    assert "reports no presentation times" in str(raised.value)


def test_the_measurement_is_written_from_the_two_constants_it_is_declared_with():
    script = browser.bias_script(browser.MEASURED_FRAMES, browser.MEASURED_FRAME_MS)
    assert f"let left = {browser.MEASURED_FRAMES};" in script
    assert f"performance.now() + {browser.MEASURED_FRAME_MS};" in script
    assert "FRAMES" not in script and "HOLD_MS" not in script


BIAS_LIMIT_MS = 200
"""How far from zero a real presentation bias sits, well past any display's own refresh interval."""


@pytest.mark.browser
def test_this_machine_either_measures_a_bias_inside_the_published_range_or_says_it_cannot():
    """A browser with nothing to say refuses rather than guesses."""
    try:
        measured = browser.measure_presentation_bias()
    except ToolError as refused:
        assert "reports no presentation times" in str(refused)
        return
    assert -BIAS_LIMIT_MS <= measured <= BIAS_LIMIT_MS


# ---- the page policy ------------------------------------------------------------------------------


def test_the_page_policies_this_module_accepts_are_the_ones_the_setting_publishes():
    assert set(get_args(browser.PagePolicy)) == set(PAGE_POLICIES)
    with pytest.raises(InputError, match="page_policy"):
        browser.page_policy("mostly")


INSTALLED = Path(__file__)
"""A file that is on disk, which is all a fake launch asks of the executable it would run."""


def test_an_untrusted_page_gets_the_sandbox_a_proxy_that_answers_nothing_and_no_webrtc_udp():
    chromium = FakeChromium(INSTALLED)
    browser.launch(chromium.driver(), policy=browser.UNTRUSTED)
    asked = chromium.asked[0]
    assert asked["chromium_sandbox"] is True
    assert asked["proxy"] == {"server": browser.DEAD_PROXY, "bypass": browser.EVERY_HOST}
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in asked["args"]  # type: ignore[operator]


def test_a_trusted_page_keeps_the_machines_own_network_and_still_gets_a_scrubbed_environment():
    chromium = FakeChromium(INSTALLED)
    browser.launch(chromium.driver(), policy=browser.TRUSTED)
    asked = chromium.asked[0]
    assert "proxy" not in asked and "chromium_sandbox" not in asked
    assert asked["env"] == child_environment()


def test_a_machine_that_cannot_run_the_sandbox_is_refused_and_never_falls_back(monkeypatch):
    """A fetch cannot give a machine a sandbox, so the refusal says what the machine needs instead."""
    monkeypatch.setattr(browser.chromium_fetch, "fetch_chromium", lambda: pytest.fail("must not fetch"))
    chromium = FakeChromium(INSTALLED, refusal="No usable sandbox! Update your kernel.")
    with pytest.raises(ToolError, match="sandbox on") as raised:
        browser.launch(chromium.driver(), policy=browser.UNTRUSTED)
    assert "seccomp" in (raised.value.hint or "")
    assert len(chromium.asked) == 1, "the sandbox was never dropped for a second try"


def test_every_page_a_browser_opens_is_routed_by_the_policy_it_was_launched_under(monkeypatch, tmp_path):
    seen: list[bool] = []
    monkeypatch.setattr(browser, "route_pages", lambda *_a, trusted, **_k: seen.append(trusted))
    monkeypatch.setattr(browser, "instrument", lambda page: page)
    monkeypatch.setattr(browser, "sync_playwright", FakeChromium(INSTALLED).started())
    allowed = Allowed.of(tmp_path, ["deck"])
    for policy in (browser.TRUSTED, browser.UNTRUSTED):
        with browser.chromium(policy=policy) as launched:
            browser.open_page(launched, allowed, width=10, height=10)  # type: ignore[arg-type]
    # A browser this module never launched is routed as a stranger's page.
    browser.open_page(BareBrowser(), allowed, width=10, height=10)  # type: ignore[arg-type]
    assert seen == [True, False, False]


@pytest.mark.parametrize("opener", [browser.chromium, browser.launch, browser.render_slate])
def test_no_launch_has_a_page_policy_to_fall_back_on(opener):
    """A default would be the policy of a caller that forgot one, which is the caller most likely to be wrong."""
    assert inspect.signature(opener).parameters["policy"].default is inspect.Parameter.empty


LATE_SECONDS = 2.0
"""How long a channel is given to land after the page has tried it, which the trusted control always meets."""

LOCAL_NETWORK_ACCESS_OFF = "--disable-features=LocalNetworkAccessChecks"
"""The switch that stops Chromium refusing a loopback address on its own, so the policy is what refuses it.

With Chromium's own check on, a page at the project's origin reaches no listener on this machine
under either policy, and a test of the policy would pass with the policy switched off.
"""

CHANNELS = {"/fetch", "/img", "/beacon", "/ws", "/worker", "udp"}
"""What the listeners record for each channel the page opens, one name per channel."""


@pytest.mark.browser
@pytest.mark.parametrize("policy", ["trusted", "untrusted"])
def test_an_untrusted_page_reaches_nothing_through_any_channel_it_can_open(tmp_path, monkeypatch, httpserver, policy):
    """Routing alone let a WebSocket open and WebRTC send STUN packets, so each channel is tried here.

    The listeners sit on this machine, so a channel that reached one is a channel that could reach a
    cloud metadata address from a render host. The trusted page is the control: every channel reaches
    its listener there, so a channel the untrusted page does not reach is one the policy refused.
    """
    launched = browser.launch_options

    def unchecked(chosen: str) -> dict[str, Any]:
        options = launched(chosen)
        return options | {"args": [*options.get("args", []), LOCAL_NETWORK_ACCESS_OFF]}

    monkeypatch.setattr(browser, "launch_options", unchecked)
    udp_hits: set[str] = set()

    def hits() -> set[str]:
        return {request.path for request, _ in httpserver.log} | udp_hits

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)

    def stun() -> None:
        while True:
            try:
                udp.recvfrom(2048)
                udp_hits.add("udp")
            except TimeoutError:
                continue
            except OSError:
                return

    threading.Thread(target=stun, daemon=True).start()
    at = f"127.0.0.1:{httpserver.port}"
    stun_at = f"127.0.0.1:{udp.getsockname()[1]}"
    deck = tmp_path / "deck"
    deck.mkdir()
    deck.joinpath("index.html").write_text(
        f"""<!doctype html><meta charset=utf-8><title>t</title><script>
window.tried = (async () => {{
  try {{ await fetch('http://{at}/fetch'); }} catch (e) {{}}
  await new Promise(ok => {{ const i = new Image(); i.onload = i.onerror = ok; i.src = 'http://{at}/img'; }});
  try {{ navigator.sendBeacon('http://{at}/beacon', 'x'); }} catch (e) {{}}
  await new Promise(ok => {{ try {{ const w = new WebSocket('ws://{at}/ws');
    w.onopen = w.onerror = () => ok(); setTimeout(ok, 1500); }} catch (e) {{ ok(); }} }});
  await new Promise(ok => {{ const src = "fetch('http://{at}/worker').then(() => postMessage(1), () => postMessage(0))";
    const w = new Worker(URL.createObjectURL(new Blob([src], {{ type: 'text/javascript' }})));
    w.onmessage = ok; setTimeout(ok, 2000); }});
  await new Promise(ok => {{ try {{
    const pc = new RTCPeerConnection({{ iceServers: [{{ urls: 'stun:{stun_at}' }}] }});
    pc.createDataChannel('x'); pc.createOffer().then(o => pc.setLocalDescription(o)); setTimeout(ok, 2000);
  }} catch (e) {{ ok(); }} }});
  return true;
}})();
</script>""",
        encoding="utf-8",
    )
    try:
        with browser.chromium(policy=policy) as real:
            page, assets = browser.open_page(real, Allowed.of(tmp_path, ["deck"]), width=400, height=300)
            page.goto(page_url("deck/index.html"), wait_until="load")
            assert page.evaluate("() => window.tried") is True
            # A beacon and a STUN packet may land after the page is done, so both sides wait as long.
            deadline = time.monotonic() + LATE_SECONDS
            while hits() != CHANNELS and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        udp.close()
    assert hits() == (CHANNELS if policy == "trusted" else set())
    assert f"http://{at}" in assets.external


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

    browser.waited(Clock(), 2.5, lambda: None)  # type: ignore[arg-type]
    assert waits == [1000.0, 1000.0, 500.0]


def test_a_frozen_frame_is_taken_once_the_page_is_ready_and_has_painted_twice(tmp_path):
    """A fixed 400 ms settle was most of `check`, and ready() and two frames are what a frame needs."""
    waits: list[float] = []

    class Timed(FakePage):
        def wait_for_timeout(self, ms: float) -> None:
            waits.append(ms)

    page = Timed(FakeContext(tmp_path))
    browser.screenshot(page, page_url("deck/index.html"), tmp_path / "a.png")  # type: ignore[arg-type]
    order = [script for script in page.scripts if browser.READY_JS in script or browser.PAINTED_JS in script]
    assert [browser.READY_JS in script for script in order] == [True, False]
    assert waits == [], "no fixed settle is spent on top of ready() and two painted frames"


def test_a_recorded_pages_own_errors_and_warnings_are_collected_and_its_chatter_is_not(tmp_path) -> None:
    """A page's console.error and console.warn are the author's diagnostics, and nothing recorded them."""

    class Page:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def on(self, event: str, handler: object) -> None:
            self.handlers[event] = handler

        def goto(self, _url: str, **_kwargs: object) -> None:
            for kind, text in (("log", "chatter"), ("warning", "a slow font"), ("error", "no cue 2.1")):
                self.handlers["console"](Said(kind, text))  # type: ignore[operator]

    page = Page()
    context = type("Context", (), {"new_page": lambda _self: page})()
    capture = browser.Capture(context=context, assets=None, directory=tmp_path, opened=0.0)  # type: ignore[arg-type]
    console: list[tuple[str, str]] = []
    capture.open("http://project.localhost/deck/", [], console)
    assert console == [("warning", "a slow font"), ("error", "no cue 2.1")]
