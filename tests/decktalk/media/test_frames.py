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
