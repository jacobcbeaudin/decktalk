"""Driving one page in a launched Chromium: the probe, the deadline on every call, and its report.

It waits for the page to say it is ready and then asks it for one report. Every page it opens is
served from the local origin in `origin.py`, so a page may fetch a file beside it and import a
module, and the recorder learns which files the page actually loaded.

Two rules hold this module to a page it does not trust. Every call into the page carries a deadline,
because a deck's own script runs in the same thread and a page that never answers would otherwise
hold a build for as long as it cared to. And the probe is sealed onto the window before any script
of the page runs, so the measurements come from the instrumentation the recorder injected.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from playwright.sync_api import Browser, BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from ..errors import ToolError
from ..page import ENGINE_PATH, MOTION_SCALE_PROPERTY
from ..settings import COLOR_SCHEMES, MotionConfig
from ..toolchain.assets import RUNTIME_FILE, probe_path
from . import pagereport
from .browser import choice_of, trusts
from .origin import Allowed, Assets, route_pages
from .pagereport import PageReport

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
NO_CATALOG = (
    f"no window.__decktalk.catalog (does the page load {ENGINE_PATH}{RUNTIME_FILE}, and does it register a scene?)"
)

DEADLINE_SECONDS = 15.0
"""Calibration: many times the longest a probe call measures, so only a page that stopped answering hits it."""

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
        f"    {deadline_seconds * 1000:.0f}));"
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


def view(width: int, height: int, color_scheme: str, motion: MotionConfig) -> dict[str, Any]:
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
        page = browser.new_page(**view(width, height, color_scheme, motion))
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
    except ToolError as unanswered:
        log.debug("[page] %s  could not say whether it has a catalog: %s", label, unanswered)
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
