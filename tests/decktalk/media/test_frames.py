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


@pytest.mark.parametrize("read", [lambda: frames.luma_at(Path("a.webm"), 7.5)])
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


def test_the_cover_scan_reads_a_scaled_copy_of_each_frame(monkeypatch):
    """A full 1080p frame cost three times the scan and changed no verdict the scan makes."""
    argv: list[list[str]] = []
    monkeypatch.setattr(frames.ffmpeg, "stderr", lambda *a: argv.append(list(a)) or "")
    frames.frame_stats(Path("a.webm"), 4.0)
    assert graph(argv[0]).startswith(f"scale={frames.STATS_WIDTH}:{frames.STATS_HEIGHT},signalstats")
