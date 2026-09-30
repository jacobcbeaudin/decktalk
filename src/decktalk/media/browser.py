"""Headless Chromium through Playwright: recording a page, taking screenshots and drawing slates.

This is the only module that launches a browser, and therefore the one place that fetches one: a
machine without Chromium gets it here, the first time a command needs it, the way `ffmpeg.py` gets
ffmpeg. It waits for the page to say it is ready and then asks it for one report, so a stage above
asks for a recording or a frame and never for a browser.

Every page it opens is served from the local origin in `origin.py`, so a page may fetch a file
beside it and import a module, and the recorder learns which files the page actually loaded.

Two rules hold this module to a page it does not trust. Every call into the page carries a deadline,
because a deck's own script runs in the same thread and a page that never answers would otherwise
hold a build for as long as it cared to. And the probe is sealed onto the window before any script
of the page runs, so the measurements come from the instrumentation the recorder injected.

`[record] page_policy` decides how far the page itself is trusted, and every launch reads it, so
`check`, `storyboard` and the poster follow the same policy as `record`. A trusted page is the
author's own work and reaches the network as it would in the author's browser. An untrusted page is
a stranger's. Its Chromium runs with the sandbox on and refuses to start without it, its requests
off the origin are aborted by the router, and its browser is pointed at a proxy that answers nothing,
which closes the channels routing never sees: a WebSocket, a DNS lookup and a WebRTC probe. Under
both policies the browser is given the environment `environment.py` builds rather than the
process's own, so a key the host holds never reaches the process that runs a page's script.
"""

from __future__ import annotations

import html
import logging
import re
import shutil
import statistics
import tempfile
import time
import weakref
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from ..errors import InputError, ToolError
from ..page import MOTION_SCALE_PROPERTY
from ..settings import COLOR_SCHEMES, PAGE_POLICIES, MotionConfig
from ..toolchain import chromium_fetch
from ..toolchain.assets import probe_path
from . import MILLISECONDS, pagereport
from .encode import css_color
from .environment import child_environment
from .origin import Allowed, Assets, route_pages
from .pagereport import PageReport, Recording

log = logging.getLogger(__name__)

# decktalk-probe.js is the instrumentation every command needs from a page and no page carries:
# the magenta cover over the first paint, the helper that says when a page has settled, the
# measured catalog's boxes and the freeze that stops at one cue. It is added as an init script, so
# it runs before the page's own scripts and the runtime finds it, and it is never a <script src>
# in a deck. The cover is drawn only where it is asked for, so a screenshot never shows it.
PROBE_JS = probe_path().read_text(encoding="utf-8")
# The probe is the recorder's own instrument, so a deck cannot take its name once it is on the
# window. This runs as the init script after the probe's own and before any script of the page.
SEAL_JS = """(() => {
  const probe = window.__dtprobe;
  const own = Object.getOwnPropertyDescriptor(window, "__dtprobe");
  if (!probe || (own && own.writable === false)) return;
  Object.freeze(probe);
  Object.defineProperty(window, "__dtprobe", { value: probe, writable: false, configurable: false });
})()"""
COVER_JS = "() => window.__dtprobe.cover()"
# How much this render slows every declared length down, which the runtime reads off the root. The
# tag is appended once the page's own sheets are in the head, so the setting decides and not a deck.
MOTION_JS = """(() => {
  const add = () => {
    const style = document.createElement("style");
    style.id = "dt-motion";
    style.textContent = ":root{%s:%s}";
    (document.head || document.documentElement).appendChild(style);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", add, { once: true });
  else add();
})()"""
# Remove the cover, then start the page clock on the next animation frame.
START_JS = "() => window.__dtprobe.lift()"
READY_JS = "() => window.__dtprobe.ready()"
# Two animation frames after the page is ready, so what the page drew in answer to ready() has been
# through layout and paint before a frozen frame is taken.
PAINTED_JS = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => done(true))))"
REPORT_JS = "() => window.__dtprobe.report()"
# Whether the runtime is present and the page registered at least one scene.
HAS_CATALOG_JS = "() => !!(window.__decktalk && window.__decktalk.catalog && window.__decktalk.catalog.length)"
NO_CATALOG = "no window.__decktalk.catalog (is decktalk-runtime.js included, and does the page register a scene?)"

CHECK_SECONDS = 1.0
"""Calibration: how often a recording asks whether it should stop, which is as long as a person waits on a stop."""

DEADLINE_SECONDS = 15.0
"""Calibration: many times the longest a probe call measures, so only a page that stopped answering hits it."""

ColorScheme = Literal["light", "dark", "no-preference"]
"""What a page may be told the viewer prefers, which is the closed set `[record] color_scheme` publishes."""


UNSCALED = 1.0
"""Truth: the multiplier that changes no length, which is a page left exactly as it was written."""


def motion_scripts(motion: MotionConfig) -> list[str]:
    """What `[motion]` asks a page for, as the scripts that put it there before the page's own run.

    Reduced motion is a media feature rather than an attribute, so Chromium is asked for it and the
    same render is what a person who asked their own machine for less motion sees in a preview. The
    scale is a custom property, because it multiplies every length the sheet plays and no query key
    could carry it.
    """
    return [] if motion.scale == UNSCALED else [MOTION_JS % (MOTION_SCALE_PROPERTY, f"{motion.scale:g}")]


def scheme(value: str) -> ColorScheme:
    """The colour scheme a page is opened under, refused here rather than handed to Chromium unread.

    A setting Chromium does not know is a project file that says something untrue about the render,
    and the browser takes it silently, so the one place that passes it on is the place that reads it.
    """
    return _one_of("color_scheme", value, COLOR_SCHEMES)


def _one_of[S: str](key: str, value: str, choices: tuple[S, ...]) -> S:
    """One closed `[record]` key's value, refused unless it is a settings choice, whose own entry it returns."""
    if value not in choices:
        raise InputError(
            f"[record] {key} = {value!r} is not one of {', '.join(choices)}.",
            hint=f"Set it to one of {', '.join(choices)}.",
        )
    return choices[choices.index(value)]


SLATE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;background:{bg};color:#f4f6f8;
font-family:Inter,-apple-system,Helvetica,Arial,sans-serif;overflow:hidden}}
.wrap{{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 160px;gap:28px}}
.eyebrow{{font-size:30px;color:#9aa4b2;letter-spacing:.08em;text-transform:uppercase}}
.title{{font-size:96px;font-weight:700;letter-spacing:-2px;line-height:1.05}}
.sub{{font-size:44px;color:#9aa4b2}}
.foot{{position:absolute;left:160px;bottom:120px;font-size:30px;color:#5b6573}}
</style></head><body><div class="wrap">
<div class="eyebrow">{eyebrow}</div><div class="title">{title}</div><div class="sub">{sub}</div>
</div><div class="foot">{foot}</div></body></html>"""


PagePolicy = Literal["trusted", "untrusted"]
"""How far a page is trusted, which is the closed set `[record] page_policy` publishes."""

TRUSTED: PagePolicy = "trusted"
UNTRUSTED: PagePolicy = "untrusted"

DEAD_PROXY = "http://127.0.0.1:9"
"""Truth: the discard port on this machine, where no proxy answers, so a request sent to it goes nowhere.

The router answers the project's origin before the network stack sees a request, so the proxy is
reached only by what routing cannot stop, which is every channel an untrusted page must not have.
"""

EVERY_HOST = "<-loopback>"
"""Truth: Chromium's word for taking loopback off its implicit bypass list, so no host skips the proxy."""

UNTRUSTED_ARGS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp",)
"""What an untrusted page's Chromium is started with, which keeps WebRTC from sending UDP past the proxy."""

SANDBOX_HINT = (
    "An untrusted page runs only inside Chromium's sandbox. In a container that means a user other than "
    "root and a seccomp profile that allows user namespaces."
)
"""What a machine that cannot start the sandbox is told, because the sandbox is a property of the machine."""


def page_policy(value: str) -> PagePolicy:
    """The policy a page is opened under, refused here rather than read as the weaker of the two.

    An unknown value is a project file that says something untrue about the render, and reading it as
    trusted would open the network to a page whose host meant to close it.
    """
    return _one_of("page_policy", value, PAGE_POLICIES)


def launch_options(policy: PagePolicy) -> dict[str, Any]:
    """The keyword arguments a launch under `policy` passes to Playwright, beyond the executable.

    The environment is scrubbed under both policies. The sandbox, the proxy and the WebRTC switch are
    the untrusted policy's alone, because a trusted page is a deck on its author's own machine and
    the machine's own proxy is the one it should use.
    """
    options: dict[str, Any] = {"env": child_environment()}
    if policy == UNTRUSTED:
        options |= {
            "chromium_sandbox": True,
            "proxy": {"server": DEAD_PROXY, "bypass": EVERY_HOST},
            "args": list(UNTRUSTED_ARGS),
        }
    return options


_POLICIES: weakref.WeakKeyDictionary[Browser, PagePolicy] = weakref.WeakKeyDictionary()
"""The policy each open browser was launched under, which every page it opens is routed by."""


def trusts(browser: Browser) -> bool:
    """Whether a browser was launched for a trusted page, which a browser this module never launched was not."""
    return _POLICIES.get(browser) == TRUSTED


class RecordingSink(Protocol):
    """Where the log of one recording is kept, which the recorder clears before it captures and fills after.

    The pair on disk has to be complete or absent. A webm replaced under the log of the recording
    before it keeps its hash and moves narration t=0, so the next assemble trims the new picture at
    the old moment and every reveal in the section lands wrong. Clearing first and writing last
    leaves a crash with no log, which the next run reads as a section it has not recorded.
    """

    def clear(self) -> None:
        """Delete the log of the recording that is about to be replaced, before anything is captured."""

    def write(self, recording: Recording) -> None:
        """Write the log of the recording now on disk, once the webm is in place."""


@contextmanager
def driving(what: str) -> Iterator[None]:
    """Turn a browser that would not do something into a `ToolError` saying what it would not do.

    A page that never loads, a context that will not open and a call the page never answered are all
    the tool failing, and reporting them as INTERNAL would send a reader looking for a bug in
    DeckTalk instead of at their own deck or their own machine.
    """
    try:
        yield
    except PlaywrightError as exc:
        raise ToolError(f"{what} ({str(exc).splitlines()[0]}).") from exc


@contextmanager
def chromium(browser_path: str = "", *, policy: str) -> Iterator[Browser]:
    """A launched headless Chromium, as the machine and the page policy configure it, closed on exit.

    Under the trusted policy no proxy argument is passed. Request routing answers the local origin
    before the network stack reaches it, so no proxy ever sees that host, and every other request a
    recorded page makes goes the way the machine sends it, through its own proxy and its own logging.
    Under the untrusted policy the browser is sealed as `launch_options` says.

    `browser_path` is `[record] browser_path`, the executable a machine that manages its own
    Chromium names. It is empty on a machine DeckTalk fetches the browser for, which is where
    `launch` fetches it. `policy` is `[record] page_policy`, which every caller that opens a
    project's page passes on. It has no default, because a default would be the policy a caller that
    forgot it gets, and a caller that forgot it is the one most likely to open a stranger's page.
    """
    with sync_playwright() as pw:
        browser = launch(pw, browser_path, policy=policy)
        _POLICIES[browser] = page_policy(policy)
        try:
            yield browser
        finally:
            browser.close()


def launch(pw: Playwright, browser_path: str = "", *, policy: str) -> Browser:
    """A launched Chromium, fetching the build Playwright manages when this machine has not got it.

    This is the one place a browser starts, so every command gets the browser it needs without
    anyone running an install step first, the way `ffmpeg_paths` gets ffmpeg. The fetch downloads
    Chromium alone and never its system libraries, because installing those goes through sudo and a
    build that stops for a root password is a build that hangs in a script and in CI. When Chromium
    still will not launch after it has been fetched, those libraries are what is missing, and the
    error says to run `decktalk install`, which is the one command that may ask for a password.

    A machine that names its own executable is told about that executable instead. Fetching would
    not help it: the next launch would use the same path again. An untrusted page whose Chromium is
    on disk and will not start is a machine that cannot run the sandbox, which a fetch does not
    change either, so it is refused and never started without one.
    """
    sealed = page_policy(policy)
    options = launch_options(sealed)
    started = time.monotonic()
    try:
        return _launched(pw.chromium.launch(executable_path=browser_path or None, **options), sealed, started)
    except PlaywrightError as exc:
        said = str(exc).splitlines()[0]
        if browser_path:
            raise ToolError(
                f"could not launch the Chromium at {browser_path} ({said}).",
                hint="[record] browser_path names it. Clear that setting to use the build DeckTalk fetches.",
            ) from exc
        if sealed == UNTRUSTED and chromium_fetch.installed_chromium(pw) is not None:
            raise ToolError(f"could not launch Chromium with its sandbox on ({said}).", hint=SANDBOX_HINT) from exc
    # A launch that failed with no executable named falls through to here, which is the fetch.
    if chromium_fetch.installed_chromium(pw) is not None:
        log.info(
            "Chromium is on this machine and did not launch, so the build is being fetched again.",
            extra={"data": {"reason": said}},
        )
    chromium_fetch.fetch_chromium(env=child_environment())
    started = time.monotonic()
    try:
        return _launched(pw.chromium.launch(**options), sealed, started)
    except PlaywrightError as exc:
        raise ToolError(
            f"Chromium was fetched and still would not launch ({str(exc).splitlines()[0]}).",
            hint=SANDBOX_HINT
            if sealed == UNTRUSTED
            else "Run `decktalk install`, which also installs the system libraries Chromium needs and is the one "
            "command that may ask for a password.",
        ) from exc


def _launched(browser: Browser, policy: PagePolicy, started: float) -> Browser:
    """Record which Chromium started, under which policy and how long it took, and hand it back."""
    seconds = time.monotonic() - started
    log.debug(
        "Chromium %s started in %.2f seconds.",
        browser.version,
        seconds,
        extra={"data": {"version": browser.version, "policy": policy, "seconds": round(seconds, 3)}},
    )
    return browser


def evaluate(page: Page, script: str, *, deadline_seconds: float = DEADLINE_SECONDS) -> object:
    """Run one expression in the page and refuse to wait for it past the deadline.

    The expression is raced against a timer inside the page, because the page is where a deck's own
    promises are kept and a call that hangs there hangs this build. A page whose script never yields
    the thread at all cannot be raced from inside itself, and that is what `page.close()` on the way
    out of the recording context is for.
    """
    raced = (
        "async () => {"
        f"  const answer = Promise.resolve().then({script});"
        "  const timer = new Promise((_ok, no) => setTimeout("
        f"    () => no(new Error('the page did not answer within {deadline_seconds:g} seconds')),"
        f"    {deadline_seconds * MILLISECONDS:.0f}));"
        "  return await Promise.race([answer, timer]);"
        "}"
    )
    with driving("the page could not answer"):
        return page.evaluate(raced)


def instrument[T: (Page, BrowserContext)](target: T, *scripts: str) -> T:
    """Add decktalk-probe.js, sealed, and then `scripts` to every page `target` loads from here on. Returns it.

    An init script is added to the page and not to a navigation, so a page a command drives through
    several URLs keeps one probe across all of them. The seal runs after the probe and before any
    script of the page, and it leaves an already sealed window alone.
    """
    for script in (PROBE_JS, SEAL_JS, *scripts):
        target.add_init_script(script)
    return target


def _view(width: int, height: int, color_scheme: str, motion: MotionConfig) -> dict[str, Any]:
    """The viewport, colour scheme and motion that a page and a recording context are both opened with."""
    return {
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "color_scheme": scheme(color_scheme),
        "reduced_motion": "reduce" if motion.reduce else "no-preference",
    }


def open_page(
    browser: Browser,
    allowed: Allowed,
    *,
    width: int,
    height: int,
    color_scheme: str = "no-preference",
    motion: MotionConfig | None = None,
    documents: Mapping[str, bytes] | None = None,
) -> tuple[Page, Assets]:
    """A page a command drives, and the record of what it loaded.

    Its requests under the local origin are answered from what `allowed` names, and it carries the
    probe, because every page a command opens is a page that command has to freeze and measure. It
    renders the motion `[motion]` asks for, so a frozen frame is a frame of the film being built.
    """
    motion = motion or MotionConfig()
    with driving("could not open a page"):
        page = browser.new_page(**_view(width, height, color_scheme, motion))
    instrument(page, *motion_scripts(motion))
    return page, route_pages(page, allowed, documents, trusted=trusts(browser))


def await_ready(page: Page) -> None:
    """Wait for the page's fonts and the runtime's own readiness, and carry on when it has neither."""
    try:
        evaluate(page, READY_JS)
    except ToolError:
        log.debug("the page did not answer __dtprobe.ready(), so it is taken as ready")


def await_painted(page: Page) -> None:
    """Wait until the page is ready and has painted two frames since, which is when a frozen frame is final.

    `ready()` is the contract for a frozen frame: a page that is ready has its fonts, its scene and its
    freeze in place, and the two frames after it carry what that state drew to the screen. A fixed
    settle of 400 ms was six of every eight seconds `check` took, and the frames it waited for were
    byte for byte the frames this takes without it.
    """
    await_ready(page)
    try:
        evaluate(page, PAINTED_JS)
    except ToolError:
        log.debug("the page painted no frame on request, so the frame is taken as it stands")


def page_error_text(err: object) -> str:
    """One line for an uncaught page exception: the message, and the file and line when Chromium gives them."""
    message = str(getattr(err, "message", None) or err).strip().splitlines()[0] if str(err).strip() else "error"
    name = getattr(err, "name", None)
    if name and not message.startswith(f"{name}:"):
        message = f"{name}: {message}"
    stack = str(getattr(err, "stack", "") or "")
    m = re.search(r"((?:file|https?)://\S+?):(\d+)(?::\d+)?\)?\s*$", stack, re.MULTILINE)
    if m:
        message += f" ({m.group(1).split('?', 1)[0].rsplit('/', 1)[-1]}:{m.group(2)})"
    return message


CONSOLE_KINDS = frozenset(("error", "warning"))
"""The console lines of a recorded page that are kept, which are the ones an author wrote to be seen."""


def page_errors(page: Page, caught: list[str], label: str) -> list[str]:
    """The page's uncaught exceptions, plus one entry when the runtime catalog is missing. Each is logged."""
    errors = list(caught)
    try:
        if not evaluate(page, HAS_CATALOG_JS):
            errors.append(NO_CATALOG)
    except ToolError:
        # silent: a page that cannot answer has no catalog, which is the error recorded.
        errors.append(NO_CATALOG)
    for e in errors:
        log.debug("[page] %s  page error: %s", label, e)
    return errors


def read_report(page: Page, label: str) -> PageReport:
    """What the page says about itself, in the one call the contract names, read into models.

    A page that cannot answer at all reports nothing rather than stopping the recording, because a
    recording of a page with no probe in it is still a recording and `page_errors` says so.
    """
    try:
        answer = evaluate(page, REPORT_JS)
    except ToolError as refused:
        return pagereport.read(None).model_copy(update={"unreadable": (str(refused),)})
    report = pagereport.read(answer)
    for row in report.warnings:
        log.debug("[page] %s  %s: %s", label, row.code.name, row.message)
    for line in report.unreadable:
        log.debug("[page] %s  %s", label, line)
    return report


@dataclass
class Capture:
    """A browser context that is recording, and the temporary directory Playwright writes its webm into.

    Playwright writes the file when the context closes, so this owns both the context and the
    directory and hands the finished file over in one step.
    """

    context: BrowserContext
    assets: Assets
    directory: Path
    opened: float  # time.monotonic() when the context was created, which is when capture may have begun
    page: Page | None = None

    def open(self, url: str, caught: list[str], console: list[tuple[str, str]] | None = None) -> Page:
        """The one page of this recording, loaded, with its uncaught exceptions collected into `caught`.

        The page's own `console.error` and `console.warn` lines are the author's diagnostics, so they are
        collected into `console` as their kind and text, and recorded once the recording is read.
        """
        with driving(f"could not open {url}"):
            self.page = self.context.new_page()
        self.page.on("pageerror", lambda err: caught.append(page_error_text(err)))
        if console is not None:
            self.page.on(
                "console",
                lambda said: console.append((said.type, said.text)) if said.type in CONSOLE_KINDS else None,
            )
        with driving(f"could not load {url}"):
            self.page.goto(url, wait_until="load")
        return self.page

    def place(self, out: Path) -> None:
        """Close the context, which writes the webm, and move that webm onto `out`.

        The page is closed first, so a deck whose script is still running is stopped before anything
        waits on it, and the old file at `out` is replaced only once the new one exists.
        """
        video = self.page.video if self.page else None
        if self.page:
            self.page.close()
        self.context.close()
        src = Path(video.path()) if video else None
        if src is None or not src.exists():
            raise ToolError(f"Chromium produced no video for {out.name}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        shutil.move(str(src), str(out))


@contextmanager
def capturing(
    browser: Browser,
    allowed: Allowed,
    *,
    width: int,
    height: int,
    color_scheme: str,
    motion: MotionConfig,
    documents: Mapping[str, bytes] | None = None,
) -> Iterator[Capture]:
    """A recording context and the temporary directory it writes into, both closed however this ends.

    A page that never loads used to leave both behind and surface as INTERNAL. The context is closed
    here and never by a caller, so the one place that owns them is the one place that releases them.
    """
    directory = Path(tempfile.mkdtemp(prefix="decktalk-rec-"))
    try:
        with driving("could not open a recording context"):
            context = browser.new_context(
                **_view(width, height, color_scheme, motion),
                record_video_dir=str(directory),
                record_video_size={"width": width, "height": height},
            )
        opened = time.monotonic()
        assets = route_pages(context, allowed, documents, trusted=trusts(browser))
        capture = Capture(context=context, assets=assets, directory=directory, opened=opened)
        instrument(context, "(" + COVER_JS + ")()", *motion_scripts(motion))
        try:
            yield capture
        finally:
            with suppressing_a_closed_context():
                context.close()
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@contextmanager
def suppressing_a_closed_context() -> Iterator[None]:
    """Close a context that may already be closed, because `place` closes it on the way out."""
    try:
        yield
    except PlaywrightError as exc:
        log.debug("the recording context was already closed (%s)", str(exc).splitlines()[0])


def record_page(
    browser: Browser,
    url: str,
    seconds: float,
    out: Path,
    *,
    allowed: Allowed,
    log_sink: RecordingSink,
    settle_seconds: float,
    min_cover_seconds: float,
    width: int,
    height: int,
    color_scheme: str,
    motion: MotionConfig,
    documents: Mapping[str, bytes] | None = None,
    check: Callable[[], None] = lambda: None,
) -> Recording:
    """Record `url` for `seconds` after the narration clock starts, and leave the webm beside its log.

    `allowed` is what the local origin may answer with, so the page may fetch its own files, the log
    can name every one of them, and a page reaching for the script or for `.env` is turned away.

    The order is the whole point of `log_sink`. The old log goes before anything is captured, the
    webm is replaced next, and the log of what was just recorded is written last, so the pair on
    disk is either complete or absent and a crash can never leave a new picture under an old t=0.

    `check` raises when the recording should stop, and the section's span is waited for in slices so
    it is asked at least once a second. A recording stopped that way places nothing and writes no log.
    """
    log_sink.clear()
    with capturing(
        browser, allowed, width=width, height=height, color_scheme=color_scheme, motion=motion, documents=documents
    ) as capture:
        caught: list[str] = []
        console: list[tuple[str, str]] = []
        page = capture.open(url, caught, console)
        loaded = time.monotonic()
        await_ready(page)
        # Settle after load, and never start the clock before the recorder has certainly begun
        # capturing, because Windows starts its capture late, and the cover makes the wait invisible.
        wait = max(settle_seconds, min_cover_seconds - (time.monotonic() - capture.opened))
        page.wait_for_timeout(wait * MILLISECONDS)
        evaluate(page, START_JS)
        started = time.monotonic()
        waited(page, seconds, check)
        report = read_report(page, out.stem)
        errors = page_errors(page, caught, out.stem)
        for kind, text in console:
            # A console.error is the author telling themselves something broke, so it reaches the terminal.
            level = logging.WARNING if kind == "error" else logging.DEBUG
            said = {"kind": kind, "text": text}
            log.log(level, "[page] %s  console %s: %s", out.stem, kind, text, extra={"data": said})
        recording = Recording(
            url=url,
            assets=tuple(capture.assets.paths),
            external=tuple(capture.assets.external),
            missing=tuple(capture.assets.missing),
            requested_seconds=round(seconds, 3),
            load_seconds=round(loaded - capture.opened, 3),
            settle_seconds=round(started - loaded, 3),
            clock_start_seconds=round(started - capture.opened, 3),
            page_errors=tuple(errors),
            report=report,
        )
        _log_what_the_page_reported(recording, out.stem)
        capture.place(out)
    log_sink.write(recording)
    return recording


def waited(page: Page, seconds: float, check: Callable[[], None]) -> None:
    """Wait `seconds` in slices of at most `CHECK_SECONDS`, asking `check` before each one.

    The slices add up to the span exactly, so the recording is as long as one wait made it, and each
    costs one call to the browser, which is a millisecond against a second.
    """
    left = seconds
    while left > 0:
        check()
        step = min(left, CHECK_SECONDS)
        page.wait_for_timeout(step * MILLISECONDS)
        left -= step


def _log_what_the_page_reported(recording: Recording, label: str) -> None:
    """The lines a person reading a build wants about one recording, which no artifact carries."""
    for cue in recording.report.cues:
        log.debug(
            "[cue ] %s  due %s  ran %s  frame %s  next %s  after %s",
            cue.id, cue.due, cue.ran, cue.frame, cue.next, cue.after,
        )  # fmt: skip
    for line in recording.report.words:
        log.debug(
            "[spkn] %s  cue %.3f  run %.3f  first word shown %s",
            line.text, line.cue_at, line.run_at, line.first_shown,
        )  # fmt: skip
    after_start = [gap for gap in recording.report.frame_gaps if gap.at is not None and gap.at > 0]
    if after_start:
        worst = max(gap.ms for gap in after_start)
        log.debug("[page] %s  %d frame stall(s) after narration t=0, worst %d ms", label, len(after_start), worst)
    under_cover = len(recording.report.frame_gaps) - len(after_start)
    if under_cover:
        log.debug("[page] %s  %d frame stall(s) under the cover", label, under_cover)


def screenshot(page: Page, url: str, out: Path) -> PageReport:
    """Write one PNG of `url` once it has painted, and give back what the page reported while it was open.

    The frame is taken once the page says it is ready and two frames have painted after that, which
    is everything a page that keeps the contract draws, so no fixed wait is spent on top of it.
    """
    with driving(f"could not load {url}"):
        page.goto(url)
    await_painted(page)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out))
    return read_report(page, out.stem)


MEASURED_FRAMES = 12
"""Calibration: how many frames the bias is measured over, which is enough for the middle one to settle."""

MEASURED_FRAME_MS = 60
"""Calibration: how long one measured frame is made to take, which is past the browser's own long-frame floor."""

# The page the bias is measured on. It draws nothing anyone looks at: it holds the main thread for
# longer than a frame, so the browser reports that frame with the work it did and the moment the
# compositor put it on the screen, and the gap between the two is the bias. A browser that reports
# no presentation time answers with an empty list, and the machine is told rather than given a guess.
BIAS_JS = """() => new Promise((done) => {
  const kinds = window.PerformanceObserver ? PerformanceObserver.supportedEntryTypes || [] : [];
  if (!kinds.includes("long-animation-frame")) { done([]); return; }
  const seen = [];
  const watch = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (entry.presentationTime === undefined) continue;
      seen.push(entry.presentationTime - (entry.startTime + entry.duration));
    }
  });
  watch.observe({ type: "long-animation-frame" });
  let left = FRAMES;
  const hold = () => {
    const until = performance.now() + HOLD_MS;
    while (performance.now() < until) { /* holding the thread is what makes the frame a long one */ }
    document.documentElement.style.background = left % 2 ? "#000" : "#fff";
    left -= 1;
    if (left > 0) { requestAnimationFrame(hold); return; }
    requestAnimationFrame(() => setTimeout(() => { watch.disconnect(); done(seen); }, 0));
  };
  requestAnimationFrame(hold);
})"""
"""The measurement, with `FRAMES` and `HOLD_MS` standing where the two constants above go."""


def bias_script(frames: int, hold_ms: int) -> str:
    """The measurement as the page receives it, which is the one place the two constants are written in."""
    return BIAS_JS.replace("FRAMES", str(frames)).replace("HOLD_MS", str(hold_ms))


def measure_presentation_bias() -> float:
    """How long this machine takes to present a frame the page has already drawn, in milliseconds.

    This is the one measurement `decktalk doctor --measure` writes into a machine file, because
    `verify` subtracts it from every offset it measures. It is the middle of a run of frames rather
    than the worst or the mean, so one frame the operating system held up moves nothing.

    It measures the browser this machine launches by default, which is the browser `doctor` reports
    on, rather than one a project names: a bias belongs to the machine and not to a deck.
    """
    # The page is DeckTalk's own and loads nothing, so it is trusted whatever the projects are.
    with chromium(policy=TRUSTED) as browser:
        page = browser.new_page()
        page.set_content("<!doctype html><title>bias</title>")
        answer = evaluate(page, bias_script(MEASURED_FRAMES, MEASURED_FRAME_MS))
    rows = answer if isinstance(answer, list) else []
    samples = [float(row) for row in rows if isinstance(row, (int, float)) and not isinstance(row, bool)]
    if not samples:
        raise ToolError(
            "this machine's browser reports no presentation times, so the bias cannot be measured.",
            hint="Leave host.presentation_bias_ms at 0, which subtracts nothing from a measured offset.",
        )
    return round(statistics.median(samples), 1)


def render_slate(
    out: Path,
    *,
    title: str,
    sub: str = "",
    eyebrow: str,
    foot: str = "",
    width: int,
    height: int,
    background: str,
    browser_path: str = "",
    policy: str,
) -> Path:
    """A titled placeholder frame, for a section whose clip is missing.

    `background` is `[video] slate_color`, written as ffmpeg writes a colour, because the plain
    frame this stands in for is drawn by ffmpeg from the same setting.
    """
    doc = SLATE_HTML.format(
        w=width,
        h=height,
        bg=css_color(background),
        eyebrow=html.escape(eyebrow),
        title=html.escape(title),
        sub=html.escape(sub),
        foot=html.escape(foot),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with chromium(browser_path, policy=policy) as browser:
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(doc)
        await_painted(page)
        page.screenshot(path=str(out))
    return out
