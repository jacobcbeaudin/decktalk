"""Recording one page section by stepping its clock, one output frame at a time.

The recorder owns time. It installs Playwright's clock before the page loads, lets the page load and
settle on that clock while it still runs, and then holds it still. For frame `k` it moves the page's
timers and animation frames on by one frame, asks the runtime to step the section clock to `k / fps`,
which runs what is due, draws every frame hook and holds every animation at that second, and captures
what the page shows once it has drawn that second: laid out, painted and committed, with every font it
shows loaded and every image decoded. Each capture goes down one pipe into one encode, so frame `k` of the file
is the page at second `k / fps` by construction, there is no start to find in it, nothing can stall,
and two recordings of one page are the same frames.

Most frames of a narrated deck are the frame before. Each step answers whether anything the page
draws may have changed since the frame before, and a frame where nothing did is written again
without a capture, which is the same frame for the cost of a step. Each step also answers the
earliest second anything can next change, so every frame of a still stretch is written again without
even a step: the page is stepped once across the stretch, to its last frame, and then to the frame
where the next change lands.

Because frame `k` is a function of its second alone, a section's frames split into pieces that
several pages record at once, each into a lossless file of its own that starts on a keyframe, and
the pieces join into the section's file without being encoded again. A still stretch ends at a
piece's last frame, and every piece captures its own first frame.

    record_piece  one range of a section's frames, recorded in a page of its own into a file of its own
    join          the pieces of one section, joined into its file, and what their pages said as one recording
    Stepper       the frame loop behind a piece: the clock, the step and the capture
    FrameSink     where the frames go, which `LosslessPipe` is for the one lossless encode, fed from a thread
"""

from __future__ import annotations

import base64
import contextvars
import logging
import math
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from playwright.sync_api import CDPSession, Page

from ..errors import ToolError
from ..files import drafted
from ..page import SECOND_DIGITS
from ..settings import MotionConfig
from . import ffmpeg
from .browser import Chromium
from .clipframes import ClipFrames
from .origin import Allowed, Assets, route_pages
from .pagereport import PageReport, Recording, UndeclaredChange, joined
from .pages import (
    CONSOLE_KINDS,
    DEADLINE_SECONDS,
    await_ready,
    driving,
    instrument,
    motion_scripts,
    page_error_text,
    page_errors,
    read_report,
    view,
)

log = logging.getLogger(__name__)

MILLISECONDS = 1000
"""Truth: how many milliseconds one second holds, which Playwright's clock and the page count in."""

STEP_JS = "([seconds, wait]) => window.__decktalkProbe.step(seconds, wait)"
"""The one call that moves a recorded page, which answers once the page has drawn the second it was moved to.

It answers `{due, changed, stillUntil, undeclared}`: the second the next task is due, or null, whether
anything the page draws may have changed since the step before, the earliest second anything can
next change, or null when nothing ever will, and the element a script changed since the step before
inside nothing the page declared as moving, or null.
"""

STILL_SLACK_MS = 1
"""Truth: how far a still stretch's end may sit from the millisecond the change it waits for lands on.

The page answers the second its next change is due at, and the recorder moves the page's timers in
whole milliseconds, each frame's own rounded one, so the two can disagree by a rounding. The stretch
ends one millisecond early, which at worst steps one frame of it more and never skips the change.
"""

STARTED_JS = "(wait) => window.__decktalkProbe.started(wait)"
"""The call the hold waits on, which answers once every animation the page set playing has started.

A loop the first slide starts is still waiting for the browser to start it when the page loads, and
one paused before it starts can show a frame further on than the second it is sought to.
"""

NOW_JS = "() => Date.now()"
"""The page's own clock in milliseconds, read once to hold it still a moment ahead of where it stands."""

HOLD_LEAD_MS = 1000
"""Calibration: how far past the page's own clock the recorder holds it, which no load waits on.

The hold has to land after every timer the load already owed, so a timer the page set while it
loaded runs once, before the first frame, rather than in the middle of the section.
"""

LOSSLESS = (
    "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv444p", "-x264-params", "stitchable=1",
)  # fmt: skip
"""Truth: the encode a recording is written with, which keeps every captured pixel.

A recording is an intermediate that `assemble` encodes once more at the film's own quality, so a
lossy encode here would put the encoder's ringing into the frames twice and into every frame verify
compares. Lossless at the fastest preset costs disk, which `build/` is for, and no time. Every piece
of a section is written with the same parameters, and `stitchable` keeps x264 from tuning its headers
to one piece, so the pieces join into one stream without an encode.
"""

CAPTURE = {"format": "png", "optimizeForSpeed": True}
"""Truth: what each frame is captured as, a lossless image the encoder reads straight from the pipe."""

SKIP_AUDIT_EVERY: int = 0
"""Truth: how often a recording captures a frame it would write again and stops if it differs, zero for never.

Every build DeckTalk runs takes the fast path. The test suite raises it, so every frame its builds
write again is proved against a capture of the same frame.
"""


class FrameSink(Protocol):
    """Where a recording's frames go, one call per frame in order.

    `write` takes a frame the page was captured as, and `repeat` says the next frame is the frame
    before, which the stepper knows without a capture. A sink keeps what it needs to honour a repeat:
    the lossless pipe lengthens the run of the frame before, which its feeder sends again.
    """

    def write(self, frame: bytes) -> None:
        """Take the next frame, captured."""

    def repeat(self) -> None:
        """Take the next frame as the frame before again."""


ENCODE_QUEUE_FRAMES = 8
"""Calibration: how many distinct frames may wait for the encode before the stepper waits for it.

A captured 1080p frame is one to three megabytes and takes the page about 15 ms to make, where the
encode takes about 2 ms to read and encode one, so the queue rarely holds more than one or two. The
bound keeps a stalled encode from holding a section's worth of frames in memory. A repeated frame is
a count on the frame before and never takes a place of its own.
"""


@dataclass
class LosslessPipe:
    """The one lossless encode a recording is written with, as a frame sink that never makes the stepper wait for it.

    The frames wait as runs, each a frame and how many times in a row it is shown, so a repeat
    lengthens the last run and costs the stepper nothing. A feeder thread writes every frame of every
    run down the pipe in order, as many times as it runs, while the stepper goes on to the next
    step, so stepping the page and encoding its frames overlap. The frames the encode reads are the
    frames it would read written one by one.
    """

    send: Callable[[bytes], None]
    depth: int = ENCODE_QUEUE_FRAMES
    runs: deque[list] = field(default_factory=deque)
    """The frames still to be sent, each as `[frame, how many more times]`, oldest first."""
    last: bytes | None = None
    failed: BaseException | None = None
    """What stopped the feeder, which the stepper raises at its next frame."""
    done: bool = False
    """Whether every frame has been handed over, or the recording stopped, which ends the feeder."""
    turn: threading.Condition = field(default_factory=threading.Condition)

    def write(self, frame: bytes) -> None:
        with self.turn:
            self.turn.wait_for(lambda: len(self.runs) < self.depth or self.failed is not None)
            self.raise_failed()
            self.runs.append([frame, 1])
            self.last = frame
            self.turn.notify_all()

    def repeat(self) -> None:
        with self.turn:
            self.raise_failed()
            if self.last is None:
                raise ToolError("a recording cannot repeat a frame before it has captured one.")
            if self.runs and self.runs[-1][0] is self.last:
                self.runs[-1][1] += 1
            else:
                self.runs.append([self.last, 1])
            self.turn.notify_all()

    def raise_failed(self) -> None:
        if self.failed is not None:
            raise self.failed

    def feed(self) -> None:
        """Send every frame of every run in order until the stepper is done, which the feeder thread runs."""
        while True:
            with self.turn:
                self.turn.wait_for(lambda: bool(self.runs) or self.done)
                if not self.runs:
                    return
                run = self.runs[0]
                frame = run[0]
                run[1] -= 1
                if run[1] == 0:
                    self.runs.popleft()
                self.turn.notify_all()
            try:
                self.send(frame)
            except BaseException as exc:  # noqa: BLE001  (handed to the stepper, which raises it at its next frame)
                with self.turn:
                    self.failed = exc
                    self.turn.notify_all()
                return

    @classmethod
    @contextmanager
    def feeding(cls, send: Callable[[bytes], None]) -> Iterator[LosslessPipe]:
        """A pipe whose feeder sends through `send`, which has sent every frame once the block ends.

        A block that raises drops what has not been sent and leaves the feeder to end on its own once
        the encode it writes into has been stopped.
        """
        pipe = cls(send)
        feeder = threading.Thread(target=contextvars.copy_context().run, args=(pipe.feed,), daemon=True)
        feeder.start()
        try:
            yield pipe
        except BaseException:
            with pipe.turn:
                pipe.runs.clear()
                pipe.done = True
                pipe.turn.notify_all()
            raise
        with pipe.turn:
            pipe.done = True
            pipe.turn.notify_all()
        feeder.join()
        pipe.raise_failed()


Area = tuple[int, int, int, int]
"""A rectangle of a captured frame in its own pixels, as its left, top, right and bottom edges."""


@dataclass(frozen=True)
class Unexplained:
    """Undeclared change the steps since a capture named: the first element, its second, and where it all lies.

    `area` is every changed element as the page laid it out, or None when one of them is laid out
    nowhere, so the whole frame stands for it. Only a frame that differs inside it shows the change,
    because the same step may run a cue that draws somewhere else.
    """

    at: float
    what: str
    area: Area | None

    @classmethod
    def of(cls, seconds: float, said: object) -> Unexplained | None:
        """What one step said the page changed undeclared, or None when it named nothing."""
        if not isinstance(said, dict) or not isinstance(said.get("what"), str):
            return None
        box = said.get("box")
        if not isinstance(box, dict) or not all(isinstance(box.get(k), (int, float)) for k in "xywh"):
            return cls(seconds, said["what"], None)
        left, top = math.floor(box["x"]), math.floor(box["y"])
        return cls(seconds, said["what"], (left, top, math.ceil(box["x"] + box["w"]), math.ceil(box["y"] + box["h"])))

    def joined(self, later: Unexplained) -> Unexplained:
        """This change and one a later step named before the same capture, first named first, in one area."""
        if self.area is None or later.area is None:
            return Unexplained(self.at, self.what, None)
        (a, b, c, d), (e, f, g, h) = self.area, later.area
        return Unexplained(self.at, self.what, (min(a, e), min(b, f), max(c, g), max(d, h)))

    def shown(self, before: bytes, after: bytes) -> bool:
        """Whether two captured frames differ where this change lies, which a frame differing elsewhere does not."""
        if before == after:
            return False
        if self.area is None:
            return True
        left, top, right, bottom = self.area
        crop = f"crop={right - left}:{bottom - top}:{left}:{top},format=rgba"
        with tempfile.TemporaryDirectory() as work:
            frames = [Path(work, "before.png"), Path(work, "after.png")]
            for path, shot in zip(frames, (before, after), strict=True):
                path.write_bytes(shot)
            pixels = ffmpeg.raw(
                "-i", str(frames[0]), "-i", str(frames[1]),
                "-filter_complex", f"[0:v]{crop}[a];[1:v]{crop}[b];[a][b]vstack",
                "-frames:v", "1", "-f", "rawvideo", "-",
            )  # fmt: skip
        half = len(pixels) // 2
        return pixels[:half] != pixels[half:]


@dataclass
class Stepper:
    """One loaded page held still on Playwright's clock, which shows any frame it is asked for by its index.

    A frame is addressed by its index and drawn from its second alone. The page's timers are moved to
    frame `k`'s own millisecond from wherever they stand, the runtime steps the section clock to
    `k / fps`, which runs whatever is due by then in the order it was due, and nothing carries over from
    the frame before but the work already due. So frame `k` reached directly is the frame stepping every
    frame before it reaches, which is the seam a render split across workers or done out of order needs.

    A step answers only once the page has drawn it, so nothing the browser does on its own time, a
    rendering update, a font fetch or an image decode, decides what a captured frame shows. A page
    that has not drawn a step within `pages.DEADLINE_SECONDS`, the deadline every call into a page
    has, stops the recording at that frame.

    Every step also answers whether the picture may have changed, and the stepper keeps whether any
    step has since the last capture, so `record` can write a frame where none did again rather than
    capture it. It answers the earliest second anything can next change as well, which is how `record`
    writes the frames of a still stretch again without stepping the page to each.

    A step also names an element a script changed since the step before, outside every cue, frame
    hook and animation and inside nothing the page declared as moving. The stepper counts it as
    undeclared change once a captured frame differs from the frame before it, so a change no pixel
    shows is never reported.
    """

    page: Page
    fps: int
    ticked: int = 0
    """How many milliseconds the page's timers have moved since the clock was held."""
    due: float | None = 0.0
    """The second the page's next task is due, as its last step answered, or None when nothing is owed."""
    changed: bool = True
    """Whether any step since the last capture answered that the picture may have changed."""
    still_until: float | None = 0.0
    """The earliest second anything can next change, as the last step answered, or None when nothing ever will."""
    shown: bytes | None = None
    """The last frame captured, which an audited skip is compared with."""
    captured: int = 0
    """How many frames this stepper captured."""
    repeated: int = 0
    """How many frames this stepper wrote again without a capture."""
    steps: int = 0
    """How many times this stepper moved the page, a task's own second included."""
    unexplained: Unexplained | None = None
    """The undeclared change every step since the last capture named, with the second the first one named it."""
    undeclared: dict[str, UndeclaredChange] = field(default_factory=dict)
    """Every element undeclared change was seen in, keyed by how the page names it, in the order first seen."""

    def millisecond(self, seconds: float) -> int:
        """The whole millisecond a section second sits at on the page's timers.

        A frame at thirty or sixty a second is not a whole millisecond, so every frame is placed at its
        own rounded millisecond rather than a rounded step from the one before, and never drifts.
        """
        return round(seconds * MILLISECONDS)

    def hold(self) -> None:
        """Stop the page's clock a moment ahead of where it stands, which is where the section starts.

        The clock is held only once every animation the page has set playing has started, so the first
        step pauses each one where it is sought rather than where the browser was about to start it.
        """
        with driving("could not hold the page's clock"):
            self.page.evaluate(STARTED_JS, DEADLINE_SECONDS)
            ahead = float(self.page.evaluate(NOW_JS)) + HOLD_LEAD_MS
            # Playwright reads a number handed to `pause_at` as seconds, and the page counts milliseconds.
            self.page.clock.pause_at(ahead / MILLISECONDS)
        self.ticked = 0
        self.due = 0.0
        self.changed = True
        self.still_until = 0.0
        self.shown = None
        self.unexplained = None

    def step(self, seconds: float) -> None:
        """Move the page's timers and section clock to `seconds`, wait until it is drawn, and learn what it answered."""
        at = self.millisecond(seconds)
        if at < self.ticked:
            raise ToolError(f"second {seconds:g} is behind the page, which has already shown {self.ticked} ms.")
        if at > self.ticked:
            self.page.clock.run_for(at - self.ticked)
            self.ticked = at
        answer = self.page.evaluate(STEP_JS, [seconds, DEADLINE_SECONDS])
        self.steps += 1
        due = answer.get("due") if isinstance(answer, dict) else None
        self.due = float(due) if isinstance(due, (int, float)) else None
        # Anything but an answer that plainly says nothing changed is a change, so a page cannot skip by accident.
        self.changed = self.changed or not isinstance(answer, dict) or answer.get("changed") is not False
        # Likewise anything but a second or a plain null is no still stretch at all.
        still = answer.get("stillUntil", seconds) if isinstance(answer, dict) else seconds
        self.still_until = None if still is None else float(still) if isinstance(still, (int, float)) else seconds
        seen = Unexplained.of(seconds, answer.get("undeclared") if isinstance(answer, dict) else None)
        if seen is not None:
            self.unexplained = seen if self.unexplained is None else self.unexplained.joined(seen)

    def advance(self, k: int) -> bool:
        """Step the page to frame `k`, and answer whether its picture may have changed since the last capture.

        Every task due before the frame's second is stepped to on its own second first, so a timer a
        cue sets runs from the cue and not from the frame after it, whichever frame came before. A frame
        before one already shown cannot be reached, because a timer that fired stays fired.
        """
        seconds = k / self.fps
        with driving(f"the page stopped at frame {k}"):
            while self.due is not None and self.due < seconds:
                self.step(self.due)
            self.step(seconds)
        return self.changed

    def capture(self, k: int) -> bytes:
        """What the page shows now as a PNG, which is frame `k` once the page has been advanced to it."""
        with driving(f"the page stopped at frame {k}"):
            shot = self.cdp().send("Page.captureScreenshot", CAPTURE)
        before, self.shown = self.shown, base64.b64decode(shot["data"])
        unexplained = self.unexplained
        if unexplained is not None and before is not None and unexplained.shown(before, self.shown):
            self.seen(unexplained.at, unexplained.what)
        self.unexplained = None
        self.changed = False
        self.captured += 1
        return self.shown

    def seen(self, seconds: float, what: str) -> None:
        """Count one frame that showed undeclared change made in `what`, first seen at `seconds`."""
        row = self.undeclared.get(what)
        at = round(seconds, SECOND_DIGITS) if row is None else row.at
        self.undeclared[what] = UndeclaredChange(what=what, at=at, frames=1 if row is None else row.frames + 1)

    def frame(self, k: int) -> bytes:
        """Frame `k` as a PNG, which is the page at second `k / fps` on its section clock, always captured."""
        self.advance(k)
        return self.capture(k)

    def still_through(self, k: int, count: int) -> int:
        """The last frame before `count` that is surely frame `k` again, by what the step to frame `k` answered.

        That is the frame before the first whose millisecond reaches the second the next change is due
        at, or the section's last frame when nothing will change again.
        """
        if self.still_until is None:
            return count - 1
        lands = self.millisecond(self.still_until) - STILL_SLACK_MS
        j = max(k + 1, math.ceil(lands * self.fps / MILLISECONDS))
        while j > k + 1 and self.millisecond((j - 1) / self.fps) >= lands:
            j -= 1
        while self.millisecond(j / self.fps) < lands:
            j += 1
        return min(j - 1, count - 1)

    def audit(self, k: int) -> None:
        """Capture frame `k`, which every step called still, and stop the recording if it is not the frame before."""
        before = self.shown
        if self.capture(k) != before:
            raise ToolError(
                f"frame {k} differs from the frame before, though every step since said nothing on the page changed.",
                hint=(
                    "Draw what changed from a frame hook (DeckTalk.frame) or declare it as a loop, or record "
                    "every frame with --set record.skip_still=false."
                ),
            )

    def record(
        self, frames: range, sink: FrameSink, check: Callable[[], None], *, skip_still: bool, audit_every: int
    ) -> None:
        """Write each frame of `frames` into `sink` in order, asking `check` before each, so a stopped run stops.

        A range that starts after frame zero is a piece of a section. Its first frame is always
        captured, and the frame before it is captured first and not written, so that first frame is
        judged against the frame before it as a recording of the whole section judges it. A still
        stretch ends at the range's last frame.

        With `skip_still`, a frame no step since the last capture called changed is written again
        rather than captured, and so is every frame of a still stretch, without a step: the page is
        stepped next to the stretch's last frame, which keeps the page's own seconds where the frames
        are, and then on as before. With `audit_every` above zero, every that many such frames is
        stepped and captured anyway and compared with the frame before, which stops the recording on
        the first that differs.
        """
        skipped = 0
        k, count = frames.start, frames.stop
        if k > 0:
            self.frame(k - 1)
        while k < count:
            check()
            if self.advance(k) or not skip_still or self.shown is None or k == frames.start:
                sink.write(self.capture(k))
            else:
                skipped += 1
                if audit_every and skipped % audit_every == 0:
                    self.audit(k)
                self.repeated += 1
                sink.repeat()
            if not skip_still:
                k += 1
                continue
            across = self.still_through(k, count)
            if audit_every:
                across = min(across, k + audit_every - skipped % audit_every)
            for _ in range(k + 1, across):
                check()
                skipped += 1
                self.repeated += 1
                sink.repeat()
            k = max(k + 1, across)

    def cdp(self) -> CDPSession:
        """The DevTools session frames are captured over, opened once for the page."""
        if self.session is None:
            self.session = self.page.context.new_cdp_session(self.page)
        return self.session

    session: CDPSession | None = None


def frame_count(seconds: float, fps: int) -> int:
    """How many frames a span holds at a rate, which is the span rounded to the nearest frame."""
    return round(seconds * fps)


def encoding(out: Path, fps: int) -> list[str]:
    """The arguments of the one encode the frames of `out` are piped into."""
    return ["-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-", *LOSSLESS, str(out)]


@contextmanager
def stepping(
    chromium: Chromium,
    allowed: Allowed,
    *,
    width: int,
    height: int,
    color_scheme: str,
    motion: MotionConfig,
    documents: Mapping[str, bytes] | None = None,
    clips: ClipFrames | None = None,
) -> Iterator[tuple[Page, Assets]]:
    """A page on Playwright's clock in a context of its own, and what it loads, closed however this ends.

    `clips` decodes every pane clip the page plays, and is stopped with the context.
    """
    with driving("could not open a recording context"):
        context = chromium.browser.new_context(**view(width, height, color_scheme, motion))
    try:
        assets = route_pages(context, allowed, documents, trusted=chromium.trusted, clips=clips)
        instrument(context, *motion_scripts(motion))
        with driving("could not open a page"):
            page = context.new_page()
            page.clock.install()
        yield page, assets
    finally:
        with driving("could not close the recording context"):
            context.close()
        if clips is not None:
            clips.close()


@dataclass(frozen=True)
class Piece:
    """One range of a section's frames, recorded into a file of its own, and what its page said while it was."""

    path: Path
    frames: range
    url: str
    fps: int
    assets: tuple[str, ...]
    external: tuple[str, ...]
    missing: tuple[str, ...]
    load_seconds: float
    page_errors: tuple[str, ...]
    console: tuple[tuple[str, str], ...]
    report: PageReport
    undeclared: tuple[UndeclaredChange, ...]
    captured: int
    """How many frames the piece's page was captured on, the frame before its first included."""
    steps: int
    """How many times the piece's page was moved, the seconds stepped before its first frame included."""


def record_piece(
    chromium: Chromium,
    url: str,
    frames: range,
    out: Path,
    *,
    allowed: Allowed,
    width: int,
    height: int,
    fps: int,
    skip_still: bool,
    color_scheme: str,
    motion: MotionConfig,
    documents: Mapping[str, bytes] | None = None,
    check: Callable[[], None] = lambda: None,
) -> Piece:
    """Record frames `frames` of `url` in a page of their own into `out`, which starts on its first frame.

    `allowed` is what the local origin may answer with, so the page may fetch its own files, the log
    can name every one of them, and a page reaching for the script or for `.env` is turned away. The
    page is stepped to the first frame through the seconds work is due alone, so a piece that starts
    late in a section costs its page load and those steps, not every frame before it.
    `skip_still` is `[record] skip_still`, which decides whether a frame nothing changed on is written
    again rather than captured, and `SKIP_AUDIT_EVERY` how often one is captured anyway to prove it.
    The page is held once it says it is ready, which is once its fonts, the promises its author
    handed the runtime and its scene are in place, and every step after that waits for the page to
    draw it.

    The video is drafted beside `out` and moved onto it once the page has been read, so a piece on
    disk is always whole.
    """
    with (
        drafted(out) as partial,
        stepping(
            chromium,
            allowed,
            width=width,
            height=height,
            color_scheme=color_scheme,
            motion=motion,
            documents=documents,
            # Every pane clip is decoded at the film's rate and no larger than its frame, by the ffmpeg the encode runs.
            clips=ClipFrames(fps=fps, executable=ffmpeg.ffmpeg(), width=width, height=height),
        ) as (page, assets),
    ):
        caught: list[str] = []
        console: list[tuple[str, str]] = []
        page.on("pageerror", lambda err: caught.append(page_error_text(err)))
        page.on("console", lambda said: console.append((said.type, said.text)) if said.type in CONSOLE_KINDS else None)
        opened = time.monotonic()
        with driving(f"could not load {url}"):
            page.goto(url, wait_until="load")
        await_ready(page)
        loaded = time.monotonic()
        stepper = Stepper(page, fps)
        stepper.hold()
        with (
            ffmpeg.fed(*encoding(partial, fps), what=f"the encode of {out.name}") as write,
            LosslessPipe.feeding(write) as sink,
        ):
            stepper.record(frames, sink, check, skip_still=skip_still, audit_every=SKIP_AUDIT_EVERY)
        log.debug(
            "[page] %s  %d frames captured, %d written again, %d steps",
            out.stem,
            stepper.captured,
            stepper.repeated,
            stepper.steps,
            extra={"data": {"captured": stepper.captured, "repeated": stepper.repeated, "steps": stepper.steps}},
        )
        report = read_report(page, out.stem)
        errors = page_errors(page, caught, out.stem)
        if not partial.exists():
            raise ToolError(f"the encode of {out.name} wrote no file.")
    return Piece(
        path=out,
        frames=frames,
        url=url,
        fps=fps,
        assets=tuple(assets.paths),
        external=tuple(assets.external),
        missing=tuple(assets.missing),
        load_seconds=round(loaded - opened, 3),
        page_errors=tuple(errors),
        console=tuple(console),
        report=report,
        undeclared=tuple(stepper.undeclared.values()),
        captured=stepper.captured,
        steps=stepper.steps,
    )


def _union[T](groups: Sequence[Sequence[T]]) -> tuple[T, ...]:
    """Every item of `groups`, once each, in the order the groups first hold it."""
    return tuple(dict.fromkeys(item for group in groups for item in group))


def _undeclared(groups: Sequence[Sequence[UndeclaredChange]]) -> tuple[UndeclaredChange, ...]:
    """Each element undeclared change was seen in once, first seen at the earliest second, on every frame it showed."""
    rows: dict[str, UndeclaredChange] = {}
    for group in groups:
        for row in group:
            seen = rows.get(row.what)
            rows[row.what] = (
                row
                if seen is None
                else UndeclaredChange(what=row.what, at=min(seen.at, row.at), frames=seen.frames + row.frames)
            )
    return tuple(rows.values())


def join(pieces: Sequence[Piece], out: Path, *, seconds: float, name: str) -> Recording:
    """Join the pieces of one section into `out`, in frame order, and give back what their pages said as one.

    The pieces are lossless streams written with the same parameters, each starting on a keyframe,
    so they are joined by copying their packets, and the frames of the join are the frames of the
    pieces. One piece is moved onto `out` as it is, and joined pieces are deleted. `out` is the draft
    the caller moves onto the section's video, and `name` is what the lines say for that video.

    Each page loaded the section, stepped through the seconds work was due before its first frame,
    and drew its own frames, so what the pages said is joined as `pagereport.joined` joins it, a
    line a page printed is kept once however many pages printed it, and the load is the first page's.
    """
    ordered = sorted(pieces, key=lambda piece: piece.frames.start)
    if not ordered:
        raise ToolError(f"{name} has no piece to join.")
    if len(ordered) == 1:
        ordered[0].path.replace(out)
    else:
        listing = out.with_suffix(".pieces.txt")
        listing.write_text(ffmpeg.concat_list(piece.path.resolve() for piece in ordered), encoding="utf-8")
        try:
            ffmpeg.run(*ffmpeg.concat_source(listing), "-c", "copy", str(out))
        finally:
            listing.unlink(missing_ok=True)
        for piece in ordered:
            piece.path.unlink(missing_ok=True)
    first = ordered[0]
    steps = sum(piece.steps for piece in ordered)
    log.debug(
        "[page] %s  joined from %d pieces, %d frames captured, %d steps",
        name,
        len(ordered),
        sum(piece.captured for piece in ordered),
        steps,
        extra={"data": {"pieces": len(ordered), "steps": steps}},
    )
    for kind, text in _union([piece.console for piece in ordered]):
        # A console.error is the author telling themselves something broke, so it reaches the terminal.
        level = logging.WARNING if kind == "error" else logging.DEBUG
        log.log(level, "[page] %s  console %s: %s", name, kind, text, extra={"data": {"kind": kind, "text": text}})
    return Recording(
        url=first.url,
        assets=_union([piece.assets for piece in ordered]),
        external=_union([piece.external for piece in ordered]),
        missing=_union([piece.missing for piece in ordered]),
        requested_seconds=round(seconds, 3),
        fps=first.fps,
        load_seconds=first.load_seconds,
        page_errors=_union([piece.page_errors for piece in ordered]),
        report=joined([piece.report for piece in ordered]),
        undeclared=_undeclared([piece.undeclared for piece in ordered]),
        pieces=tuple(piece.frames.start for piece in ordered),
        steps=steps,
    )


__all__ = [
    "CAPTURE",
    "LOSSLESS",
    "SKIP_AUDIT_EVERY",
    "FrameSink",
    "LosslessPipe",
    "Piece",
    "Stepper",
    "encoding",
    "frame_count",
    "join",
    "record_piece",
    "stepping",
]
