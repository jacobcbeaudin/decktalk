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
"""

from __future__ import annotations

import html
import logging
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from pydantic import BaseModel, Field

from ..errors import InputError, ToolError
from ..findings import MODEL
from ..toolchain import chromium_fetch
from ..toolchain.assets import probe_path
from . import pagereport
from .encode import css_color
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
# Remove the cover, then start the page clock on the next animation frame.
START_JS = "() => window.__dtprobe.lift()"
READY_JS = "() => window.__dtprobe.ready()"
REPORT_JS = "() => window.__dtprobe.report()"
# Whether the runtime is present and the page registered at least one scene.
HAS_CATALOG_JS = "() => !!(window.__decktalk && window.__decktalk.catalog && window.__decktalk.catalog.length)"
NO_CATALOG = "no window.__decktalk.catalog (is decktalk-runtime.js included, and does the page register a scene?)"

DEADLINE_SECONDS = 15.0
"""How long one call into the page may take, which is many times the longest a probe call measures."""

ColorScheme = Literal["dark", "light", "no-preference"]
"""What a page may be told the viewer prefers, which is the closed set Chromium itself accepts."""

COLOR_SCHEMES: tuple[ColorScheme, ...] = ("dark", "light", "no-preference")
"""The values `[record] color_scheme` may take, named here because this is the layer that passes them on."""


def scheme(value: str) -> ColorScheme:
    """The colour scheme a page is opened under, refused here rather than handed to Chromium unread.

    A setting Chromium does not know is a project file that says something untrue about the render,
    and the browser takes it silently, so the one place that passes it on is the place that reads it.
    """
    if value not in COLOR_SCHEMES:
        raise InputError(
            f"[record] color_scheme = {value!r} is not one of {', '.join(COLOR_SCHEMES)}.",
            hint=f"Set it to one of {', '.join(COLOR_SCHEMES)}.",
        )
    return value


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


class Recording(BaseModel):
    """One section recorded: what the page loaded, what it said, and where narration t=0 sits in the webm.

    This is what the recorder knows. Whether the recording still matches the project, and what the
    frames of it show, are the stage's to add when it writes the log.
    """

    model_config = MODEL

    url: str = Field(description="The page URL that was recorded, with its query.")
    assets: tuple[str, ...] = Field(description="Every project file the page loaded, project-relative.")
    external: tuple[str, ...] = Field(description="Every other origin the page reached for while recording.")
    requested_seconds: float = Field(ge=0, description="How long the page was recorded for after the clock started.")
    load_seconds: float = Field(ge=0, description="How long the page took to load.")
    settle_seconds: float = Field(ge=0, description="How long the page was left to settle after it loaded.")
    clock_start_seconds: float = Field(ge=0, description="Seconds from the recorder's start to narration t=0.")
    page_errors: tuple[str, ...] = Field(description="Uncaught exceptions, or the one line for no runtime at all.")
    report: PageReport = Field(description="What the page said about itself, read once.")


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
def chromium(browser_path: str = "") -> Iterator[Browser]:
    """A launched headless Chromium, as the machine configures it, closed on exit.

    No proxy argument is passed. Request routing answers the local origin before the network stack
    reaches it, so no proxy ever sees that host, and every other request a recorded page makes goes
    the way the machine sends it, through its own proxy and its own logging.

    `browser_path` is `[record] browser_path`, the executable a machine that manages its own
    Chromium names. It is empty on a machine DeckTalk fetches the browser for, which is where
    `launch` fetches it.
    """
    with sync_playwright() as pw:
        browser = launch(pw, browser_path)
        try:
            yield browser
        finally:
            browser.close()


def launch(pw: Playwright, browser_path: str = "") -> Browser:
    """A launched Chromium, fetching the build Playwright manages when this machine has not got it.

    This is the one place a browser starts, so every command gets the browser it needs without
    anyone running an install step first, the way `ffmpeg_paths` gets ffmpeg. The fetch downloads
    Chromium alone and never its system libraries, because installing those goes through sudo and a
    build that stops for a root password is a build that hangs in a script and in CI. When Chromium
    still will not launch after it has been fetched, those libraries are what is missing, and the
    error says to run `decktalk install`, which is the one command that may ask for a password.

    A machine that names its own executable is told about that executable instead. Fetching would
    not help it: the next launch would use the same path again.
    """
    try:
        return pw.chromium.launch(executable_path=browser_path or None)
    except PlaywrightError as exc:
        if browser_path:
            raise ToolError(
                f"could not launch the Chromium at {browser_path} ({str(exc).splitlines()[0]}).",
                hint="[record] browser_path names it. Clear that setting to use the build DeckTalk fetches.",
            ) from exc
    # A launch that failed with no executable named falls through to here, which is the fetch.
    if chromium_fetch.installed_chromium(pw) is not None:
        log.info("Chromium is on this machine and did not launch, so the build is being fetched again")
    chromium_fetch.fetch_chromium()
    try:
        return pw.chromium.launch()
    except PlaywrightError as exc:
        raise ToolError(
            f"Chromium was fetched and still would not launch ({str(exc).splitlines()[0]}).",
            hint="Run `decktalk install`, which also installs the system libraries Chromium needs and is the one "
            "command that may ask for a password.",
        ) from exc


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


def instrument(page: Page) -> Page:
    """Add decktalk-probe.js to every page `page` loads from here on, sealed. Returns the page.

    An init script is added to the page and not to a navigation, so a page a command drives through
    several URLs keeps one probe across all of them. The seal runs after the probe and before any
    script of the page, and it leaves an already sealed window alone.
    """
    page.add_init_script(PROBE_JS)
    page.add_init_script(SEAL_JS)
    return page


def open_page(
    browser: Browser,
    allowed: Allowed,
    *,
    width: int,
    height: int,
    color_scheme: str = "no-preference",
) -> tuple[Page, Assets]:
    """A page a command drives, and the record of what it loaded.

    Its requests under the local origin are answered from what `allowed` names, and it carries the
    probe, because every page a command opens is a page that command has to freeze and measure.
    """
    with driving("could not open a page"):
        page = browser.new_page(
            viewport={"width": width, "height": height}, device_scale_factor=1, color_scheme=scheme(color_scheme)
        )
    instrument(page)
    return page, route_pages(page, allowed)


def await_ready(page: Page) -> None:
    """Wait for the page's fonts and the runtime's own readiness, and carry on when it has neither."""
    try:
        evaluate(page, READY_JS)
    except ToolError:
        log.debug("the page did not answer __dtprobe.ready(), so it is taken as ready")


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


def page_errors(page: Page, caught: list[str], label: str) -> list[str]:
    """The page's uncaught exceptions, plus one entry when the runtime catalog is missing. Each is logged."""
    errors = list(caught)
    try:
        if not evaluate(page, HAS_CATALOG_JS):
            errors.append(NO_CATALOG)
    except ToolError:
        errors.append(NO_CATALOG)
    for e in errors:
        log.warning("[page] %s  page error: %s", label, e)
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
        log.warning("[page] %s  %s: %s", label, row.code.name, row.message)
    for line in report.unreadable:
        log.warning("[page] %s  %s", label, line)
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

    def open(self, url: str, caught: list[str]) -> Page:
        """The one page of this recording, loaded, with its uncaught exceptions collected into `caught`."""
        with driving(f"could not open {url}"):
            self.page = self.context.new_page()
        self.page.on("pageerror", lambda err: caught.append(page_error_text(err)))
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
def capturing(browser: Browser, allowed: Allowed, *, width: int, height: int, color_scheme: str) -> Iterator[Capture]:
    """A recording context and the temporary directory it writes into, both closed however this ends.

    A page that never loads used to leave both behind and surface as INTERNAL. The context is closed
    here and never by a caller, so the one place that owns them is the one place that releases them.
    """
    directory = Path(tempfile.mkdtemp(prefix="decktalk-rec-"))
    try:
        with driving("could not open a recording context"):
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
                color_scheme=scheme(color_scheme),
                reduced_motion="no-preference",
                record_video_dir=str(directory),
                record_video_size={"width": width, "height": height},
            )
        opened = time.monotonic()
        capture = Capture(context=context, assets=route_pages(context, allowed), directory=directory, opened=opened)
        context.add_init_script(PROBE_JS)
        context.add_init_script(SEAL_JS)
        context.add_init_script("(" + COVER_JS + ")()")
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
) -> Recording:
    """Record `url` for `seconds` after the narration clock starts, and leave the webm beside its log.

    `allowed` is what the local origin may answer with, so the page may fetch its own files, the log
    can name every one of them, and a page reaching for the script or for `.env` is turned away.

    The order is the whole point of `log_sink`. The old log goes before anything is captured, the
    webm is replaced next, and the log of what was just recorded is written last, so the pair on
    disk is either complete or absent and a crash can never leave a new picture under an old t=0.
    """
    log_sink.clear()
    with capturing(browser, allowed, width=width, height=height, color_scheme=color_scheme) as capture:
        caught: list[str] = []
        page = capture.open(url, caught)
        loaded = time.monotonic()
        await_ready(page)
        # Settle after load, and never start the clock before the recorder has certainly begun
        # capturing, because Windows starts its capture late, and the cover makes the wait invisible.
        wait = max(settle_seconds, min_cover_seconds - (time.monotonic() - capture.opened))
        page.wait_for_timeout(wait * 1000)
        evaluate(page, START_JS)
        started = time.monotonic()
        page.wait_for_timeout(seconds * 1000)
        report = read_report(page, out.stem)
        errors = page_errors(page, caught, out.stem)
        recording = Recording(
            url=url,
            assets=tuple(capture.assets.paths),
            external=tuple(capture.assets.external),
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
    for name in capture.assets.missing:
        log.warning("[page] %s  the page asked for %s and the project has no such file", out.stem, name)
    return recording


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
        log.warning("[page] %s  %d frame stall(s) after narration t=0, worst %d ms", label, len(after_start), worst)
    under_cover = len(recording.report.frame_gaps) - len(after_start)
    if under_cover:
        log.debug("[page] %s  %d frame stall(s) under the cover", label, under_cover)


def screenshot(page: Page, url: str, out: Path, *, settle_ms: int) -> PageReport:
    """Write one PNG of `url`, and give back what the page reported while it was open."""
    with driving(f"could not load {url}"):
        page.goto(url)
    await_ready(page)
    page.wait_for_timeout(settle_ms)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out))
    return read_report(page, out.stem)


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
    with chromium(browser_path) as browser:
        page = browser.new_page(viewport={"width": width, "height": height})
        page.set_content(doc)
        await_ready(page)
        page.screenshot(path=str(out))
    return out
