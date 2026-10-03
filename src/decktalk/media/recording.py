"""Recording one page section in a launched Chromium, from the first paint to the end of its span.

A recording opens a context that writes a webm, loads the page under the cover the probe draws,
lifts the cover to start the section clock, waits the section's span and then reads what the page
reported, so the webm and the log of what the page did are written as one pair.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from ..errors import ToolError
from ..settings import MotionConfig
from .browser import Chromium
from .origin import Allowed, Assets, route_pages
from .pagereport import Recording
from .pages import (
    CONSOLE_KINDS,
    COVER_JS,
    START_JS,
    await_ready,
    driving,
    evaluate,
    instrument,
    motion_scripts,
    page_error_text,
    page_errors,
    read_report,
    view,
)

log = logging.getLogger(__name__)

CHECK_SECONDS = 1.0
"""Calibration: how often a recording asks whether it should stop, which is as long as a person waits on a stop."""


class RecordingSink(Protocol):
    """Where the log of one recording is kept, which the recorder clears before it captures and fills after.

    The pair on disk has to be complete or absent. A webm replaced under the log of the recording
    before it keeps its digest and moves narration t=0, so the next assemble trims the new picture at
    the old moment and every reveal in the section lands wrong. Clearing first and writing last
    leaves a crash with no log, which the next run reads as a section it has not recorded.
    """

    def clear(self) -> None:
        """Delete the log of the recording that is about to be replaced, before anything is captured."""

    def write(self, recording: Recording) -> None:
        """Write the log of the recording now on disk, once the webm is in place."""


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
        with driving(f"could not finish {out.name}"):
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
    chromium: Chromium,
    allowed: Allowed,
    *,
    width: int,
    height: int,
    color_scheme: str,
    motion: MotionConfig,
    documents: Mapping[str, bytes] | None = None,
) -> Iterator[Capture]:
    """A recording context and the temporary directory it writes into, both closed however this ends.

    A page that never loads leaves neither behind. The context is closed here and never by a caller,
    so the one place that owns them is the one place that releases them.
    """
    directory = Path(tempfile.mkdtemp(prefix="decktalk-rec-"))
    try:
        with driving("could not open a recording context"):
            context = chromium.browser.new_context(
                **view(width, height, color_scheme, motion),
                record_video_dir=str(directory),
                record_video_size={"width": width, "height": height},
            )
        opened = time.monotonic()
        assets = route_pages(context, allowed, documents, trusted=chromium.trusted)
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
    chromium: Chromium,
    url: str,
    seconds: float,
    out: Path,
    *,
    allowed: Allowed,
    log_sink: RecordingSink,
    settle_seconds: float,
    cover_min_seconds: float,
    width: int,
    height: int,
    color_scheme: str,
    motion: MotionConfig,
    documents: Mapping[str, bytes] | None = None,
    check: Callable[[], None] = lambda: None,
) -> Recording:
    """Record `url` for `seconds` after the section clock starts, and leave the webm beside its log.

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
        chromium, allowed, width=width, height=height, color_scheme=color_scheme, motion=motion, documents=documents
    ) as capture:
        caught: list[str] = []
        console: list[tuple[str, str]] = []
        page = capture.open(url, caught, console)
        loaded = time.monotonic()
        await_ready(page)
        # Settle after load, and never start the clock before the recorder has certainly begun
        # capturing, because Windows starts its capture late, and the cover makes the wait invisible.
        wait = max(settle_seconds, cover_min_seconds - (time.monotonic() - capture.opened))
        with driving(f"the page stopped while {out.name} was recorded"):
            page.wait_for_timeout(wait * 1000)
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
        page.wait_for_timeout(step * 1000)
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
            "[spkn] %s  cue %.3f  spoken %.3f  first word shown %s",
            line.text, line.cue_at, line.spoken_at, line.first_shown,
        )  # fmt: skip
    after_start = [gap for gap in recording.report.frame_gaps if gap.at is not None and gap.at > 0]
    if after_start:
        worst = max(gap.ms for gap in after_start)
        log.debug("[page] %s  %d frame stall(s) after narration t=0, worst %d ms", label, len(after_start), worst)
    under_cover = len(recording.report.frame_gaps) - len(after_start)
    if under_cover:
        log.debug("[page] %s  %d frame stall(s) under the cover", label, under_cover)
