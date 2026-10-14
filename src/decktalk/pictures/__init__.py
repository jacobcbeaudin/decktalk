"""Every picture a run makes of a project: a section's video, a frozen state, a caption, a poster and a slate.

    __init__.py  the canvas, the pictures a run opens, a project page and the two sources of a section's video
    command.py   the command renderer: a section a program draws, handed its motion document
    address.py   where a page section is opened, for the recorder and for each frozen state of it
    sheet.py     frozen states drawn under one directory, the frames the project keeps, and the contact sheet

A run opens its pictures once, with `pictures(inputs, run)`, and asks them for what it needs. A
section's video comes from one of two sources behind one interface, `PictureSource`: the page
stepper, which records a page section one frame at a time in Chromium, and the command renderer,
which runs the program a render section names. Each is handed the section, its span, its words and
its cue seconds, drawn on the run's canvas, and hands back a video whose frame zero is second zero of
the section with the report of what moved in it, so `assemble` and `verify` read one shape whoever
drew the picture. `Pictures.source` chooses the source by the kind of section, so no stage branches
on it.

Chromium is launched the first time a page is asked for and never otherwise, so a run whose sections
a program draws, or whose stills are all kept, starts no browser and fetches none. Every project page
opened through here carries the probe, is served only what the project serves, and is drawn on the
canvas, and every call into it is held to `[record] page_deadline_seconds` by a `Watchdog` that closes
the page from outside when it does not answer, so a page that spins its main thread ends a run with a
TOOL error rather than holding it open.

The canvas is the one place the frame's size, its rate, its colour scheme and the motion it renders
with are read, so every picture a run makes is drawn on one frame. The size is `[video] size`, and a
page opened on the canvas is laid out on a canvas of the frame's own shape, which `page.layout`
gives and the page reads as `--dt-width` and `--dt-height`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Protocol

from playwright.sync_api import Page

from decktalk.inputs import Inputs, RecordedSection, RenderSection
from decktalk.machine.run import Run
from decktalk.media import browser, pages, stepping
from decktalk.media.browser import Chromium
from decktalk.media.origin import Allowed, Assets, page_url
from decktalk.media.pagereport import PageReport, Recording
from decktalk.page import SECOND_DIGITS, Q
from decktalk.pictures import command
from decktalk.pictures.address import CAPTION_BAND
from decktalk.settings import MotionConfig, Settings

WHOLE_PAGE_SECONDS = math.inf
"""Truth: a project page has no deadline as a whole, because it draws as many states as its run asks for.

Each call into it has its own deadline, which is what stops a page that has stopped answering. A
recording's page has a deadline as a whole as well, which `stepping` reckons from its frames.
"""

PAGE_NAME = "a page of the project"
"""What a stopped project page is called, which the call it was stopped at follows with the file it was showing."""


@dataclass(frozen=True)
class Canvas:
    """The frame every picture of a run is drawn on: its size, its rate, its colour scheme and its motion."""

    width: int
    height: int
    fps: int
    color_scheme: str
    motion: MotionConfig

    @classmethod
    def of(cls, settings: Settings) -> Canvas:
        """The canvas a project's settings draw on."""
        video = settings.video
        width, height = video.frame
        return cls(width, height, video.fps, settings.record.color_scheme, settings.motion)

    @property
    def size(self) -> str:
        """The frame as `WIDTHxHEIGHT@FPS`, which is how a recording's key names it."""
        return f"{self.width}x{self.height}@{self.fps}"


@dataclass(frozen=True)
class Shot:
    """What one section's video is drawn from: the section, its span, and what its source reads.

    `url` is the page a page section is opened at, or the mark a render section's recording names
    in its place. `document` is the motion document a render section's program reads, and None for
    a page section.
    """

    section: RecordedSection
    seconds: float
    url: str
    document: Mapping[str, object] | None = None


def motion_document(
    inputs: Inputs, section: RecordedSection, cues: Mapping[str, float], seconds: float
) -> dict[str, object]:
    """The motion document a render section's program reads, on the run's canvas, from its words and its cue seconds."""
    canvas = Canvas.of(inputs.settings)
    return command.motion_document(
        fps=canvas.fps,
        width=canvas.width,
        height=canvas.height,
        seconds=seconds,
        words=inputs.words(section.number).words,
        cues=cues,
    )


def splits(section: RecordedSection) -> bool:
    """Whether a section's frames may be drawn in several pieces at once, by several recorders.

    Frame `k` of a page is the page at second `k / fps` however it was reached, so a page's frames
    split, and a program draws its whole video in one run, so a render section's do not.
    """
    return not isinstance(section, RenderSection)


class PictureSource(Protocol):
    """Where one section's video comes from, which is the page stepper or the command renderer.

    A source draws the section's frames into pieces and joins its pieces into the section's video. It
    is handed the frames in several ranges by several recorders when the section `splits`, and all at
    once when it does not.
    """

    def piece(self, frames: range, out: Path, check: Callable[[], None]) -> stepping.Piece:
        """Draw `frames` of the section into `out`, asking `check` whether to stop."""
        ...

    def join(self, pieces: Sequence[stepping.Piece], out: Path) -> Recording:
        """Join every piece of the section into its video at `out`, and say what was drawn."""
        ...


@dataclass(frozen=True)
class PageStepper:
    """The page stepper: a page section recorded one frame at a time on Playwright's held clock."""

    pictures: Pictures
    shot: Shot
    threads: int
    """How many threads each piece's encode runs on, which is the same for every piece of a run."""

    def piece(self, frames: range, out: Path, check: Callable[[], None]) -> stepping.Piece:
        """Record `frames` of the page into `out` in a page of their own."""
        pictures = self.pictures
        canvas = pictures.canvas
        return stepping.record_piece(
            pictures.chromium(),
            self.shot.url,
            frames,
            out,
            allowed=pictures.allowed,
            width=canvas.width,
            height=canvas.height,
            fps=canvas.fps,
            record=pictures.inputs.settings.record,
            motion=canvas.motion,
            threads=self.threads,
            name=f"section {self.shot.section.number}",
            documents=pictures.inputs.documents(),
            check=check,
        )

    def join(self, pieces: Sequence[stepping.Piece], out: Path) -> Recording:
        """Join the pieces into the section's video with no encode."""
        return stepping.join(pieces, out, seconds=self.shot.seconds, name=self.shot.section.key)


@dataclass(frozen=True)
class CommandRenderer:
    """The command renderer: a render section's program, run once to write the whole of its video."""

    pictures: Pictures
    shot: Shot
    section: RenderSection

    def piece(self, frames: range, out: Path, check: Callable[[], None]) -> stepping.Piece:
        """Run the section's program, which writes every frame of its video into `out`."""
        check()
        inputs, shot = self.pictures.inputs, self.shot
        done = command.run_command(
            self.section.render_command,
            root=inputs.root,
            work=inputs.workspace.render_dir(self.section.key),
            out=out,
            document=shot.document or {},
            timeout=inputs.settings.tools.timeout_seconds,
            label=f"section {self.section.number}",
        )
        return stepping.Piece(
            path=out,
            frames=frames,
            url=shot.url,
            fps=self.pictures.canvas.fps,
            assets=(),
            external=(),
            missing=(),
            load_seconds=done.seconds,
            page_errors=(),
            console=(),
            report=PageReport(motions=done.motions),
            undeclared=(),
            captured=0,
            steps=0,
        )

    def join(self, pieces: Sequence[stepping.Piece], out: Path) -> Recording:
        """Move the one video the program wrote onto `out`, with the lanes it said it drew for verify to measure."""
        (drawn,) = pieces
        drawn.path.replace(out)
        return Recording(
            url=self.shot.url,
            assets=(),
            external=(),
            requested_seconds=round(self.shot.seconds, SECOND_DIGITS),
            fps=drawn.fps,
            load_seconds=drawn.load_seconds,
            page_errors=(),
            report=drawn.report,
        )


@dataclass(eq=False)
class ProjectPage:
    """One page of the project in the run's Chromium, every call into which is held to the page deadline.

    The page is served only what the project serves and carries the probe, so it draws what the
    recorder records. `assets` is every project file it loaded, which is what a still drawn on it is
    kept against.
    """

    inputs: Inputs
    page: Page
    assets: Assets
    watch: pages.Watchdog
    showing: str | None = None
    """The address the page was last pointed at, so a page drawing many captions loads once."""

    @contextmanager
    def held(self, doing: str) -> Generator[None]:
        """Hold one call into the page to its deadline, and fail with the watchdog's sentence once it has closed it.

        A call that swallows the page's failure, such as a wait to be ready that carries on without
        an answer, still fails here, because the page it would carry on with is closed.
        """
        with self.watch.call(doing):
            yield
        self.watch.check()

    def load(self, url: str, file: str) -> None:
        """Point the page at `url` and wait for it to be ready, each held to its deadline."""
        with self.held(f"the load of {file}"):
            pages.load(self.page, url)
        with self.held(f"the wait for {file} to be ready"):
            pages.await_ready(self.page)
        self.showing = url

    def report(self, file: str) -> PageReport:
        """What one page of the project says about itself once it is loaded and ready."""
        self.load(page_url(file), file)
        with self.held(f"the report of {file}"):
            return pages.read_report(self.page, Path(file).stem)

    def reports(self, files: Sequence[str]) -> dict[str, PageReport]:
        """What each page says about itself, read once per file.

        A page the project has not got is absent from the map rather than mapped to nothing, so a
        caller can tell a page that published no scene from a page nobody could open.
        """
        return {named: self.report(named) for named in dict.fromkeys(files) if self.inputs.path(named).exists()}

    def still(self, file: str, query: Mapping[Q, str] | None, out: Path) -> PageReport:
        """Write one PNG of `file` at `query` once it has painted, and give back what the page reported."""
        with self.held(f"the still {out.name} of {file}"):
            report = pages.screenshot(self.page, page_url(file, query), out)
        self.showing = None
        return report

    def caption(self, file: str, caption: Mapping[str, object] | None, out: Path) -> None:
        """Write one caption as `file` draws it in its band, over a clear frame, to `out`."""
        url = page_url(file, {Q.CAPTION: CAPTION_BAND})
        if self.showing != url:
            self.load(url, file)
        with self.held(f"a caption of {file}"):
            pages.caption_frame(self.page, caption, out)


@dataclass(eq=False)
class Pictures:
    """The pictures one run makes, with the Chromium it launches the first time a page is asked for.

    Everything it opens, the browser and every page and watchdog, is closed with it.
    """

    inputs: Inputs
    spend: bool
    stack: ExitStack
    opened: Chromium | None = None

    @cached_property
    def canvas(self) -> Canvas:
        """The frame every picture of the run is drawn on."""
        return Canvas.of(self.inputs.settings)

    @cached_property
    def allowed(self) -> Allowed:
        """What the local origin answers a page of this project with."""
        return Allowed.of(self.inputs.root, self.inputs.served_paths())

    def chromium(self) -> Chromium:
        """The run's Chromium, launched under the page policy the project sets the first time it is asked for."""
        if self.opened is None:
            policy = self.inputs.settings.record.page_policy
            self.opened = self.stack.enter_context(
                browser.chromium(self.inputs.settings.tools.chromium, policy=policy, spend=self.spend)
            )
        return self.opened

    def source(self, shot: Shot, *, threads: int) -> PictureSource:
        """Where one section's video comes from, chosen by the kind of section it is."""
        if isinstance(shot.section, RenderSection):
            return CommandRenderer(self, shot, shot.section)
        return PageStepper(self, shot, threads)

    def page(self) -> ProjectPage:
        """A new page of this project, drawn on the canvas and held to the page deadline, closed with the run."""
        canvas = self.canvas
        page, assets = pages.open_page(
            self.chromium(),
            self.allowed,
            width=canvas.width,
            height=canvas.height,
            color_scheme=canvas.color_scheme,
            motion=canvas.motion,
            documents=self.inputs.documents(),
        )
        return ProjectPage(self.inputs, page, assets, self.watching(page))

    def slate(self, html: str, out: Path) -> Path:
        """Write a frame of DeckTalk's own `html`, on the canvas, to `out` as a PNG."""
        canvas = self.canvas
        drawn = pages.open_document(self.chromium(), html, viewport={"width": canvas.width, "height": canvas.height})
        watch = self.watching(drawn)
        with watch.call("the wait for the slate to paint"):
            pages.await_painted(drawn)
        watch.check()
        with watch.call(f"the slate {out.name}"):
            pages.write_frame(drawn, out)
        return out

    def watching(self, page: Page) -> pages.Watchdog:
        """A watchdog over every call into `page`, running until the run closes its pictures."""
        return self.stack.enter_context(
            pages.Watchdog.watching(
                lambda: pages.close_soon(page.context),
                name=PAGE_NAME,
                call_seconds=self.inputs.settings.record.page_deadline_seconds,
                whole_seconds=WHOLE_PAGE_SECONDS,
            )
        )


@contextmanager
def pictures(inputs: Inputs, run: Run) -> Generator[Pictures]:
    """The pictures one run makes, opened on its project and closed, with any browser it launched, on exit."""
    with ExitStack() as stack:
        yield Pictures(inputs, run.spend, stack)


__all__ = [
    "PAGE_NAME",
    "WHOLE_PAGE_SECONDS",
    "Canvas",
    "CommandRenderer",
    "PageStepper",
    "PictureSource",
    "Pictures",
    "ProjectPage",
    "Shot",
    "motion_document",
    "pictures",
    "splits",
]
