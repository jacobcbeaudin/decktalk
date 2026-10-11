"""The stepped recorder: frame `k` is the page at second `k / fps`, every time, in a real Chromium and a real encode.

These tests drive a real page through the recorder and decode what it wrote, so they need Chromium
and ffmpeg both and carry the `e2e` marker, which is the suite that has the two. The suite binds
no machine of its own, because its other tests drive the command line, so this module binds the one
whose tool cache holds both.
"""

from __future__ import annotations

import hashlib
import itertools
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

import pytest

from decktalk.artifacts.recordings import pane_landings
from decktalk.errors import ToolError
from decktalk.findings import Code
from decktalk.media import browser, ffmpeg, stepping
from decktalk.media.clipframes import landings_of
from decktalk.media.origin import Allowed, page_url
from decktalk.media.pagereport import Box, Recording
from decktalk.media.pages import await_painted
from decktalk.page import ENGINE_PATH, T0_SIGNAL, Q
from decktalk.settings import MotionConfig, RecordConfig
from decktalk.toolchain.assets import RUNTIME_FILE
from support.tools import machine_tools

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def tools() -> Iterator[None]:
    """The machine whose tool cache holds Chromium and ffmpeg, bound for every recording here."""
    with machine_tools():
        yield


WIDTH, HEIGHT, FPS = 320, 180, 25
"""A small frame at the default rate, which is all a test of the clock needs."""

WAIT = RecordConfig().frame_wait_seconds
"""How long a step may take to be drawn, which is the default a project records with."""

SKIP_STILL = RecordConfig().skip_still
"""Whether a frame nothing changed on is written again rather than captured, which is the default."""

AUDIT_EVERY = 1
"""Capture every frame the recorder would skip and compare it, which is the strictest audit a test can ask for."""

PIXELS_PER_SECOND = 100
"""How fast the fixture's bar grows, so frame `k` shows a bar `k * 100 / 25` pixels wide."""

SCENE = 'DeckTalk.scene(1, { slides: [{ id: "1.1", owns: ["go"], render: () => "<p>stepped</p>" }] });'


def fixture_page(tmp_path: Path, script: str, markup: str = "") -> Allowed:
    """A deck page that declares one scene, holds `markup` and runs `script`, served from the project's own origin."""
    deck = tmp_path / "deck"
    deck.mkdir(exist_ok=True)
    deck.joinpath("index.html").write_text(
        "<!doctype html><html lang=en><head><meta charset=utf-8><title>stepped</title>"
        "<style>html,body{margin:0;background:#000}"
        "#bar,#late{position:fixed;left:0;height:40px;background:#fff}#bar{top:0;width:0}"
        "#late{top:80px;width:100px;visibility:hidden}</style>"
        f'<script src="{ENGINE_PATH}{RUNTIME_FILE}"></script></head>'
        f"<body>{markup}<div id=bar></div><div id=late></div><script>{SCENE}{script}</script></body></html>",
        encoding="utf-8",
    )
    return Allowed.of(tmp_path, ["deck"])


URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0", Q.T0: T0_SIGNAL})


def record(
    allowed: Allowed,
    out: Path,
    seconds: float,
    *,
    url: str = URL,
    starts: tuple[int, ...] = (0,),
    skip_still: bool = SKIP_STILL,
    audit_every: int = AUDIT_EVERY,
) -> Recording:
    """Record `seconds` of the page into `out`, in one piece for each of `starts`, each in a page of its own."""
    count = stepping.frame_count(seconds, FPS)
    bounds = [*starts, count]
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        pieces = [
            stepping.record_piece(
                real,
                url,
                range(first, stop),
                out.with_name(f".{out.stem}.piece{first}{out.suffix}"),
                allowed=allowed,
                width=WIDTH,
                height=HEIGHT,
                fps=FPS,
                frame_wait_seconds=WAIT,
                skip_still=skip_still,
                skip_audit_every=audit_every,
                color_scheme="dark",
                motion=MotionConfig(),
            )  # fmt: skip
            for first, stop in itertools.pairwise(bounds)
        ]
    return stepping.join(pieces, out, seconds=seconds)


def luma_frames(video: Path) -> list[bytes]:
    """Every frame of `video` as its luma plane at the size it was recorded at."""
    raw = ffmpeg.raw(*ffmpeg.source(video), "-f", "rawvideo", "-pix_fmt", "gray", "-")
    size = WIDTH * HEIGHT
    return [raw[at : at + size] for at in range(0, len(raw), size)]


def lit_width(frame: bytes, row: int) -> int:
    """How many pixels of one row are lit, which is how wide a white bar on black is drawn."""
    line = frame[row * WIDTH : (row + 1) * WIDTH]
    return sum(1 for pixel in line if pixel > 128)


GROW = f"DeckTalk.frame((t) => {{ document.getElementById('bar').style.width = (t * {PIXELS_PER_SECOND}) + 'px'; }});"


def test_frame_k_shows_the_page_at_second_k_over_fps(tmp_path):
    """A bar drawn `t * 100` pixels wide is `4k` pixels wide on frame `k`, with no frame early, late or stalled."""
    out = tmp_path / "01.mp4"
    record(fixture_page(tmp_path, GROW), out, 1.0)
    frames = luma_frames(out)
    assert [lit_width(frame, 20) for frame in frames] == [k * PIXELS_PER_SECOND // FPS for k in range(FPS)]


def test_two_recordings_of_one_page_are_the_same_frames(tmp_path):
    """Nothing in a stepped recording depends on the machine's load, so the same page records the same pixels."""
    allowed = fixture_page(tmp_path, GROW)
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    record(allowed, first, 0.8)
    record(allowed, second, 0.8)
    digests = [[hashlib.sha256(frame).hexdigest() for frame in luma_frames(video)] for video in (first, second)]
    assert digests[0] == digests[1]
    assert len(set(digests[0])) == len(digests[0]), "every frame shows its own second"


def test_a_recording_holds_exactly_the_frames_its_span_rounds_to_and_starts_on_its_first(tmp_path):
    """The section clock starts on frame zero by construction, so there is no start to find and none to guess."""
    out = tmp_path / "01.mp4"
    recording = record(fixture_page(tmp_path, GROW), out, 1.23)
    assert len(luma_frames(out)) == stepping.frame_count(1.23, FPS) == 31
    assert lit_width(luma_frames(out)[0], 20) == 0
    assert recording.fps == FPS
    assert recording.report.mode == "record"


def test_a_timer_the_page_sets_fires_on_the_section_clock_and_not_the_wall_clock(tmp_path):
    """A cue at zero sets a one second timer, and the element it shows is on screen from frame 25 exactly."""
    show = "() => { document.getElementById('late').style.visibility = 'visible'; }"
    script = f"DeckTalk.on('1.1:go', () => setTimeout({show}, 1000));"
    out = tmp_path / "01.mp4"
    record(fixture_page(tmp_path, script), out, 1.2)
    shown = [lit_width(frame, 100) > 0 for frame in luma_frames(out)]
    assert shown.index(True) == FPS
    assert all(shown[FPS:])


def test_an_encode_that_fails_is_a_tool_error_naming_the_section_and_leaves_no_file(tmp_path, monkeypatch):
    """A broken encode is the tool failing, and a half-written video is never placed where a section's goes."""
    monkeypatch.setattr(stepping, "LOSSLESS", ("-c:v", "no-such-encoder"))
    out = tmp_path / "07.mp4"
    with pytest.raises(ToolError) as raised:
        record(fixture_page(tmp_path, GROW), out, 0.4)
    assert ".07.piece0.mp4" in str(raised.value)
    assert not out.exists()
    assert list(tmp_path.glob("*.part*")) == []


SEEKED = (
    GROW
    + "DeckTalk.on('1.1:go', () => setTimeout(() => {"
    + " document.getElementById('late').style.visibility = 'visible'; }, 400));"
    + "document.getElementById('late').animate([{ opacity: 0.2 }, { opacity: 1 }], { duration: 600 });"
)
"""A page whose frame `k` depends on a frame hook, a timer the section set and an animation, and on nothing else."""


def deck_page(tmp_path: Path, body: str) -> Allowed:
    """A deck page on black whose body is `body`, served from the project's own origin."""
    deck = tmp_path / "deck"
    deck.mkdir(exist_ok=True)
    deck.joinpath("index.html").write_text(
        "<!doctype html><html lang=en><head><meta charset=utf-8><title>deck</title>"
        "<style>html,body{margin:0;background:#000}</style>"
        f'<script src="{ENGINE_PATH}{RUNTIME_FILE}"></script></head><body>{body}</body></html>',
        encoding="utf-8",
    )
    return Allowed.of(tmp_path, ["deck"])


def a_frame(allowed: Allowed, k: int, *, walked: bool, url: str = URL) -> bytes:
    """Frame `k` of the fixture, reached by stepping every frame before it or by addressing it directly."""
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        opened = stepping.stepping(
            real, allowed, width=WIDTH, height=HEIGHT, color_scheme="dark", motion=MotionConfig()
        )
        with opened as (page, _assets):
            page.goto(url, wait_until="load")
            stepper = stepping.Stepper(page, FPS, WAIT)
            stepper.hold()
            if walked:
                sink = Frames()
                stepper.record(range(k + 1), sink, lambda: None, skip_still=False, audit_every=0)
                return sink.frames[-1]
            return stepper.frame(k)


def test_a_frame_addressed_by_its_index_is_the_frame_stepping_every_frame_before_it_reaches(tmp_path):
    """Frame `k` is a function of `k / fps` alone, so frames can later be rendered in any order or in parallel."""
    allowed = fixture_page(tmp_path, SEEKED)
    k = 17
    assert a_frame(allowed, k, walked=False) == a_frame(allowed, k, walked=True)


INTERRUPTED = (
    "<style>#fade{position:fixed;left:0;top:0;width:320px;height:180px;background:#fff;opacity:0;"
    "transition:opacity 1s linear}#fade.on{opacity:1}</style><div id=fade></div><script>"
    "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['go', 'back'], render: () => '' }] });"
    "DeckTalk.on('1.1:go', () => document.getElementById('fade').classList.add('on'))"
    ".on('1.1:back', () => document.getElementById('fade').classList.remove('on'));</script>"
)
"""A fade a cue starts and a later cue turns back halfway, which a CSS transition starts from where it stands."""

INTERRUPTED_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.1,1.1:back@0.5", Q.T0: T0_SIGNAL})


def test_a_transition_a_cue_interrupts_turns_back_from_its_value_at_that_cue_however_the_frame_was_reached(tmp_path):
    """The fade turns back from where it stands at the second `back` is due, not at the last second the page showed.

    Frame 15 reached by its index steps only the seconds the two cues are due, and reached by walking
    it steps every frame between them, so a turn that started from the last second shown would start
    from nothing on one path and from most of the way on the other.
    """
    allowed = deck_page(tmp_path, INTERRUPTED)
    k = 15
    assert a_frame(allowed, k, walked=False, url=INTERRUPTED_URL) == a_frame(
        allowed, k, walked=True, url=INTERRUPTED_URL
    )


MOVING = (
    "<style>@keyframes travel{from{transform:translateX(0px)}to{transform:translateX(960px)}}"
    "@keyframes spin{to{transform:rotate(360deg)}}"
    ".packet{position:absolute;left:0;top:120px;width:192px;height:192px;background:#fff}"
    ".ring{position:absolute;left:1500px;top:600px;width:192px;height:192px;background:#fff}</style>"
    '<div data-scene="1" data-name="moving"><template data-slide="1.1">'
    '<div class="packet" data-motion="travel" data-motion-from="go" data-motion-to="land"></div>'
    '<div class="ring" data-loop="spin" data-loop-seconds="2"></div></template></div>'
)
"""A slide whose packet travels half the canvas from `go` to `land` while a ring turns beside it."""


def moving_page(tmp_path: Path) -> Allowed:
    return deck_page(tmp_path, MOVING)


def test_every_declared_motion_reports_the_lane_it_swept_and_the_cues_it_lands_on(tmp_path):
    """Verify masks what the page said moved, so the page says where, when and on which cues, frame by frame."""
    url = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.2,1.1:land@0.6", Q.T0: T0_SIGNAL})
    recording = record(moving_page(tmp_path), tmp_path / "01.mp4", 1.0, url=url)
    motion, loop = recording.report.motions
    scale = WIDTH / 1920
    assert motion.kind == "motion" and loop.kind == "loop"
    assert (motion.departs, motion.arrives) == (0.2, 0.6)
    assert [(row.cue, row.kind, row.at) for row in motion.landings] == [
        ("1.1:go", "depart", 0.2),
        ("1.1:land", "arrive", 0.6),
    ]
    assert motion.box is not None
    assert (motion.box.x, motion.box.y) == (0, round(120 * scale))
    assert motion.box.w == round((960 + 192) * scale) and motion.box.h == round(192 * scale)
    assert (loop.departs, loop.arrives) == (0.0, round((FPS - 1) / FPS, 3))
    assert loop.landings == ()


WEBGL = (
    "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['go'],"
    " render: () => '<canvas id=gl width=320 height=180></canvas>' }] });"
    "let gl = null;"
    "DeckTalk.frame((t) => {"
    " gl = gl || document.getElementById('gl')?.getContext('webgl', { preserveDrawingBuffer: true });"
    " if (!gl) return;"
    " gl.clearColor(0.5 + 0.5 * Math.sin(t * 3), 0.2, 0.5 + 0.5 * Math.cos(t * 2), 1); gl.clear(gl.COLOR_BUFFER_BIT);"
    "}, { region: '#gl' });"
)
"""A composition drawn in WebGL from the section second, which is what a three.js scene is underneath."""


def test_a_webgl_composition_is_drawn_from_its_second_and_records_the_same_frames_twice(tmp_path):
    """A canvas drawn by a frame hook is as exact as the page around it, so a three.js composition is too."""
    deck = tmp_path / "deck"
    deck.mkdir()
    deck.joinpath("index.html").write_text(
        "<!doctype html><html lang=en><head><meta charset=utf-8><title>gl</title>"
        "<style>html,body{margin:0;background:#000}#gl{position:fixed;left:0;top:0;width:320px;height:180px}</style>"
        f'<script src="{ENGINE_PATH}{RUNTIME_FILE}"></script></head><body><script>{WEBGL}</script></body></html>',
        encoding="utf-8",
    )
    allowed = Allowed.of(tmp_path, ["deck"])
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    record(allowed, first, 0.8)
    record(allowed, second, 0.8)
    frames_a, frames_b = luma_frames(first), luma_frames(second)
    assert frames_a == frames_b
    assert len(set(frames_a)) > len(frames_a) // 2, "the canvas changes with its second"


# ---- a frame is captured only once the page has drawn it ----------------------------------------


@contextmanager
def held(allowed: Allowed, url: str = URL) -> Iterator[stepping.Stepper]:
    """A loaded fixture page held still on its clock, prepared the way `record_piece` prepares one."""
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        opened = stepping.stepping(
            real, allowed, width=WIDTH, height=HEIGHT, color_scheme="dark", motion=MotionConfig()
        )
        with opened as (page, _assets):
            page.goto(url, wait_until="load")
            await_painted(page)
            stepper = stepping.Stepper(page, FPS, WAIT)
            stepper.hold()
            yield stepper


LAID_OUT = (
    GROW
    + "window.__laidOut = null;"
    + "new ResizeObserver((rows) => { window.__laidOut = rows[0].contentRect.width; })"
    + ".observe(document.getElementById('bar'));"
)
"""A bar that grows every frame, and the width the page's last rendering update laid it out at.

A ResizeObserver is told of a size during the rendering update that lays the page out, after the
style and layout of that update and before its paint, and never from a script that only changed a style.
"""


def test_a_step_answers_only_once_the_page_has_laid_out_and_painted_what_it_drew(tmp_path):
    """A capture taken the moment a step returns would race the page's own rendering, so a step waits for it."""
    with held(fixture_page(tmp_path, LAID_OUT)) as stepper:
        laid_out = []
        for k in range(1, FPS // 2):
            stepper.step(k / FPS)
            laid_out.append(stepper.page.evaluate("() => window.__laidOut"))
    assert laid_out == [k * PIXELS_PER_SECOND / FPS for k in range(1, FPS // 2)]


FONT_FILE = "Inter-latin.woff2"
"""A web font the shipped lesson carries, which the fixture below names only once its word is shown."""

LATE_WORD = (
    f"<style>@font-face{{font-family:Late;src:url({FONT_FILE});font-display:block}}"
    "#word{display:none;position:fixed;left:0;top:100px;font:40px Late;color:#fff}</style>"
    "<p id=word>late</p>"
)
"""A word in a web font nothing on the page has used yet, which the browser fetches only when it is laid out."""

SHOW_WORD = "DeckTalk.frame((t) => { if (t >= 0.2) document.getElementById('word').style.display = 'block'; });"


def test_a_step_that_shows_text_in_a_font_not_yet_used_answers_once_the_font_has_loaded(tmp_path):
    """A frame captured while its font loads shows no text at all, so the step waits for every font it needs."""
    allowed = fixture_page(tmp_path, SHOW_WORD, LATE_WORD)
    font = resources.files("decktalk").joinpath("template", "examples", "lesson", "deck", "fonts", FONT_FILE)
    tmp_path.joinpath("deck", FONT_FILE).write_bytes(font.read_bytes())
    with held(allowed) as stepper:
        stepper.step(0.24)
        status = stepper.page.evaluate("() => [...document.fonts].find((face) => face.family === 'Late').status")
    assert status == "loaded"


FRAME_WAIT_FOR_A_HUNG_PAGE_SECONDS = 0.5
"""A wait short enough that a page which never finishes drawing fails this test quickly."""


def test_a_page_that_is_never_drawn_within_the_wait_fails_the_frame_rather_than_capturing_it(tmp_path):
    """A frame whose image never decodes is a ToolError naming the frame, never a frame captured half drawn."""
    script = "DeckTalk.frame((t) => { if (t >= 0.2 && !window.__hung) { window.__hung = true;"
    script += " const img = new Image(); img.src = 'never.png'; img.decode = () => new Promise(() => {});"
    script += " document.body.appendChild(img); } });"
    allowed = fixture_page(tmp_path, script)
    with held(allowed) as stepper:
        stepper.wait_seconds = FRAME_WAIT_FOR_A_HUNG_PAGE_SECONDS
        stepper.frame(4)
        with pytest.raises(ToolError) as raised:
            stepper.frame(5)
    assert "frame 5" in str(raised.value)


STEPPED_BACK = (
    "<style>.box{position:absolute;left:40px;top:20px;width:192px;height:120px;background:#fff}</style>"
    '<div data-scene="1" data-name="back"><template data-slide="1.1">'
    '<div class="box" data-in="go" data-in-style="fade" data-back="dim"></div></template></div>'
)
"""A box that fades in on one cue and steps back on a later one, long after its fade has ended."""

STEPPED_BACK_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.1,1.1:dim@0.8", Q.T0: T0_SIGNAL})


def test_an_element_that_arrived_steps_back_on_the_same_frames_however_the_frame_was_reached(tmp_path):
    """The box dims from the second `dim` is due on both paths, because its fade ended on the clock and not on an event.

    Reached by its index, frame 22 steps only the two cue seconds, and the step to `dim` carries the
    fade to its end and steps the box back in one task, before any frame is drawn. An entrance taken
    off by the browser's end event would still be on then and would hold the box bright.
    """
    allowed = deck_page(tmp_path, STEPPED_BACK)
    k = 22
    assert a_frame(allowed, k, walked=False, url=STEPPED_BACK_URL) == a_frame(
        allowed, k, walked=True, url=STEPPED_BACK_URL
    )


COVERED = (
    "<style>.held{position:absolute;inset:0}.ring{position:absolute;left:40px;top:20px;width:120px;height:120px;"
    "border-radius:999px;border:12px solid #fff}#over{position:absolute;left:20px;top:100px;width:200px;height:43px;"
    "background:#888;visibility:hidden}#over.on{visibility:visible}</style>"
    "<div class=held><div class=ring></div><div id=over></div></div><script>"
    "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['show', 'hide'], render: () => '' }] });"
    "DeckTalk.on('1.1:show', () => document.getElementById('over').classList.add('on'))"
    ".on('1.1:hide', () => document.getElementById('over').classList.remove('on'));</script>"
)
"""A ring a box covers part of for a while, which leaves the ring exactly as it was once the box has gone."""


def covered_url(show: float, hide: float) -> str:
    return page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: f"1.1:show@{show},1.1:hide@{hide}", Q.T0: T0_SIGNAL})


def test_a_part_of_the_page_drawn_again_is_drawn_as_a_page_that_never_changed_draws_it(tmp_path):
    """The ring under a box that came and went is the ring of a page whose box never showed, to the pixel.

    The compositor redraws only what changed, and a redraw of part of a tile must draw the ring's
    anti-aliased edge as a whole tile would, or a frame shows what the frames before it drew and not
    its own second.
    """
    allowed = deck_page(tmp_path, COVERED)
    k = 20
    came_and_went = a_frame(allowed, k, walked=True, url=covered_url(0.2, 0.6))
    never_shown = a_frame(allowed, k, walked=True, url=covered_url(2.0, 2.0))
    assert came_and_went == never_shown


# ---- a still frame is written again rather than captured ----------------------------------------


class Frames:
    """A frame sink that keeps every frame it is handed in order, and writes the last one again on a repeat."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.repeats = 0

    def write(self, frame: bytes) -> None:
        self.frames.append(frame)

    def repeat(self) -> None:
        self.repeats += 1
        self.frames.append(self.frames[-1])


def stepped_frames(
    allowed: Allowed, seconds: float, *, skip_still: bool, audit_every: int = 0, url: str = URL
) -> Frames:
    """Every frame of a fixture page recorded into memory, skipping still frames or capturing every one."""
    sink = Frames()
    with held(allowed, url) as stepper:
        stepper.record(range(stepping.frame_count(seconds, FPS)), sink, lambda: None, skip_still=skip_still,
                       audit_every=audit_every)  # fmt: skip
    return sink


RAW_TIMER = (
    "DeckTalk.on('1.1:go', () => setTimeout(() => {"
    " document.getElementById('late').style.visibility = 'visible'; }, 400));"
)
"""A cue that starts a plain timer, which shows an element from second 0.4 with nothing the runtime ran."""


def test_a_style_a_plain_timer_changes_is_captured_on_its_own_frame_and_every_still_frame_is_written_again(tmp_path):
    """The recorder skips the capture of a frame nothing changed on, yet a timer the runtime never ran is seen."""
    allowed = fixture_page(tmp_path, RAW_TIMER)
    skipped = stepped_frames(allowed, 1.0, skip_still=True, audit_every=AUDIT_EVERY)
    captured = stepped_frames(allowed, 1.0, skip_still=False)
    assert skipped.frames == captured.frames
    assert skipped.repeats >= FPS - 3, "a page that changes on two frames is captured on few more"
    out = tmp_path / "01.mp4"
    record(allowed, out, 1.0, skip_still=True)
    shown = [lit_width(frame, 100) > 0 for frame in luma_frames(out)]
    assert shown.index(True) == 10
    assert all(shown[10:])


STILL_CANVAS = (
    "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['go'],"
    " render: () => '<canvas id=still width=32 height=18>' }] });"
)
"""A slide holding a canvas that nothing draws, which the recorder cannot see into and so never trusts."""


def test_a_page_with_a_canvas_is_captured_on_every_frame(tmp_path):
    """A canvas draws without touching the DOM, so a page that holds one is never reported still."""
    sink = stepped_frames(deck_page(tmp_path, f"<script>{STILL_CANVAS}</script>"), 0.6, skip_still=True)
    assert sink.repeats == 0
    assert len(sink.frames) == stepping.frame_count(0.6, FPS)


HIDDEN = (
    "<style id=sheet>#hidden{position:fixed;left:0;top:0;width:100px;height:40px;background:#fff;visibility:hidden}"
    "</style>"
    "<div id=hidden></div><script>"
    "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['go'], render: () => '' }] });"
    "DeckTalk.on('1.1:go', () => setTimeout(() => {"
    " document.getElementById('sheet').sheet.insertRule('#hidden{visibility:visible}', 1); }, 400));</script>"
)
"""A page that shows a box from second 0.4 through its style sheet's object model, which no DOM mutation reports."""


def test_the_audit_stops_a_recording_whose_skipped_frame_differs_from_the_frame_before(tmp_path):
    """A change drawn outside what DeckTalk knows is skipped silently without the audit, and fails loudly with it."""
    allowed = deck_page(tmp_path, HIDDEN)
    assert stepped_frames(allowed, 0.8, skip_still=True).frames != stepped_frames(allowed, 0.8, skip_still=False).frames
    with pytest.raises(ToolError) as raised:
        stepped_frames(allowed, 0.8, skip_still=True, audit_every=AUDIT_EVERY)
    assert "frame 10" in str(raised.value)


# ---- the first frame of a page whose loop is already turning -----------------------------------


MOVING_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.2,1.1:land@0.6", Q.T0: T0_SIGNAL})

PENDING_JS = "() => document.getAnimations().filter((animation) => animation.pending).length"
"""How many of the page's animations are still waiting for the browser to start or pause them."""


@contextmanager
def held_fresh(allowed: Allowed, url: str) -> Iterator[stepping.Stepper]:
    """A fresh page held still straight after it loads, with nothing waited on between the load and the hold."""
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        opened = stepping.stepping(
            real, allowed, width=WIDTH, height=HEIGHT, color_scheme="dark", motion=MotionConfig()
        )
        with opened as (page, _assets):
            page.goto(url, wait_until="load")
            stepper = stepping.Stepper(page, FPS, WAIT)
            stepper.hold()
            yield stepper


def test_the_clock_is_held_only_once_every_animation_the_page_set_playing_has_started(tmp_path):
    """A loop the first slide starts is often still waiting to start at load, and a hold must not freeze it there."""
    with held_fresh(moving_page(tmp_path), MOVING_URL) as stepper:
        assert stepper.page.evaluate(PENDING_JS) == 0


FRESH_PAGES = 8
"""How many fresh pages draw frame 0, which disagreed in about one page in three before every loop had started."""


def test_frame_zero_of_a_slide_whose_loop_is_turning_is_the_same_from_every_fresh_page(tmp_path):
    """A loop paused before the browser started it showed the ring a frame ahead on some pages, never on others."""
    allowed = moving_page(tmp_path)
    first = []
    for _ in range(FRESH_PAGES):
        with held_fresh(allowed, MOVING_URL) as stepper:
            first.append(hashlib.sha256(stepper.frame(0)).hexdigest())
    assert len(set(first)) == 1, first


# ---- the lossless pipe never makes the stepper wait for a repeat --------------------------------


class Held:
    """A send into the encode that keeps every frame it is sent and holds the first until it is let go."""

    def __init__(self, fail_at: int | None = None) -> None:
        self.sent: list[bytes] = []
        self.go = threading.Event()
        self.fail_at = fail_at

    def __call__(self, frame: bytes) -> None:
        self.go.wait()
        if len(self.sent) == self.fail_at:
            raise ToolError("the encode of 01.mp4 failed: it stopped reading.")
        self.sent.append(frame)


def test_the_pipe_sends_every_frame_as_many_times_as_it_runs_and_a_repeat_never_waits_for_the_encode():
    """A thousand repeats while the encode is held return at once, and the encode then reads every frame in order."""
    send = Held()
    with stepping.LosslessPipe.feeding(send) as sink:
        sink.write(b"a")
        for _ in range(1000):
            sink.repeat()
        sink.write(b"b")
        sink.repeat()
        send.go.set()
    assert send.sent == [b"a"] * 1001 + [b"b"] * 2


def test_an_encode_that_stops_reading_stops_the_recording_at_its_next_frame():
    """A failed encode is the stepper's error, raised at the next frame it hands over and never swallowed."""
    send = Held(fail_at=2)
    send.go.set()
    with pytest.raises(ToolError) as raised, stepping.LosslessPipe.feeding(send) as sink:
        sink.write(b"a")
        for _ in range(stepping.ENCODE_QUEUE_FRAMES * 100):
            sink.write(b"b")
    assert "01.mp4" in str(raised.value)


def test_a_recording_that_stops_drops_the_frames_not_yet_sent_and_does_not_wait_for_the_encode():
    """A cancelled recording leaves the block at once, with the encode still held on a frame it will never finish."""
    send = Held()
    with pytest.raises(KeyboardInterrupt), stepping.LosslessPipe.feeding(send) as sink:
        sink.write(b"a")
        sink.write(b"b")
        raise KeyboardInterrupt
    send.go.set()
    assert b"b" not in send.sent


# ---- a still stretch is crossed in one step -----------------------------------------------------


def stepped_seconds(allowed: Allowed, url: str, seconds: float) -> tuple[list[float], Frames, Frames]:
    """The seconds the recorder stepped the page to while it recorded `url` skipping still frames, and both recordings.

    The second recording captures every frame, which is what the first has to equal.
    """
    seen: list[float] = []
    skipping = Frames()
    with held(allowed, url) as stepper, pytest.MonkeyPatch.context() as patch:
        step = stepper.step

        def counted(at: float) -> None:
            seen.append(round(at, 3))
            step(at)

        patch.setattr(stepper, "step", counted)
        stepper.record(
            range(stepping.frame_count(seconds, FPS)), skipping, lambda: None, skip_still=True, audit_every=0
        )
    capturing = Frames()
    with held(allowed, url) as stepper:
        stepper.record(
            range(stepping.frame_count(seconds, FPS)), capturing, lambda: None, skip_still=False, audit_every=0
        )
    return seen, skipping, capturing


LATE_CUE = (
    "<style>#late{position:fixed;left:0;top:80px;width:100px;height:40px;background:#fff;visibility:hidden}"
    "#late.on{visibility:visible}</style><div id=late></div><script>"
    "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['go', 'show'], render: () => '' }] });"
    "DeckTalk.on('1.1:show', () => document.getElementById('late').classList.add('on'));</script>"
)
"""A slide that shows a box on its second cue and draws nothing else, so the page is still on either side of it."""

LATE_CUE_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0,1.1:show@2", Q.T0: T0_SIGNAL})


def test_a_still_stretch_is_crossed_in_one_step_and_its_frames_are_written_again(tmp_path):
    """Nothing can change between the cue at zero and the cue at two seconds, so the page is not stepped between them.

    The recorder steps the first frame, then the last frame of the still stretch, which keeps the
    page's own seconds in step with the frames, then the cue's frame, and once nothing is owed it
    steps only the last frame of the section. Every frame between is the frame before, written again.
    """
    seen, skipping, capturing = stepped_seconds(deck_page(tmp_path, LATE_CUE), LATE_CUE_URL, 3.0)
    assert seen == [0.0, 1.96, 2.0, 2.96]
    assert skipping.frames == capturing.frames
    shown = [lit_width(frame, 100) > 0 for frame in decoded(skipping.frames, tmp_path)]
    assert shown.index(True) == 2 * FPS


TIMER_IN_A_STILL_STRETCH = (
    "<style>#late{position:fixed;left:0;top:80px;width:100px;height:40px;background:#fff;visibility:hidden}"
    "</style><div id=late></div><script>"
    "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['go'], render: () => '' }] });"
    "DeckTalk.on('1.1:go', () => setTimeout(() => {"
    " document.getElementById('late').style.visibility = 'visible'; }, 1500));</script>"
)
"""A cue at zero that starts a plain timer, which shows a box at second 1.5 with nothing the runtime knows of."""

TIMER_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0", Q.T0: T0_SIGNAL})


def test_a_plain_timer_inside_a_long_still_stretch_lands_on_its_own_frame(tmp_path):
    """A timer the runtime never ran ends the still stretch it sits in, so the frame it changes is stepped and captured.

    The page's own timers say when the next of them is due, so the recorder stops the stretch on the
    frame before it and steps the timer's own frame, the first at or past second 1.5.
    """
    seen, skipping, capturing = stepped_seconds(deck_page(tmp_path, TIMER_IN_A_STILL_STRETCH), TIMER_URL, 3.0)
    assert seen == [0.0, 1.48, 1.52, 2.96]
    assert skipping.frames == capturing.frames
    shown = [lit_width(frame, 100) > 0 for frame in decoded(skipping.frames, tmp_path)]
    assert shown.index(True) == 38
    assert all(shown[38:])


def test_a_page_with_nothing_owed_is_still_for_good_once_the_recorder_has_waited_on_it(tmp_path):
    """The recorder's own waits leave no timer and the runtime's loop no frame on the page's clock to end a stretch.

    Every wait the recorder races inside the page clears its timer once it is answered, and the
    first step cancels the frame the runtime's own loop was waiting on, so a page with no task owed
    and nothing moving answers that nothing will ever change.
    """
    with held(fixture_page(tmp_path, "")) as stepper:
        stepper.step(0.0)
        assert stepper.still_until is None


def decoded(frames: list[bytes], tmp_path: Path) -> list[bytes]:
    """Each captured PNG as its luma plane, which is what `lit_width` reads."""
    planes = []
    for k, png in enumerate(frames):
        still = tmp_path / f"frame-{k}.png"
        still.write_bytes(png)
        planes.append(ffmpeg.raw("-i", str(still), "-f", "rawvideo", "-pix_fmt", "gray", "-"))
    return planes


# ---- a pane clip ------------------------------------------------------------------------------------

CLIP_FRAMES_SHOWN = 8
"""How many frames the pane's clip holds, each brighter than the one before."""

CLIP_STEP = 25
"""How much brighter each frame of the pane's clip is drawn than the one before."""

PANE = (
    "<style>.pane{position:absolute;left:0;top:0;width:1920px;height:1080px}</style>"
    '<div data-scene="1" data-name="pane"><template data-slide="1.1">'
    '<div class="pane" data-clip="deck/steps.mkv" data-clip-from="go"></div></template></div>'
)
"""A slide that is one pane over the whole frame, playing its clip from the cue `go`."""

PANE_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.2", Q.T0: T0_SIGNAL})


def pane_page(tmp_path: Path) -> Allowed:
    allowed = deck_page(tmp_path, PANE)
    ffmpeg.run(
        "-f", "lavfi", "-i",
        f"color=c=black:s=64x36:r={FPS}:d={CLIP_FRAMES_SHOWN / FPS},geq=lum='N*{CLIP_STEP}':cb=128:cr=128",
        "-c:v", "ffv1", "-pix_fmt", "yuv444p", str(tmp_path / "deck" / "steps.mkv"),
    )  # fmt: skip
    return allowed


def record_pane(allowed: Allowed, out: Path, starts: tuple[int, ...] = (0,)) -> Recording:
    return record(allowed, out, 0.8, url=PANE_URL, starts=starts, skip_still=True, audit_every=1)


def test_a_pane_clip_shows_its_first_frame_until_its_cue_and_then_one_frame_of_its_own_a_frame(tmp_path):
    """The cue is at frame 5, so frame `k` shows the clip's frame `k - 5` from there, and its first frame before."""
    out = tmp_path / "01.mp4"
    recording = record_pane(pane_page(tmp_path), out)
    middle = (HEIGHT // 2) * WIDTH + WIDTH // 2
    levels = [frame[middle] for frame in luma_frames(out)]
    cue = round(0.2 * FPS)
    assert len(set(levels[: cue + 1])) == 1, f"the pane holds the clip's first frame up to its cue: {levels}"
    played = levels[cue : cue + CLIP_FRAMES_SHOWN]
    assert played == sorted(set(played)), f"each frame from the cue shows the clip's next frame: {levels}"
    assert levels[cue + CLIP_FRAMES_SHOWN :] == [levels[cue + CLIP_FRAMES_SHOWN - 1]] * (
        len(levels) - cue - CLIP_FRAMES_SHOWN
    ), "the pane holds the clip's last frame once it has played"
    (lane,) = recording.report.motions
    assert (lane.kind, lane.clip, lane.departs) == ("clip", "deck/steps.mkv", 0.2)
    assert [(one.cue, one.kind, one.at) for one in lane.landings] == [("1.1:go", "depart", 0.2)]
    assert "deck/steps.mkv" in recording.assets
    assert recording.pane_clips() == (lane,)


def test_a_pane_clip_records_the_same_frames_twice(tmp_path):
    allowed = pane_page(tmp_path)
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    record_pane(allowed, first)
    record_pane(allowed, second)
    assert luma_frames(first) == luma_frames(second)


def test_a_pane_clip_recorded_in_pieces_is_the_frames_one_page_records(tmp_path):
    """A piece that starts after the clip's cue shows the clip's own frame for its second, not the clip's first."""
    allowed = pane_page(tmp_path)
    whole, split = tmp_path / "a.mp4", tmp_path / "b.mp4"
    once = record_pane(allowed, whole)
    joined = record_pane(allowed, split, starts=(0, 3, 8, 11))
    assert luma_frames(split) == luma_frames(whole)
    assert joined.report == once.report


def test_a_pane_clip_records_the_same_frames_when_still_frames_are_skipped_with_the_audit_on(tmp_path):
    """Each new frame of a playing clip is a change, so skipping still frames never writes an old clip frame again."""
    allowed = pane_page(tmp_path)
    skipped = stepped_frames(allowed, 0.8, skip_still=True, audit_every=1, url=PANE_URL)
    captured = stepped_frames(allowed, 0.8, skip_still=False, url=PANE_URL)
    assert skipped.frames == captured.frames


def test_a_pane_clip_is_still_before_its_cue(tmp_path):
    """The pane shows the clip's first frame until its cue on frame 5, so frames 1 to 4 are written again."""
    sink = stepped_frames(pane_page(tmp_path), 0.8, skip_still=True, url=PANE_URL)
    assert sink.repeats >= 4, f"only {sink.repeats} of the frames before the cue were written again"


# ---- what the page draws that nothing declared, and what a motion leaves undrawn -----------------


SCRIPTED_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.1,1.1:land@0.4", Q.T0: T0_SIGNAL})


def scripted_page(tmp_path: Path, opacity: str) -> Allowed:
    """A box a script motion widens from `go` to `land`, whose opacity `draw` sets from `opacity`, a JS expression."""
    return deck_page(
        tmp_path,
        "<style>#box{position:fixed;left:0;top:0;height:60px;width:0;background:#fff}</style><script>"
        "DeckTalk.scene(1, { slides: [{ id: '1.1', owns: ['go', 'land'], render: () => '<div id=box></div>' }] });"
        "DeckTalk.on('1.1:go', (slide) => DeckTalk.motion(slide.querySelector('#box'), { from: '1.1:go',"
        " to: '1.1:land', draw: (p, t) => { const box = document.getElementById('box');"
        f" box.style.width = (p * 200) + 'px'; box.style.opacity = String({opacity}); }} }}));</script>",
    )


def recorded(allowed: Allowed, out: Path, url: str, seconds: float, *, audit_every: int = AUDIT_EVERY) -> Recording:
    return record(allowed, out, seconds, url=url, audit_every=audit_every)


def test_a_script_motion_whose_draw_still_changes_after_it_arrives_is_reported_on_its_arrival(tmp_path):
    """A draw that fades in over 0.2 s once its progress is 1 is never drawn past the arrival, so the page says so."""
    unsettled = recorded(scripted_page(tmp_path, "p < 1 ? 0 : Math.min(1, (t - 0.4) / 0.2)"), tmp_path / "a.mp4",
                         SCRIPTED_URL, 1.0)  # fmt: skip
    rows = [(row.code, row.cue) for row in unsettled.report.warnings]
    assert (Code.PAGE_MOTION_UNFINISHED, "1.1:land") in rows
    settled = recorded(scripted_page(tmp_path, "p"), tmp_path / "b.mp4", SCRIPTED_URL, 1.0)
    assert Code.PAGE_MOTION_UNFINISHED not in [row.code for row in settled.report.warnings]


UNDECLARED_TIMER = (
    "DeckTalk.on('1.1:go', () => setTimeout(() => {"
    " document.getElementById('late').style.visibility = 'visible'; }, 400));"
)
"""A cue that starts a plain timer which shows `#late` from second 0.4, inside nothing the page declared as moving."""


def test_a_change_a_plain_timer_draws_outside_every_declared_lane_is_named_with_its_second(tmp_path):
    """The recorder learns a script changed the picture with nothing declared to explain it, and where and when."""
    recording = recorded(fixture_page(tmp_path, UNDECLARED_TIMER), tmp_path / "a.mp4", URL, 1.0)
    assert [(row.what, row.at, row.frames) for row in recording.undeclared] == [("<div#late>", 0.4, 1)]


def test_a_timer_that_changes_a_declared_region_or_nothing_visible_is_not_undeclared_change(tmp_path):
    """A change inside a region a frame hook declares is masked, and a change no pixel shows cannot mislead verify."""
    region = UNDECLARED_TIMER + "DeckTalk.frame(() => {}, { region: '#late' });"
    declared = recorded(fixture_page(tmp_path, region), tmp_path / "a.mp4", URL, 1.0)
    assert declared.undeclared == ()
    unseen = UNDECLARED_TIMER.replace(".style.visibility = 'visible'", ".dataset.seen = 'yes'")
    invisible = recorded(fixture_page(tmp_path, unseen), tmp_path / "b.mp4", URL, 1.0)
    assert invisible.undeclared == ()


def test_a_timer_inside_a_still_stretch_ends_the_stretch_and_is_reported_as_undeclared_change(tmp_path):
    """Crossing a still stretch in one step must still stop where the page's own timer lands, and name what it drew."""
    out = tmp_path / "a.mp4"
    recording = recorded(fixture_page(tmp_path, UNDECLARED_TIMER), out, URL, 1.0, audit_every=0)
    shown = [lit_width(frame, 100) > 0 for frame in luma_frames(out)]
    assert shown.index(True) == 10 and all(shown[10:]), "the stretch ends on the frame the timer lands on"
    assert [(row.what, row.at, row.frames) for row in recording.undeclared] == [("<div#late>", 0.4, 1)]


def test_a_playing_pane_clip_and_a_timer_drawing_in_its_pane_are_declared_change(tmp_path):
    """A pane clip is declared motion, so neither its frames nor a page timer drawing inside it are undeclared."""
    plain = tmp_path / "a.mp4"
    assert record_pane(pane_page(tmp_path), plain).undeclared == ()
    timed = PANE.replace(
        "</template></div>",
        "</template></div><script>DeckTalk.on('1.1:go', () => setTimeout(() => {"
        " document.querySelector('.pane').style.filter = 'invert(1)'; }, 200));</script>",
    )
    assert timed != PANE
    deck = tmp_path / "deck" / "index.html"
    deck.write_text(deck.read_text(encoding="utf-8").replace(PANE, timed), encoding="utf-8")
    out = tmp_path / "b.mp4"
    assert record_pane(Allowed.of(tmp_path, ["deck"]), out).undeclared == ()
    assert luma_frames(out) != luma_frames(plain), "the timer's change shows in the pane"


SPOKEN_LINE = (
    "<style>.said{position:absolute;left:20px;top:60px;font:40px sans-serif;color:#fff}</style>"
    '<div data-scene="1" data-name="said"><template data-slide="1.1">'
    '<p class="said" data-in="go" data-words>one two three four</p></template></div>'
)
"""A line shown word by word on the voice, whose words are said between the frames, not on them."""

SPOKEN_LINE_URL = page_url(
    "deck/index.html",
    {Q.SCENE: "1", Q.CUES: "1.1:go@0.1", Q.WORDS: "one@0.3,two@0.43,three@0.57,four@0.7", Q.T0: T0_SIGNAL},
)


@pytest.mark.parametrize("k", [9, 11, 13, 15, 17, 20])
def test_a_word_shown_on_the_voice_arrives_on_the_same_frames_however_the_frame_was_reached(tmp_path, k):
    """A word is revealed at its own second, so its fade is as far along on a frame reached by its index.

    A piece of the starter that began on frame 439 showed a word starting its fade on the piece's
    first frame, which the one pass had finished drawing, because a word was revealed only on the
    seconds a step happened to draw.
    """
    allowed = deck_page(tmp_path, SPOKEN_LINE)
    assert a_frame(allowed, k, walked=False, url=SPOKEN_LINE_URL) == a_frame(
        allowed, k, walked=True, url=SPOKEN_LINE_URL
    )


# ---- a section recorded in pieces and joined ----------------------------------------------------


def test_a_section_recorded_in_pieces_and_joined_is_the_frames_and_the_report_of_one_pass(tmp_path):
    """Pieces that each start on a keyframe join without an encode into the frames one page records whole.

    The packet's lane is swept across every piece and the ring turns through all of them, so the
    joined report has to take the union of what each page saw, and the lanes and cues of one pass are
    what it reads.
    """
    allowed = moving_page(tmp_path)
    whole, split = tmp_path / "whole.mp4", tmp_path / "split.mp4"
    once = record(allowed, whole, 1.0, url=MOVING_URL)
    joined = record(allowed, split, 1.0, url=MOVING_URL, starts=(0, 7, 13, 19))
    assert joined.pieces == (0, 7, 13, 19)
    pairs = list(zip(luma_frames(whole), luma_frames(split), strict=True))
    assert len(pairs) == FPS
    assert [k for k, (one, other) in enumerate(pairs) if one != other] == []
    assert joined.report == once.report
    assert list(tmp_path.glob(".*")) == []


TRAVELLING = MOVING.replace('<div class="ring" data-loop="spin" data-loop-seconds="2"></div>', "")
"""The moving slide with its packet alone, which stands still before it departs and after it lands."""


def test_a_section_recorded_in_pieces_with_still_frames_skipped_is_the_one_pass_that_captured_every_frame(tmp_path):
    """Each piece skips the frames nothing changed on and still captures its own first frame.

    The packet stands still before it departs and after it lands, so most frames of every piece are
    written again rather than captured, and the join is still the one pass that captured them all.
    """
    allowed = deck_page(tmp_path, TRAVELLING)
    whole, split = tmp_path / "whole.mp4", tmp_path / "split.mp4"
    once = record(allowed, whole, 1.0, url=MOVING_URL, skip_still=False)
    joined = record(allowed, split, 1.0, url=MOVING_URL, starts=(0, 3, 17, 21), skip_still=True)
    pairs = list(zip(luma_frames(whole), luma_frames(split), strict=True))
    assert len(pairs) == FPS
    assert [k for k, (one, other) in enumerate(pairs) if one != other] == []
    assert joined.report == once.report


# ---- whether a pane held still while its clip played -----------------------------------------------

CLIP_SECONDS = CLIP_FRAMES_SHOWN / FPS
"""How long the pane's clip plays from its cue, which is the span its own landings fall in."""

OWN_LANDING = (
    '[{"id": "step", "kind": "motion", "box": {"x": 0, "y": 0, "w": 64, "h": 36}, "size": {"w": 64, "h": 36},'
    ' "departs": 0.12, "arrives": 0.12, "landings": [{"cue": "step", "kind": "depart", "at": 0.12}]}]'
)
"""One landing of the clip's own, a little after it starts, as a build that made the clip would write it."""


def pane_at(tmp_path: Path, style: str, wrap: str = "") -> Allowed:
    """The pane fixture with the pane laid out by `style`, inside a `wrap` element when one is given."""
    pane = '<div class="pane" data-clip="deck/steps.mkv" data-clip-from="go"></div>'
    held = pane if not wrap else f"{wrap}{pane}</div>"
    allowed = pane_page(tmp_path)
    deck = tmp_path / "deck" / "index.html"
    markup = PANE.replace("width:1920px;height:1080px", style).replace(pane, held)
    deck.write_text(deck.read_text(encoding="utf-8").replace(PANE, markup), encoding="utf-8")
    landings_of(tmp_path / "deck" / "steps.mkv").write_text(OWN_LANDING, encoding="utf-8")
    return allowed


def recorded_pane(allowed: Allowed, out: Path, url: str = PANE_URL, starts: tuple[int, ...] = (0,)) -> Recording:
    return record(allowed, out, 1.0, url=url, starts=starts, audit_every=0)


def test_a_still_pane_at_a_fraction_of_a_pixel_places_its_clip_s_landings(tmp_path):
    """The canvas is drawn at a sixth, so the pane stands at 1.4 px, 167.2 px wide: a box no rounding keeps whole."""
    allowed = pane_at(tmp_path, "left:8.4px;width:1003.2px;height:540px")
    recording = recorded_pane(allowed, tmp_path / "01.mp4")
    placed = pane_landings(recording, tmp_path)
    assert placed.unplaced == ()
    assert [(lane.id.split("/")[-1], lane.landings[0].at) for lane in placed.lanes] == [("step", 0.32)]


AWAY = (
    "<style>@keyframes away{to{transform:translateX(600px)}}</style>"
    '<div data-motion="away" data-motion-from="leave" data-motion-to="gone">'
)
"""A camera that carries the pane away from the cue `leave` to the cue `gone`."""


def test_a_pane_carried_away_after_its_clip_has_played_places_its_clip_s_landings(tmp_path):
    """The clip plays from 0.2 s to 0.52 s, and the camera leaves from 0.6 s, so the section need not be cut there."""
    allowed = pane_at(tmp_path, "width:960px;height:540px", AWAY)
    url = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.2,1.1:leave@0.6,1.1:gone@0.8", Q.T0: T0_SIGNAL})
    recording = recorded_pane(allowed, tmp_path / "01.mp4", url)
    placed = pane_landings(recording, tmp_path)
    assert placed.unplaced == ()
    ((lane,),) = [placed.lanes]
    assert lane.box == Box(x=0, y=0, w=160, h=90), "the clip's lanes are drawn into the pane where it stood"


def test_a_pane_carried_away_while_its_clip_plays_places_none_of_its_landings_and_says_when(tmp_path):
    allowed = pane_at(tmp_path, "width:960px;height:540px", AWAY)
    url = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.2,1.1:leave@0.3,1.1:gone@0.8", Q.T0: T0_SIGNAL})
    recording = recorded_pane(allowed, tmp_path / "01.mp4", url)
    placed = pane_landings(recording, tmp_path)
    assert placed.lanes == ()
    assert placed.unplaced == (
        f"deck/steps.mkv: its pane moved at 0.32 s, before its clip ended at {0.2 + CLIP_SECONDS:g} s",
    )


def test_a_pane_recorded_in_pieces_is_judged_still_or_moved_as_one_pass_judges_it(tmp_path):
    """A piece that starts after the camera left sees the pane only where it went, so pieces are joined by `rest`."""
    allowed = pane_at(tmp_path, "width:960px;height:540px", AWAY)
    url = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.2,1.1:leave@0.3,1.1:gone@0.5", Q.T0: T0_SIGNAL})
    once = recorded_pane(allowed, tmp_path / "whole.mp4", url)
    split = recorded_pane(allowed, tmp_path / "split.mp4", url, starts=(0, 7, 15))
    lane = next(row for row in once.report.motions if row.kind == "clip")
    joined = next(row for row in split.report.motions if row.kind == "clip")
    assert (joined.rest, joined.moved) == (lane.rest, lane.moved) and lane.moved is not None
    assert pane_landings(split, tmp_path) == pane_landings(once, tmp_path)


# ---- what a film carries of its reveals --------------------------------------------------------------

REVEALING = (
    "<style>.square{position:absolute;left:600px;top:300px;width:600px;height:600px;background:#fff}</style>"
    '<div data-scene="1" data-name="reveals"><template data-slide="1.1">'
    '<div class="square" data-in="go" data-in-style="cut"></div></template></div>'
)
"""A slide that reveals a square at the cue `go`, at 0.2 s, with no motion at all."""


def test_a_reveal_is_reported_on_the_box_it_was_drawn_in_departing_on_its_cue(tmp_path):
    """The canvas is drawn at a sixth, so the square stands at 100, 50 and is 100 wide in the recording."""
    url = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.2", Q.T0: T0_SIGNAL})
    recording = recorded(deck_page(tmp_path, REVEALING), tmp_path / "01.mp4", url, 0.8)
    assert recording.report.motions == ()
    (reveal,) = recording.report.reveals
    assert (reveal.kind, reveal.box, reveal.departs) == ("reveal", Box(x=100, y=50, w=100, h=100), 0.2)
    assert [(one.cue, one.kind, one.at) for one in reveal.landings] == [("1.1:go", "depart", 0.2)]
    split = record(deck_page(tmp_path, REVEALING), tmp_path / "02.mp4", 0.8, url=url, starts=(0, 3, 9))
    assert split.report.reveals == recording.report.reveals, "pieces join their reveals as one pass sees them"


# ---- a picture beside a pane clip ------------------------------------------------------------------

BESIDE = (
    "<style>.pane{position:absolute;left:300px;top:200px;width:780px;height:439px;border-radius:12px;"
    "overflow:hidden}.clip{position:absolute;inset:0}"
    "#slide,#button{position:absolute;inset:0;width:100%;height:100%}"
    "#button{clip-path:inset(300.37px 20.5px 40.2px 500.1px);opacity:0}</style>"
    '<div data-scene="1" data-name="pane"><template data-slide="1.1" data-owns="show"><div class="pane">'
    '<div class="clip" data-clip="deck/steps.mkv" data-clip-from="go"></div>'
    '<img id="slide" src="slide.png" alt=""><img id="button" src="button.png" alt=""></div></template></div>'
    "<script>DeckTalk.on('1.1:show', () => { document.getElementById('button').style.opacity = '0.6'; });</script>"
)
"""A pane playing a clip under a slide's picture, whose button picture a cue shows in its lower right corner."""

BESIDE_URL = page_url("deck/index.html", {Q.SCENE: "1", Q.CUES: "1.1:go@0.1,1.1:show@0.4", Q.T0: T0_SIGNAL})

LARGE = (960, 540)
"""A frame at half the canvas, where the slide's picture is drawn at a scale of its own, as a camera draws it."""


def test_a_picture_shown_beside_a_pane_clip_never_draws_the_pictures_around_it_again(tmp_path):
    """Chromium lays out a picture overlapping the clip's layer with it, so one that first shows re-rasters the rest.

    The slide's picture fills the pane and nothing changes it, so its top left, far from the button, is
    the same pixels on every frame before and after the cue that shows the button.
    """
    allowed = deck_page(tmp_path, BESIDE)
    deck = tmp_path / "deck"
    ffmpeg.run("-f", "lavfi", "-i", "testsrc2=s=1280x720:d=1", "-frames:v", "1", str(deck / "slide.png"))
    ffmpeg.run("-f", "lavfi", "-i", "testsrc=s=1280x720:d=1", "-frames:v", "1", str(deck / "button.png"))
    ffmpeg.run("-f", "lavfi", "-i", f"color=c=gray:s=64x36:r={FPS}:d=2", "-c:v", "ffv1", str(deck / "steps.mkv"))
    width, height = LARGE
    sink = Frames()
    with browser.chromium(policy=browser.TRUSTED, spend=False) as real:
        opened = stepping.stepping(
            real, allowed, width=width, height=height, color_scheme="dark", motion=MotionConfig()
        )
        with opened as (page, _assets):
            page.goto(BESIDE_URL, wait_until="load")
            stepper = stepping.Stepper(page, FPS, WAIT)
            stepper.hold()
            stepper.record(range(round(0.8 * FPS)), sink, lambda: None, skip_still=False, audit_every=0)
    planes = decoded(sink.frames, tmp_path)

    def corner(plane: bytes) -> bytes:
        return b"".join(plane[y * width + 155 : y * width + 390] for y in range(105, 200))

    before = corner(planes[round(0.3 * FPS)])
    moved = [k for k, plane in enumerate(planes) if corner(plane) != before]
    assert moved == [], f"the slide's picture was drawn again on frames {moved}"
    shown = planes[round(0.6 * FPS)][270 * width + 450]
    assert shown != planes[round(0.3 * FPS)][270 * width + 450], "the cue shows the button"
