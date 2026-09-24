"""Frame comparison against real ffmpeg: what a static picture reports, and what a reveal reports."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from decktalk.media import frames
from support.media_cards import FPS, PANEL_PERCENT, H, W, write_card

pytestmark = pytest.mark.media


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> Path:
    return write_card(tmp_path_factory.mktemp("media") / "card.mp4")


def _grid(t: float) -> float:
    """The time of the first frame at or after t."""
    return round(math.ceil(t * FPS - 1e-6) / FPS, 3)


@pytest.mark.parametrize("ref_t", [4.40, 4.41, 4.43, 4.439, 4.44])
def test_changed_series_reads_zero_on_a_static_colored_card_at_every_grid_phase(card, ref_t):
    series = frames.changed_series(card, ref_t, ref_t, ref_t + 0.4, fps=FPS, level=12, width=W, height=H)
    assert series, "ffmpeg returned no frames"
    # The first pair is the reference compared with itself, on the frame at or after ref_t.
    assert series[0][0] == _grid(ref_t)
    # Nothing on the card changes before 5.00 s, so no frame may report a changed pixel.
    assert [p for _, p in series] == [0.0] * len(series)


def test_changed_series_times_are_the_frames_own_positions(card):
    series = frames.changed_series(card, 4.93, 4.93, 5.2, fps=FPS, level=12, width=W, height=H)
    shares = dict(series)
    assert shares[4.96] == 0.0
    assert 0.2 < shares[5.0] < 0.45  # the black square alone, 400 px
    assert PANEL_PERCENT * 0.9 < shares[5.04] < PANEL_PERCENT * 1.1


def test_changed_pixels_percent_reads_zero_for_the_same_colored_picture(card):
    assert frames.changed_pixels_percent(card, 1.0, 4.0, level=12, width=W, height=H) == 0.0
    assert frames.changed_pixels_percent(card, 4.43, 4.44, level=12, width=W, height=H) == 0.0
    share = frames.changed_pixels_percent(card, 4.9, 5.5, level=12, width=W, height=H)
    assert PANEL_PERCENT * 0.9 < share < PANEL_PERCENT * 1.1


def graph(argv: list[str]) -> str:
    """The filter graph of one ffmpeg command, whichever flag carried it."""
    flag = "-vf" if "-vf" in argv else "-filter_complex"
    return argv[argv.index(flag) + 1]


@pytest.mark.parametrize(
    "read",
    [
        lambda: frames.luma_at(Path("a.webm"), 7.5),
        lambda: frames.write_luma_frame(Path("a.webm"), 7.5, Path("out.png"), width=W, height=H),
        lambda: frames.changed_series(Path("a.webm"), 7.5, 7.5, 7.9, fps=FPS, level=12, width=W, height=H),
    ],
)
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


def test_the_series_bounds_its_trim_by_the_span_it_compares(monkeypatch):
    argv: list[list[str]] = []
    monkeypatch.setattr(frames.ffmpeg, "stderr", lambda *a: argv.append(list(a)) or "")
    monkeypatch.setattr(frames, "write_luma_frame", lambda *a, **k: None)
    frames.changed_series(Path("a.webm"), 7.0, 7.5, 7.9, fps=FPS, level=12, width=W, height=H)
    assert "trim=start=3.000:duration=0.400" in graph(argv[0])


@pytest.mark.media
@pytest.mark.parametrize(("t", "revealed"), [(4.80, False), (4.96, False), (5.04, True), (5.20, True)])
def test_luma_at_reads_the_frame_it_was_asked_for_and_not_the_one_the_jump_landed_on(card, t, revealed):
    """The card holds one keyframe and reveals at 5.00 s, so a frame either side of it reads differently."""
    yavg, _ymax = frames.luma_at(card, t)
    assert (yavg < 225.0) is revealed, (t, yavg)
