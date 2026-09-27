"""Driving a page nobody wrote for a recorder: the resources it owns, the deadline, and the seal."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import get_args

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import InputError, ToolError
from decktalk.media import browser
from decktalk.media.environment import browser_environment
from decktalk.media.origin import ORIGIN, Allowed, page_url
from decktalk.settings import BY_ID, COLOR_SCHEMES, PAGE_POLICIES, MotionConfig

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
        self.html = ""
        self.presented: list[float] = []  # what this machine answers the bias measurement with
        self.video = FakeVideo(context.directory / "page.webm")

    def on(self, event: str, handler: object) -> None:
        self.context.handlers.append((event, handler))

    def goto(self, url: str, **_kwargs: object) -> None:
        self.urls.append(url)

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
        self.asked: list[dict[str, object]] = []  # what each context was opened with

    def new_context(self, **kwargs: object) -> FakeContext:
        self.asked.append(kwargs)
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


def record(
    tmp_path: Path,
    sink: Sink,
    *,
    out: Path,
    fake: FakeBrowser | None = None,
    motion: MotionConfig | None = None,
) -> browser.Recording:
    return browser.record_page(
        fake or FakeBrowser(),
        page_url("deck/index.html"),
        0.5,
        out,
        allowed=Allowed.of(tmp_path, ["."]),
        log_sink=sink,
        settle_seconds=0.0,
        min_cover_seconds=0.0,
        width=960,
        height=540,
        color_scheme="dark",
        motion=motion or MotionConfig(),
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
    allowed = Allowed.of(tmp_path, ["."])
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
    browser.record_page(
        fake, page_url("deck/index.html"), 0.1, out, allowed=Allowed.of(tmp_path, ["."]), log_sink=Sink(out),
        settle_seconds=0.0, min_cover_seconds=0.0, width=960, height=540, color_scheme="dark", motion=MotionConfig(),
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
    with browser.chromium() as real:
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
    browser.record_page(
        fake, page_url("deck/index.html"), 0.1, out, allowed=Allowed.of(tmp_path, ["."]), log_sink=Sink(out),
        settle_seconds=0.0, min_cover_seconds=0.0, width=960, height=540, color_scheme="dark",
        motion=MotionConfig(), documents={"/__decktalk/cue-times.json": b'{"sections": []}'},
    )  # fmt: skip
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
    with browser.chromium() as real:
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
    with browser.chromium() as real:
        page, _assets = browser.open_page(real, allowed, width=320, height=240)
        for name in ("one.html", "two.html"):
            page.goto(page_url(f"deck/{name}"), wait_until="load")
            assert page.evaluate("() => typeof window.__dtprobe.report") == "function", name


def measuring(monkeypatch, presented: list[float]) -> None:
    """A launched browser whose one page answers the bias measurement with these milliseconds."""
    fake = FakeBrowser()

    @contextmanager
    def chromium(_browser_path: str = "") -> Iterator[FakeBrowser]:
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


@pytest.mark.browser
def test_this_machine_either_measures_a_bias_inside_the_published_range_or_says_it_cannot():
    """The setting is measured or it is zero, so a browser with nothing to say refuses rather than guesses."""
    bounds = BY_ID["host.presentation_bias_ms"].bounds
    try:
        measured = browser.measure_presentation_bias()
    except ToolError as refused:
        assert "reports no presentation times" in str(refused)
        return
    assert bounds is not None and bounds.ge <= measured <= bounds.le


# ---- the page policy ------------------------------------------------------------------------------


def test_the_page_policies_this_module_accepts_are_the_ones_the_setting_publishes():
    assert set(get_args(browser.PagePolicy)) == set(PAGE_POLICIES)
    with pytest.raises(InputError, match="page_policy"):
        browser.page_policy("mostly")


class Launcher:
    """Playwright's `chromium`, as far as a launch uses it: it records what it was asked and may refuse."""

    def __init__(self, *, refuse: bool = False, installed: bool = True) -> None:
        self.refuse = refuse
        self.asked: list[dict[str, object]] = []
        self.executable_path = __file__ if installed else "/nowhere/chromium"

    def launch(self, **options: object) -> Launched:
        self.asked.append(options)
        if self.refuse:
            raise PlaywrightError("No usable sandbox! Update your kernel.")
        return Launched()


class Launched:
    """A launched browser that opens pages which carry nothing and closes without a sound."""

    def new_page(self, **_kwargs: object) -> object:
        return object()

    def close(self) -> None:
        """There is no process behind it to stop."""


class Driver:
    def __init__(self, launcher: Launcher) -> None:
        self.chromium = launcher


def test_an_untrusted_page_gets_the_sandbox_a_proxy_that_answers_nothing_and_no_webrtc_udp():
    launcher = Launcher()
    browser.launch(Driver(launcher), policy=browser.UNTRUSTED)  # type: ignore[arg-type]
    asked = launcher.asked[0]
    assert asked["chromium_sandbox"] is True
    assert asked["proxy"] == {"server": browser.DEAD_PROXY, "bypass": browser.EVERY_HOST}
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in asked["args"]  # type: ignore[operator]


def test_a_trusted_page_keeps_the_machines_own_network_and_still_gets_a_scrubbed_environment():
    launcher = Launcher()
    browser.launch(Driver(launcher), policy=browser.TRUSTED)  # type: ignore[arg-type]
    asked = launcher.asked[0]
    assert "proxy" not in asked and "chromium_sandbox" not in asked
    assert asked["env"] == browser_environment()


def test_a_machine_that_cannot_run_the_sandbox_is_refused_and_never_falls_back(monkeypatch):
    """A fetch cannot give a machine a sandbox, so the refusal says what the machine needs instead."""
    monkeypatch.setattr(browser.chromium_fetch, "fetch_chromium", lambda: pytest.fail("must not fetch"))
    launcher = Launcher(refuse=True)
    with pytest.raises(ToolError, match="sandbox on") as raised:
        browser.launch(Driver(launcher), policy=browser.UNTRUSTED)  # type: ignore[arg-type]
    assert "seccomp" in (raised.value.hint or "")
    assert len(launcher.asked) == 1, "the sandbox was never dropped for a second try"


def test_every_page_a_browser_opens_is_routed_by_the_policy_it_was_launched_under(monkeypatch, tmp_path):
    seen: list[bool] = []
    monkeypatch.setattr(browser, "route_pages", lambda *_a, trusted, **_k: seen.append(trusted))
    monkeypatch.setattr(browser, "instrument", lambda page: page)

    @contextmanager
    def playwright() -> Iterator[Driver]:
        yield Driver(Launcher())

    monkeypatch.setattr(browser, "sync_playwright", playwright)
    allowed = Allowed.of(tmp_path, ["deck"])
    for policy in (browser.TRUSTED, browser.UNTRUSTED):
        with browser.chromium(policy=policy) as launched:
            browser.open_page(launched, allowed, width=10, height=10)  # type: ignore[arg-type]
    # A browser this module never launched is routed as a stranger's page.
    browser.open_page(Launched(), allowed, width=10, height=10)  # type: ignore[arg-type]
    assert seen == [True, False, False]


@pytest.mark.browser
def test_an_untrusted_page_reaches_nothing_through_any_channel_it_can_open(tmp_path):
    """Routing alone let a WebSocket open and WebRTC send STUN packets, so each channel is tried here.

    The listeners sit on this machine, so a channel that reached one is a channel that could reach a
    cloud metadata address from a render host.
    """
    hits: list[str] = []

    class Listener(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(f"http {self.path}")
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *_args: object) -> None:
            """Quiet, because the list above is the whole report."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Listener)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(0.2)

    def stun() -> None:
        while True:
            try:
                udp.recvfrom(2048)
                hits.append("udp")
            except TimeoutError:
                continue
            except OSError:
                return

    threading.Thread(target=stun, daemon=True).start()
    at = f"127.0.0.1:{server.server_address[1]}"
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
        with browser.chromium(policy=browser.UNTRUSTED) as real:
            page, assets = browser.open_page(real, Allowed.of(tmp_path, ["deck"]), width=400, height=300)
            page.goto(page_url("deck/index.html"), wait_until="load")
            assert page.evaluate("() => window.tried") is True
            time.sleep(0.5)
    finally:
        server.shutdown()
        server.server_close()
        udp.close()
    assert hits == []
    assert f"http://{at}" in assets.external
