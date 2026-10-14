"""Frame comparison against real ffmpeg: what a static picture reports, and what a reveal reports."""

from __future__ import annotations

import math
import random
import tempfile
from pathlib import Path

import pytest

from decktalk.media import ffmpeg, frames
from support.media_cards import FPS, PANEL_PERCENT, H, W, write_card
from support.tools import machine_tools

pytestmark = pytest.mark.media


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> Path:
    """The card every test here reads, written once under the machine's own tools.

    A module fixture is set up before the per-test binding in `tests/conftest.py`, so it binds for itself.
    """
    with machine_tools():
        return write_card(tmp_path_factory.mktemp("media") / "card.mp4")


def _grid(t: float) -> float:
    """The time of the first frame at or after t."""
    return round(math.ceil(t * FPS - 1e-6) / FPS, 3)


SIZE = frames.Size(W, H)
"""The card's own size, which every comparison below is made at."""


def oracle_changed(path: Path, t1: float, t2: float, *, level: int) -> float:
    """The per-frame comparison the decode replaced, kept as the answer the decode must give.

    Each frame is extracted once as a grayscale image through the exact seek, and ffmpeg compares the
    two images, which is what verify did for every probe before it decoded the film once.
    """
    with tempfile.TemporaryDirectory() as tmp:
        images = []
        for t, name in ((t1, "a.png"), (t2, "b.png")):
            seek = frames.frame_seek(t)
            target = Path(tmp) / name
            vf = f"{seek.trim()},scale={W}:{H},format=gray"
            ffmpeg.run(*seek.before, *ffmpeg.source(path), "-vf", vf, "-frames:v", "1", str(target))
            images.append(target)
        return frames.changed_images_percent(images[0], images[1], level=level, width=W, height=H)


def decoded(card: Path, *moments: float, span: tuple[float, float] | None = None) -> frames.Decoded:
    wanted = frames.Wanted()
    wanted.point(SIZE, *moments)
    if span is not None:
        wanted.span(SIZE, *span)
    return frames.decode(card, wanted)


@pytest.mark.parametrize(("t1", "t2"), [(1.0, 4.0), (4.43, 4.44), (4.9, 5.5), (4.96, 5.0), (4.93, 5.04), (0.0, 5.9)])
def test_one_decode_answers_every_comparison_as_the_per_frame_extraction_did(card, t1, t2):
    film = decoded(card, t1, t2)
    assert film.changed(t1, t2, level=12, size=SIZE) == pytest.approx(oracle_changed(card, t1, t2, level=12), abs=1e-3)


@pytest.mark.parametrize("ref_t", [4.40, 4.41, 4.43, 4.439, 4.44])
def test_the_series_reads_zero_on_a_static_colored_card_at_every_grid_phase(card, ref_t):
    film = decoded(card, ref_t, span=(ref_t, ref_t + 0.4))
    series = film.series(ref_t, ref_t, ref_t + 0.4, level=12, size=SIZE)
    assert series, "the decode kept no frames"
    # The first pair is the reference compared with itself, on the frame at or after ref_t.
    assert series[0][0] == _grid(ref_t)
    # Nothing on the card changes before 5.00 s, so no frame may report a changed pixel.
    assert [p for _, p in series] == [0.0] * len(series)


def test_the_series_times_are_the_frames_own_positions(card):
    film = decoded(card, 4.93, span=(4.93, 5.2))
    shares = dict(film.series(4.93, 4.93, 5.2, level=12, size=SIZE))
    assert shares[4.96] == 0.0
    assert 0.2 < shares[5.0] < 0.45  # the black square alone, 400 px
    assert PANEL_PERCENT * 0.9 < shares[5.04] < PANEL_PERCENT * 1.1
    assert max(shares) < 5.2, "a span stops before its end"


def test_a_moment_past_the_end_of_the_film_reads_its_last_frame(card):
    film = decoded(card, 5.9, 60.0)
    assert film.at(60.0, SIZE) == film.at(5.99, SIZE)


def test_a_long_decode_keeps_the_planned_frames_and_nothing_else(card):
    """The prototype held every frame of the film and reached 1.55 GB on a 147 s film."""
    film = decoded(card, 1.0, 3.0, 5.5)
    assert sorted(film.frames[SIZE]) == [25, 75, 138]
    # The decode stopped at the last planned frame, so it never learned where the film ends.
    assert film.ends[SIZE] == math.inf


@pytest.mark.parametrize("level", [0, 12, 40, 254])
def test_the_integer_comparison_counts_exactly_what_a_pixel_by_pixel_one_does(level):
    rng = random.Random(level)
    a = bytes(rng.randrange(256) for _ in range(4096))
    b = bytes(min(255, max(0, x + rng.randrange(-80, 81))) for x in a)
    assert frames.changed_count(a, b, level) == sum(abs(x - y) > level for x, y in zip(a, b, strict=True))


def graph(argv: list[str]) -> str:
    """The filter graph of one ffmpeg command, whichever flag carried it."""
    flag = "-vf" if "-vf" in argv else "-filter_complex"
    return argv[argv.index(flag) + 1]


@pytest.mark.parametrize("read", [lambda: frames.luma_at(Path("a.mp4"), 7.5)])
def test_every_reader_of_a_frame_jumps_coarsely_and_drops_the_remainder_in_the_graph(monkeypatch, read):
    """One exact path, because a reader that took the coarse jump alone would measure a neighbouring frame."""
    argv: list[list[str]] = []
    monkeypatch.setattr(frames.ffmpeg, "stderr", lambda *a: argv.append(list(a)) or "")
    monkeypatch.setattr(frames.ffmpeg, "run", lambda *a: argv.append(list(a)))
    read()
    for call in argv:
        assert call[call.index("-ss") + 1] == "4.500", call
        assert "trim=start=3.000" in graph(call), call
        # The remainder is never also given to the output, where it would drop reported frames.
        assert call.count("-ss") == 1, call


@pytest.mark.media
@pytest.mark.parametrize(("t", "revealed"), [(4.80, False), (4.96, False), (5.04, True), (5.20, True)])
def test_luma_at_reads_the_frame_it_was_asked_for_and_not_the_one_the_jump_landed_on(card, t, revealed):
    """The card holds one keyframe and reveals at 5.00 s, so a frame either side of it reads differently."""
    yavg, _ymax = frames.luma_at(card, t)
    assert (yavg < 225.0) is revealed, (t, yavg)


# ---- masking what the page said moves -------------------------------------------------------------


def a_film(*drawn: dict[int, list[tuple[int, int, int, int]]], size: frames.Size) -> frames.Decoded:
    """A decoded film of one frame per mapping, each a black frame with white rectangles (x, y, w, h)."""
    kept: dict[int, bytes] = {}
    for index, rects in enumerate(drawn):
        pixels = bytearray(size.width * size.height)
        for x, y, w, h in rects.get(0, []):
            for row in range(y, y + h):
                pixels[row * size.width + x : row * size.width + x + w] = b"\xff" * w
        kept[index] = bytes(pixels)
    return frames.Decoded(rate=frames.Fraction(25), frames={size: kept}, ends={size: len(kept)})


def test_a_mask_leaves_the_pixels_it_covers_out_of_a_comparison_and_out_of_its_share() -> None:
    size = frames.Size(40, 20)
    film = a_film({0: [(0, 0, 4, 4)]}, {0: [(10, 0, 4, 4), (30, 10, 10, 10)]}, size=size)
    mask = frames.Mask((frames.Rect(0, 0, 20, 20),), size)
    assert film.changed(0, 0.04, level=10, size=size) == pytest.approx((16 + 16 + 100) / 800 * 100)
    assert film.changed(0, 0.04, level=10, size=size, mask=mask) == pytest.approx(100 / 400 * 100)
    assert mask.share(size) == pytest.approx(50.0)
    assert [share for _, share in film.series(0, 0, 0.08, level=10, size=size, mask=mask)] == [0.0, 25.0]


def test_a_box_that_stops_on_its_lane_edge_changes_nothing_outside_its_mask_once_scaled_down(tmp_path: Path) -> None:
    # A black box crosses its lane and stops with its edge on the lane's own edge, the way a motion
    # arrives on its word. Every small pixel is read from the film pixels under it alone, so the lane,
    # masked out to every small pixel it touches, hides the whole crossing and nothing next to it reads
    # as a change.
    scale = 4
    lane = frames.Rect(40, 40, 160, 40)
    small = frames.Size(W // scale, H // scale)
    moved = (
        f"drawbox=x={lane.x}:y={lane.y}:w=40:h={lane.h}:color=black:t=fill:enable='eq(n,0)',"
        f"drawbox=x={lane.x + lane.w - 40}:y={lane.y}:w=40:h={lane.h}:color=black:t=fill:enable='eq(n,1)'"
    )
    film = tmp_path / "lane.mp4"
    with machine_tools():
        ffmpeg.run(
            "-f", "lavfi", "-i", f"color=c=white:s={W}x{H}:r={FPS}:d=0.08,{moved}",
            "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(film),
        )  # fmt: skip
        wanted = frames.Wanted()
        wanted.point(small, 0.0, 1 / FPS)
        decoded = frames.decode(film, wanted)
    mask = frames.Mask((lane,), frames.Size(W, H))
    assert decoded.changed(0.0, 1 / FPS, level=1, size=small) > 0.0
    assert decoded.changed(0.0, 1 / FPS, level=1, size=small, mask=mask) == 0.0


def test_the_motion_inside_one_box_is_read_frame_to_frame_as_a_share_of_its_own_area() -> None:
    size = frames.Size(40, 20)
    film = a_film({0: [(0, 0, 4, 4)]}, {0: [(1, 0, 4, 4)]}, {0: [(1, 0, 4, 4)]}, size=size)
    box = frames.Rect(0, 0, 10, 4)
    assert film.energy(0.04, 0.12, box, area=16, level=10, size=size) == [(0.04, 50.0), (0.08, 0.0)]
