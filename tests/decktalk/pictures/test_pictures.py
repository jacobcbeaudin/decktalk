"""The pictures a run opens: the canvas, the source chosen per section, the lazy browser and the held page."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from playwright.sync_api import Error as PlaywrightError

from decktalk.errors import ToolError
from decktalk.inputs import Inputs, PageSection, RenderSection
from decktalk.media import pages, stepping
from decktalk.media.origin import Assets
from decktalk.pictures import (
    PAGE_NAME,
    WHOLE_PAGE_SECONDS,
    Canvas,
    CommandRenderer,
    PageStepper,
    ProjectPage,
    Shot,
    pictures,
    splits,
)
from decktalk.settings import Settings
from support.fakes import Browsed, FakePage
from support.pages import a_project
from support.projects import load_project
from support.runs import a_run

WRITES_A_VIDEO = "import pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(b'video')"
"""A render section's program that writes its video and nothing else."""

RENDERED = f"""
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
render_command = ["{{python}}", "-c", "{WRITES_A_VIDEO}", "{{out}}"]
"""
"""A page section beside a render section, which is one of each kind of picture."""

STOPPED_SECONDS = 0.2
"""A page deadline short enough that a page that never answers is closed at once."""


def page_and_render(tmp_path: Path) -> tuple[Inputs, PageSection, RenderSection]:
    inputs = load_project(tmp_path, RENDERED, page="<div data-scene='1'></div>")
    page, render = inputs.document.recorded_sections
    assert isinstance(page, PageSection) and isinstance(render, RenderSection)
    return inputs, page, render


# ---- the canvas -------------------------------------------------------------------------------------


def test_the_canvas_is_the_frame_the_settings_draw_on() -> None:
    settings = Settings()
    canvas = Canvas.of(settings)
    assert (canvas.width, canvas.height, canvas.fps) == (
        settings.video.width,
        settings.video.height,
        settings.video.fps,
    )
    assert canvas.color_scheme == settings.record.color_scheme
    assert canvas.motion == settings.motion
    assert canvas.size == f"{settings.video.width}x{settings.video.height}@{settings.video.fps}"


# ---- one source per kind of section -----------------------------------------------------------------


def test_a_page_is_stepped_and_splits_and_a_program_draws_its_whole_video_at_once(tmp_path: Path) -> None:
    inputs, page, render = page_and_render(tmp_path)
    assert splits(page) and not splits(render)
    with pictures(inputs, a_run(tmp_path)) as made:
        assert isinstance(made.source(Shot(page, 1.0, "url"), threads=1), PageStepper)
        assert isinstance(made.source(Shot(render, 1.0, "render:x", {}), threads=1), CommandRenderer)


def test_a_run_whose_pictures_a_program_draws_launches_no_browser(tmp_path: Path, monkeypatch) -> None:
    """Chromium is launched the first time a page is asked for, so a render section never fetches or starts it."""
    browsed = Browsed().install(monkeypatch)
    inputs, _page, render = page_and_render(tmp_path)
    shot = Shot(render, 1.0, "render:x", {})
    with pictures(inputs, a_run(tmp_path)) as made:
        source = made.source(shot, threads=1)
        drawn = source.piece(range(25), tmp_path / ".02.piece0.mp4", lambda: None)
        recording = source.join([drawn], tmp_path / "02.mp4")
    assert browsed.policies == []
    assert (tmp_path / "02.mp4").read_bytes() == b"video"
    assert not drawn.path.exists()
    assert (recording.url, recording.requested_seconds, recording.assets) == ("render:x", 1.0, ())


def test_a_page_section_is_stepped_on_the_canvas_in_the_runs_one_browser(tmp_path: Path, monkeypatch) -> None:
    browsed = Browsed().install(monkeypatch)
    asked: list[dict[str, object]] = []

    def record_piece(_chromium: object, url: str, frames: range, out: Path, **named: object) -> object:
        asked.append({"url": url, "frames": frames, "out": out, **named})
        return object()

    monkeypatch.setattr(stepping, "record_piece", record_piece)
    inputs, page, _render = page_and_render(tmp_path)
    with pictures(inputs, a_run(tmp_path)) as made:
        source = made.source(Shot(page, 1.0, "http://project.localhost/deck/index.html"), threads=3)
        source.piece(range(10), tmp_path / "a.mp4", lambda: None)
        source.piece(range(10, 20), tmp_path / "b.mp4", lambda: None)
    canvas = Canvas.of(inputs.settings)
    assert browsed.policies == ["trusted"], "one browser for every page the run asks for"
    assert [(one["width"], one["height"], one["fps"], one["threads"]) for one in asked] == [
        (canvas.width, canvas.height, canvas.fps, 3)
    ] * 2
    assert asked[0]["name"] == "section 1"


# ---- a page of the project ---------------------------------------------------------------------------


def test_a_page_the_browser_will_not_load_is_a_tool_failure_and_not_a_bug(tmp_path: Path, monkeypatch) -> None:
    """A page whose navigation fails is the tool failing, so it is never reported as INTERNAL."""

    class Unreachable(FakePage):
        def goto(self, url: str, **_kwargs: object) -> None:
            raise PlaywrightError(f"net::ERR_ABORTED at {url}\nCall log:")

    load = pages.load
    Browsed(page=Unreachable()).install(monkeypatch)
    monkeypatch.setattr(pages, "load", load)
    inputs = a_project(tmp_path)
    with pictures(inputs, a_run(tmp_path)) as made, pytest.raises(ToolError, match=r"^could not load http"):
        made.page().reports(["deck/index.html"])


class Silent(FakePage):
    """A page whose every call waits until the page is closed under it, which is what a spinning page does."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = threading.Event()

    def evaluate(self, script: str, *_args: object) -> object:
        del script
        assert self.closed.wait(STOPPED_SECONDS * 50), "the watchdog never closed the page"
        raise PlaywrightError("Target page, context or browser has been closed")


def test_a_page_that_stops_answering_is_closed_and_ends_the_call_that_carried_on_without_it(tmp_path) -> None:
    """Waiting to be ready carries on without an answer, so the next step would ask a closed page.

    The page is held to its deadline from outside, so the wait ends when the watchdog closes it, and
    the call fails with the watchdog's sentence rather than carrying on.
    """
    inputs = a_project(tmp_path)
    page = Silent()
    with pages.Watchdog.watching(
        page.closed.set, name=PAGE_NAME, call_seconds=STOPPED_SECONDS, whole_seconds=WHOLE_PAGE_SECONDS
    ) as watch:
        held = ProjectPage(inputs, page.page(), Assets(tmp_path), watch)
        with pytest.raises(ToolError) as raised:
            held.report("deck/index.html")
    assert str(raised.value) == (
        f"{PAGE_NAME} stopped answering at the wait for deck/index.html to be ready: no answer in "
        f"{STOPPED_SECONDS:g} seconds, so its page was closed."
    )


def test_a_caption_page_is_loaded_once_for_every_caption_it_draws(tmp_path: Path, monkeypatch) -> None:
    browsed = Browsed().install(monkeypatch)
    monkeypatch.setattr(pages, "caption_frame", lambda _page, caption, out: out.write_text(str(caption)))
    inputs = a_project(tmp_path)
    with pictures(inputs, a_run(tmp_path)) as made:
        page = made.page()
        for line in ("one", "two"):
            page.caption("deck/index.html", {"lines": [line]}, tmp_path / f"{line}.png")
    assert browsed.loaded == ["deck/index.html"]
    assert (tmp_path / "two.png").read_text() == "{'lines': ['two']}"


def test_a_program_that_fails_names_its_section(tmp_path: Path) -> None:
    inputs = load_project(
        tmp_path, RENDERED.replace(WRITES_A_VIDEO, "import sys; sys.exit(2)"), page="<div data-scene='1'></div>"
    )
    render = inputs.document.recorded_sections[1]
    with pictures(inputs, a_run(tmp_path)) as made, pytest.raises(ToolError, match="section 2"):
        made.source(Shot(render, 1.0, "render:x", {}), threads=1).piece(range(1), tmp_path / "x.mp4", lambda: None)
