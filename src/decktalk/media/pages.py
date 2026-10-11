"""Driving one page in a launched Chromium: the probe, the deadline on every call, and its report.

It waits for the page to say it is ready and then asks it for one report. Every page it opens is
served from the local origin in `origin.py`, so a page may fetch a file beside it and import a
module, and the recorder learns which files the page actually loaded.

Two rules hold this module to a page it does not trust. Every call into the page carries a deadline,
because a deck's own script runs in the same thread and a page that never answers would otherwise
hold a build for as long as it cared to. The deadline is kept on the browser's own timer, which the
probe took before Playwright's clock or the page could replace it, so it fires on a page whose clock
the recorder holds as on any other. And the probe is sealed onto the window before any script of the
page runs, so the measurements and the deadline come from the instrumentation the recorder injected.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from playwright.sync_api import BrowserContext, CDPSession, Page
from playwright.sync_api import Error as PlaywrightError

from ..errors import ToolError
from ..page import ENGINE_PATH, MOTION_SCALE_PROPERTY
from ..settings import COLOR_SCHEMES, MotionConfig, RecordConfig
from ..toolchain.assets import RUNTIME_FILE, probe_path
from . import pagereport
from .browser import Chromium, choice_of
from .origin import Allowed, Assets, route_pages
from .pagereport import PageReport

log = logging.getLogger(__name__)

# decktalk-probe.js is the instrumentation every command needs from a page and no page carries:
# the step that moves a recorded page one frame, the helper that says when a page has settled, the
# measured catalog's boxes and the freeze that stops at one cue. It is added as an init script, so
# it runs before the page's own scripts and the runtime finds it, and it is never a <script src>
# in a deck.
PROBE_JS = probe_path().read_text(encoding="utf-8")
# The probe is the recorder's own instrument, so a deck cannot take its name once it is on the
# window. This runs as the init script after the probe's own and before any script of the page.
SEAL_JS = """(() => {
  const probe = window.__decktalkProbe;
  const own = Object.getOwnPropertyDescriptor(window, "__decktalkProbe");
  if (!probe || (own && own.writable === false)) return;
  Object.freeze(probe);
  Object.defineProperty(window, "__decktalkProbe", { value: probe, writable: false, configurable: false });
})()"""
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
READY_JS = "() => window.__decktalkProbe.ready()"
# Two animation frames after the page is ready, so what the page drew in answer to ready() has been
# through layout and paint before a frozen frame is taken.
PAINTED_JS = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(() => done(true))))"
REPORT_JS = "() => window.__decktalkProbe.report()"
# Draw one caption, given as JSON, in the band of a page opened with `?caption=1`, and wait until it has painted.
CAPTION_JS = "() => window.__decktalk.caption(%s)"
# Whether the runtime is present and the page registered at least one scene.
HAS_CATALOG_JS = "() => !!(window.__decktalk && window.__decktalk.catalog && window.__decktalk.catalog.length)"
NO_CATALOG = (
    f"no window.__decktalk.catalog (does the page load {ENGINE_PATH}{RUNTIME_FILE}, and does it register a scene?)"
)

DEADLINE_SECONDS = RecordConfig().page_deadline_seconds
"""Truth: the default of `[record] page_deadline_seconds`, which a call from a command other than `record` is held to.

`record` holds every call into its pages to the machine's own value of the key instead.
"""

ColorScheme = Literal["light", "dark", "no-preference"]
"""What a page may be told the viewer prefers, which is the closed set `[record] color_scheme` publishes."""


def motion_scripts(motion: MotionConfig) -> list[str]:
    """What `[motion]` asks a page for, as the scripts that put it there before the page's own run.

    Reduced motion is a media feature rather than an attribute, so Chromium is asked for it and the
    same render is what a person who asked their own machine for less motion sees in a preview. The
    scale is a custom property, because it multiplies every length the sheet plays and no query key
    could carry it.
    """
    return [] if motion.scale == 1 else [MOTION_JS % (MOTION_SCALE_PROPERTY, f"{motion.scale:g}")]


def scheme(value: str) -> ColorScheme:
    """The colour scheme a page is opened under, refused here rather than handed to Chromium unread.

    A setting Chromium does not know is a project file that says something untrue about the render,
    and the browser takes it silently, so the one place that passes it on is the place that reads it.
    """
    return choice_of("color_scheme", value, COLOR_SCHEMES)


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


STOPPED_HINT = (
    "Look for a script that spins the page's main thread or waits on something that never arrives, or give "
    "a slow machine more time with record.page_deadline_seconds and record.deadline_slowdown."
)
"""What a recording the watchdog stopped tells its reader to do."""


def close_soon(context: BrowserContext) -> None:
    """Close `context` from a thread other than the one driving it, which is how a call stuck in its page ends.

    Playwright's sync API answers only on the thread that opened it, and that thread is blocked in the
    stuck call. It waits by running Playwright's own event loop, so the close is handed to that loop,
    which sends it while the call waits, and the call then fails as one whose page was closed. A
    context already closed, or a loop already stopped, has nothing left to close.
    """
    impl, loop = context._impl_obj, context._loop

    def start() -> None:
        closing = asyncio.ensure_future(impl.close())
        closing.add_done_callback(lambda done: done.cancelled() or done.exception())

    try:
        loop.call_soon_threadsafe(start)
    except RuntimeError:
        # silent: a stopped loop has no context left open.
        return


def devtools(page: Page, session: CDPSession, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """What `method` over `session` answers, or a Playwright error as soon as `page` has closed or crashed.

    Playwright fails a call into a page once the page closes or crashes, but a command over a DevTools
    session waits for Chromium's own answer. On Linux a renderer that crashes under a command can close
    its page and leave the command unanswered, and closing the context then ends nothing either, so the
    `Watchdog` could not end such a call and a recording hung on it. The command is raced against the
    page's end on Playwright's loop, as Playwright races its own calls into a page, so it ends when the
    page does, and the watchdog's close ends it like any other call. A page that has already ended is
    sent nothing and answers nothing, even when a command's answer arrives with the news of its end.
    """
    impl, sent_over = page._impl_obj, session._impl_obj
    ended: asyncio.Future[Any] = impl._closed_or_crashed_future

    async def raced() -> dict[str, Any]:
        if ended.done():
            raise ended.result()
        sent = asyncio.ensure_future(sent_over.send(method, dict(params)))
        await asyncio.wait([sent, ended], return_when=asyncio.FIRST_COMPLETED)
        if not ended.done():
            return sent.result()
        sent.cancel()
        sent.add_done_callback(lambda done: done.cancelled() or done.exception())
        raise ended.result()

    return session._sync(raced())


@dataclass(eq=False)
class Watchdog:
    """A deadline on every call into one recorded page and on the recording as a whole, held from a thread of its own.

    A page whose script never yields its main thread cannot be raced from inside itself, and Playwright
    puts no deadline on `evaluate` or on its clock, so a call is held from outside: the thread waits
    until the call's deadline, which is `call_seconds` from its start or the recording's own end if that
    comes first, and closes the page's context if the call is still running then. The call then fails,
    and `call` turns that failure into a `ToolError` naming `name`, the deadline and what the call was
    doing, which is a TOOL error at the stuck frame rather than a hang.
    """

    close: Callable[[], None]
    name: str
    call_seconds: float
    whole_seconds: float
    ends: float = 0.0
    """When the recording as a whole must be done, on the monotonic clock."""
    doing: str | None = None
    """What the call in flight is doing, or None between calls."""
    due: float = 0.0
    """When the call in flight must be done, on the monotonic clock."""
    fired: str | None = None
    """The sentence a recording the watchdog stopped says, once it has closed the page."""
    over: bool = False
    turn: threading.Condition = field(default_factory=threading.Condition)

    @classmethod
    @contextmanager
    def watching(
        cls, close: Callable[[], None], *, name: str, call_seconds: float, whole_seconds: float
    ) -> Iterator[Watchdog]:
        """A watchdog whose thread runs for the block, which is the recording it holds to its deadlines."""
        dog = cls(close, name, call_seconds, whole_seconds, ends=time.monotonic() + whole_seconds)
        thread = threading.Thread(target=dog.watch, daemon=True)
        thread.start()
        try:
            yield dog
        finally:
            with dog.turn:
                dog.over = True
                dog.turn.notify_all()
            thread.join()

    def watch(self) -> None:
        """Wait on each call in turn and close the page once one outlives its deadline, which the thread runs."""
        with self.turn:
            while not self.over:
                if self.doing is None:
                    self.turn.wait()
                    continue
                left = self.due - time.monotonic()
                if left > 0:
                    self.turn.wait(left)
                    continue
                self.fired = (
                    f"{self.name} ran past its deadline of {self.whole_seconds:g} seconds at {self.doing}, "
                    "so its page was closed."
                    if self.due >= self.ends
                    else f"{self.name} stopped answering at {self.doing}: no answer in {self.call_seconds:g} "
                    "seconds, so its page was closed."
                )
                self.close()
                return

    def check(self) -> None:
        """Raise the watchdog's `ToolError` when it closed the page, or when the recording is past its own end."""
        if self.fired is None and time.monotonic() >= self.ends:
            self.fired = f"{self.name} ran past its deadline of {self.whole_seconds:g} seconds."
        if self.fired is not None:
            raise ToolError(self.fired, hint=STOPPED_HINT)

    @contextmanager
    def call(self, doing: str) -> Iterator[None]:
        """Hold one call into the page to its deadline, and say why it failed when the watchdog closed the page."""
        self.check()
        with self.turn:
            self.doing = doing
            self.due = min(time.monotonic() + self.call_seconds, self.ends)
            self.turn.notify_all()
        try:
            yield
        except (PlaywrightError, ToolError) as failed:
            if self.fired is not None:
                raise ToolError(self.fired, hint=STOPPED_HINT) from failed
            raise
        finally:
            with self.turn:
                self.doing = None


def evaluate(page: Page, script: str, *, deadline_seconds: float = DEADLINE_SECONDS) -> object:
    """Run one expression in the page and refuse to wait for it past the deadline.

    The expression is raced by the probe's `within`, on the browser's own timer, because the page is
    where a deck's own promises are kept and a call that hangs there hangs this build. That timer is
    neither the page's nor Playwright's clock, so it fires on a held page and leaves the clock no
    timer of the recorder's own to end a still stretch. A page whose script never yields the thread
    at all cannot be raced from inside itself, so `record` also holds each call to a `Watchdog`,
    which closes the page from outside.
    """
    raced = f"() => window.__decktalkProbe.within(Promise.resolve().then({script}), {deadline_seconds:g})"
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


def view(width: int, height: int, color_scheme: str, motion: MotionConfig) -> dict[str, Any]:
    """The viewport, colour scheme and motion that a page and a recording context are both opened with."""
    return {
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "color_scheme": scheme(color_scheme),
        "reduced_motion": "reduce" if motion.reduce else "no-preference",
    }


def open_page(
    chromium: Chromium,
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
        page = chromium.browser.new_page(**view(width, height, color_scheme, motion))
    instrument(page, *motion_scripts(motion))
    return page, route_pages(page, allowed, documents, trusted=chromium.trusted)


def open_document(chromium: Chromium, html: str, **options: Any) -> Page:
    """A page of DeckTalk's own showing `html`, opened with Playwright's page `options`.

    The document is set rather than served, so it loads nothing from any origin and carries no probe.
    It is for a frame DeckTalk draws itself, such as a slate or the bias measurement.
    """
    with driving("could not open a page"):
        page = chromium.browser.new_page(**options)
        page.set_content(html)
    return page


def load(page: Page, url: str) -> None:
    """Point `page` at `url` and wait for it to load."""
    with driving(f"could not load {url}"):
        page.goto(url)


def write_frame(page: Page, out: Path, *, clear: bool = False) -> None:
    """Write what `page` shows now to `out` as a PNG, with an alpha channel where it draws nothing when `clear`."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with driving(f"could not write {out.name}"):
        page.screenshot(path=str(out), omit_background=clear)


def await_ready(page: Page, *, deadline_seconds: float = DEADLINE_SECONDS) -> None:
    """Wait for the page's fonts and the runtime's own readiness, and carry on when it has neither."""
    try:
        evaluate(page, READY_JS, deadline_seconds=deadline_seconds)
    except ToolError:
        log.debug("the page did not answer __decktalkProbe.ready(), so it is taken as ready")


def await_painted(page: Page) -> None:
    """Wait until the page is ready and has painted two frames since, which is when a frozen frame is final.

    `ready()` is the contract for a frozen frame: a page that is ready has its fonts, its scene and its
    freeze in place, and the two frames after it carry what that state drew to the screen, so no
    fixed settle is waited on top of it.
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


def page_errors(page: Page, caught: list[str], label: str, *, deadline_seconds: float = DEADLINE_SECONDS) -> list[str]:
    """The page's uncaught exceptions, plus one entry when the runtime catalog is missing. Each is logged."""
    errors = list(caught)
    try:
        if not evaluate(page, HAS_CATALOG_JS, deadline_seconds=deadline_seconds):
            errors.append(NO_CATALOG)
    except ToolError as unanswered:
        log.debug("[page] %s  could not say whether it has a catalog: %s", label, unanswered)
        errors.append(NO_CATALOG)
    for e in errors:
        log.debug("[page] %s  page error: %s", label, e)
    return errors


def read_report(page: Page, label: str, *, deadline_seconds: float = DEADLINE_SECONDS) -> PageReport:
    """What the page says about itself, in the one call the contract names, read into models.

    A page that cannot answer at all reports nothing rather than stopping the recording, because a
    recording of a page with no probe in it is still a recording and `page_errors` says so.
    """
    try:
        answer = evaluate(page, REPORT_JS, deadline_seconds=deadline_seconds)
    except ToolError as refused:
        return pagereport.read(None).model_copy(update={"unreadable": (str(refused),)})
    report = pagereport.read(answer)
    for row in report.warnings:
        log.debug("[page] %s  %s: %s", label, row.code.name, row.message)
    for line in report.unreadable:
        log.debug("[page] %s  %s", label, line)
    return report


def caption_frame(page: Page, caption: Mapping[str, object] | None, out: Path) -> None:
    """Write one caption as the page draws it in its band, over a clear frame, to `out` as a PNG.

    The page must be open with `?caption=1`. The runtime answers once the caption's fonts have
    loaded and two frames have painted, so no fixed wait is spent on top of it.
    """
    evaluate(page, CAPTION_JS % json.dumps(caption))
    write_frame(page, out, clear=True)


def screenshot(page: Page, url: str, out: Path) -> PageReport:
    """Write one PNG of `url` once it has painted, and give back what the page reported while it was open.

    The frame is taken once the page says it is ready and two frames have painted after that, which
    is everything a page that keeps the contract draws, so no fixed wait is spent on top of it.
    """
    load(page, url)
    await_painted(page)
    write_frame(page, out)
    return read_report(page, out.stem)
