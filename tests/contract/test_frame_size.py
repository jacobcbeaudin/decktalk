"""A project's frame size: one key naming a shape or a size, and the canvas of that shape every page is laid out on.

One project makes one video, so a square cut of a deck is the project copied by hand with `[video] size`
changed. That holds only if every picture a run makes reads the size from that key: the frame the
recorder draws, the canvas the page is laid out on and told of, the caption band, the slate, and the
starter's own stylesheet, each of which is checked here at the three named sizes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from decktalk import template
from decktalk.captions.layout import CAPTION_MAX_CHARS
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.media import browser, pages
from decktalk.media.origin import Allowed, page_url, route_pages
from decktalk.page import ENGINE_PATH, PREVIEW_CANVAS, Q, layout
from decktalk.pictures import Canvas, pictures
from decktalk.settings import BY_ID, FRAME_SIDE_MAX, FRAME_SIDE_MIN, MotionConfig, Settings
from decktalk.settings.layers import load
from decktalk.stages.assemble import slate
from decktalk.toolchain.assets import RUNTIME_FILE
from support.projects import load_project
from support.runs import a_run

PROJECT = """
[project]
name = "sized"

[video]
size = "{size}"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"
"""
"""A one-section project whose frame is `size`."""

PAGE = (
    f"<!doctype html><meta charset=utf-8><title>sized</title><script src='{ENGINE_PATH}{RUNTIME_FILE}'></script>"
    "<div data-scene='1'><template data-slide='1.1'><p data-in='open'>one</p></template></div>"
)
"""A page with the runtime on it and one slide, which is all a canvas needs to be built."""

FRAMES = {"wide": (1920, 1080), "square": (1080, 1080), "vertical": (1080, 1920)}
"""The frame each named size draws, which the founder named as the three a project chooses between."""

SEEN_JS = """() => {
  const root = document.documentElement, style = getComputedStyle(root);
  const canvas = document.getElementById("dt-canvas").getBoundingClientRect();
  return [style.getPropertyValue("--dt-width").trim(), style.getPropertyValue("--dt-height").trim(),
          root.getAttribute("data-dt-shape"), canvas.width, canvas.height];
}"""
"""What a page sees of its canvas: the two custom properties, the shape, and the canvas's box in the frame."""

BOXES_JS = """(selector) => [...document.querySelectorAll(selector)].map((el) => {
  const box = el.getBoundingClientRect();
  return { left: box.left, top: box.top, right: box.right, bottom: box.bottom, rows: el.getClientRects().length };
})"""
"""Every box `selector` finds on the page, in frame pixels, with how many rows of text each spans."""


def sized(tmp_path: Path, size: str) -> Inputs:
    """The one-section project at `size`, loaded as a stage is handed it."""
    return load_project(tmp_path, PROJECT.format(size=size), page=PAGE)


def inside(box: dict[str, Any], width: int, height: int) -> bool:
    """Whether a box sits wholly inside a frame `width` by `height`."""
    return box["left"] >= 0 and box["top"] >= 0 and box["right"] <= width and box["bottom"] <= height


# ---- the key ---------------------------------------------------------------------------------------


def test_a_project_draws_the_wide_full_hd_frame_unless_it_names_another():
    video = Settings().video
    assert (video.size, video.width, video.height) == ("wide", 1920, 1080)


@pytest.mark.parametrize(
    ("size", "frame"),
    [*FRAMES.items(), ("1280x720", (1280, 720)), ("720x1280", (720, 1280)), ("240x7680", (240, 7680))],
)
def test_a_size_names_the_frame_every_picture_of_the_run_is_drawn_on(size, frame):
    settings = load(machine={}, project={"video": {"size": size}}, environ={}).settings
    canvas = Canvas.of(settings)
    assert (canvas.width, canvas.height) == (settings.video.width, settings.video.height) == frame
    assert canvas.size == f"{frame[0]}x{frame[1]}@{settings.video.fps}"


@pytest.mark.parametrize("size", ["1081x1080", "1920x1079", "238x240", "7682x1080", "1920 x 1080", "Wide", "0x0", "4k"])
def test_a_size_that_is_odd_out_of_range_or_misspelled_is_refused_when_the_project_loads(size):
    with pytest.raises(InputError, match=r"video\.size"):
        load(machine={}, project={"video": {"size": size}}, environ={})


def test_the_two_keys_a_size_replaced_are_unknown():
    assert "video.width" not in BY_ID and "video.height" not in BY_ID


def test_an_explicit_size_admits_exactly_the_even_sides_between_the_bounds():
    """The range is a pattern so the loader and the schema agree, and every side it admits is checked here."""
    bounds = BY_ID["video.size"].bounds
    assert bounds is not None
    admitted = [side for side in range(FRAME_SIDE_MAX + 100) if bounds.holds(f"{side}x{FRAME_SIDE_MIN}")]
    assert admitted == list(range(FRAME_SIDE_MIN, FRAME_SIDE_MAX + 1, 2))
    assert [side for side in range(FRAME_SIDE_MAX + 100) if bounds.holds(f"{FRAME_SIDE_MIN}x{side}")] == admitted


# ---- the canvas a frame lays its page out on ---------------------------------------------------------


@pytest.mark.parametrize(
    ("frame", "canvas"),
    [
        ((1920, 1080), (1920, 1080)),
        ((1080, 1080), (1080, 1080)),
        ((1080, 1920), (1080, 1920)),
        ((640, 360), (1920, 1080)),
        ((3840, 2160), (1920, 1080)),
        ((720, 1280), (1080, 1920)),
        ((2000, 1000), (2160, 1080)),
    ],
)
def test_a_page_is_laid_out_on_a_canvas_of_its_frames_shape_whose_short_side_is_full_hd(frame, canvas):
    assert layout(*frame) == canvas


def test_a_page_is_told_its_canvas_before_any_script_of_its_own_runs():
    scripts = pages.page_scripts(1080, 1920, MotionConfig())
    told = [script for script in scripts if "--dt-width:1080px;--dt-height:1920px" in script]
    assert len(told) == 1, scripts


def test_a_previewed_page_asks_the_origin_for_its_canvas(tmp_path):
    inputs = sized(tmp_path, "vertical")
    assert json.loads(inputs.documents()[PREVIEW_CANVAS]) == {"width": 1080, "height": 1920}


# ---- what a page sees -----------------------------------------------------------------------------------


@pytest.mark.browser
@pytest.mark.parametrize(
    ("size", "seen"),
    [
        ("wide", ["1920px", "1080px", "wide"]),
        ("square", ["1080px", "1080px", "square"]),
        ("vertical", ["1080px", "1920px", "vertical"]),
        ("640x360", ["1920px", "1080px", "wide"]),
    ],
)
def test_a_page_sees_its_canvas_as_two_custom_properties_and_a_shape(tmp_path, size, seen):
    inputs = sized(tmp_path, size)
    canvas = Canvas.of(inputs.settings)
    with pictures(inputs, a_run(inputs.root)) as made:
        page = made.page()
        page.still("deck/index.html", {Q.FREEZE: "1.1"}, tmp_path / "still.png")
        got = page.page.evaluate(SEEN_JS)
    assert got[:3] == seen
    assert got[3:] == [canvas.width, canvas.height], "the canvas fills the frame"


@pytest.mark.browser
def test_a_page_previewed_in_a_browser_reads_its_canvas_from_the_origin(tmp_path):
    """A preview has no recorder to tell it its canvas, so it asks the origin, and lays out again on the answer."""
    inputs = sized(tmp_path, "square")
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        page = real.browser.new_page(viewport={"width": 600, "height": 600})
        route_pages(page, Allowed.of(inputs.root, inputs.served_paths()), inputs.documents(), trusted=True)
        page.goto(page_url("deck/index.html", {Q.FREEZE: "1.1"}), wait_until="load")
        page.wait_for_function("() => document.documentElement.getAttribute('data-dt-shape') === 'square'")
        got = page.evaluate(SEEN_JS)
    assert got == ["1080px", "1080px", "square", 600, 600]


# ---- what DeckTalk draws on the frame itself ------------------------------------------------------------

LINES = ("Every picture on the page waits for a word", "and the words are the ones the voice says.")
"""Two caption lines as long as a caption line is ever made, in an ordinary run of letters."""


@pytest.mark.browser
@pytest.mark.parametrize("size", list(FRAMES))
def test_a_caption_of_two_full_lines_fits_the_frame_in_two_rows(tmp_path, size):
    assert all(len(line) <= CAPTION_MAX_CHARS for line in LINES)
    assert max(len(line) for line in LINES) == CAPTION_MAX_CHARS
    inputs = sized(tmp_path, size)
    width, height = FRAMES[size]
    caption = {"lines": list(LINES), "voice": None, "turn": None, "spoken": [0, len(LINES)]}
    with pictures(inputs, a_run(inputs.root)) as made:
        page = made.page()
        page.caption("deck/index.html", caption, tmp_path / "caption.png")
        lines = page.page.evaluate(BOXES_JS, ".dt-caption-line")
    assert len(lines) == len(LINES)
    for line in lines:
        assert inside(line, width, height), (size, line)
        assert line["rows"] == 1, (size, line)


@pytest.mark.browser
@pytest.mark.parametrize("size", list(FRAMES))
def test_a_slate_fits_the_frame(tmp_path, size):
    inputs = sized(tmp_path, size)
    width, height = FRAMES[size]
    canvas = Canvas.of(inputs.settings)
    doc = slate.slate_html(
        canvas,
        title="A chapter whose name runs on for a while",
        sub="Your clip goes here",
        eyebrow="section 12: slate",
        foot="drop it at media/a-clip-with-a-long-descriptive-name.mp4 and run `decktalk assemble`",
        background="0x0e1116",
    )
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        page = pages.open_document(real, doc, viewport={"width": width, "height": height})
        boxes = page.evaluate(BOXES_JS, ".wrap > div, .foot")
    assert len(boxes) == 4
    for box in boxes:
        assert inside(box, width, height), (size, box)


# ---- the starter at every named size ------------------------------------------------------------------

STARTER_PAGE = "deck/index.html"
"""The starter's one page."""

CAPTION_ROOM = 192
"""The bottom of the canvas a cued element keeps out of, where two lines of caption sit, as the slide guide says."""

CUED_JS = """() => {
  const cued = [...document.querySelectorAll("#dt-canvas [data-in]")];
  return cued.filter((el) => !cued.some((other) => other !== el && other.contains(el))).map((el) => {
    const box = el.getBoundingClientRect();
    return { cue: el.getAttribute("data-in"), left: box.left, top: box.top, right: box.right, bottom: box.bottom };
  });
}"""
"""Every outermost cued element of the frozen slide, with its box in canvas pixels."""


def apart(one: dict[str, Any], other: dict[str, Any]) -> bool:
    """Whether two boxes share no pixel."""
    return (
        one["right"] <= other["left"]
        or other["right"] <= one["left"]
        or one["bottom"] <= other["top"]
        or other["bottom"] <= one["top"]
    )


@pytest.mark.browser
@pytest.mark.parametrize("size", list(FRAMES))
def test_every_slide_of_the_starter_fits_its_canvas_at_every_named_size(tmp_path: Path, size: str) -> None:
    root = tmp_path / "starter"
    template.write_project(root, name="starter", example_name=None, skills=False, force=False)
    inputs = Inputs.load(root, environ={}, overrides=(f"video.size={size}",))
    canvas = Canvas.of(inputs.settings)
    with pictures(inputs, a_run(inputs.root)) as made:
        page = made.page()
        slides = [slide for scene in page.report(STARTER_PAGE).catalog for slide in scene.elements]
        assert slides, "the starter declares slides"
        for slide in slides:
            page.still(STARTER_PAGE, {Q.FREEZE: slide}, tmp_path / f"{size}-{slide}.png")
            boxes = page.page.evaluate(CUED_JS)
            assert boxes, (size, slide)
            for box in boxes:
                assert box["left"] >= 0 and box["top"] >= 0, (size, slide, box)
                assert box["right"] <= canvas.width, (size, slide, box)
                assert box["bottom"] <= canvas.height - CAPTION_ROOM, (size, slide, box)
            for at, one in enumerate(boxes):
                for other in boxes[at + 1 :]:
                    assert apart(one, other), (size, slide, one, other)
