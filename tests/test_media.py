"""Frame analysis against real ffmpeg on a synthetic video.

These tests need the ffmpeg that `decktalk setup` fetches, so they carry the media marker:

    uv run pytest -m media

The video is built from ffmpeg's lavfi sources with colored edges, because a comparison that
leaves YUV reports changed pixels on colored edges that did not change, and a gray test card
would hide that. It runs past the coarse seek in frame_seek, and its keyframes are sparse, the
way an assembled section's are.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from decktalk.config import Settings
from decktalk.media import ffmpeg
from decktalk.stages.verify import first_change_offset

pytestmark = pytest.mark.media

FPS = 25
W, H = 480, 270
REVEAL_FRAME = 125  # 5.00 s. A small black square appears on this frame.
FULL_FRAME = 126  # 5.04 s. The full accent panel covers it on the next frame.
PANEL_PERCENT = 100 * 60 / (W * H) * 100  # the 100 x 60 panel's share of the picture


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> Path:
    """Six seconds of a static colored card, then a two-frame reveal at 5.00 s."""
    out = tmp_path_factory.mktemp("media") / "card.mp4"
    boxes = [
        "drawbox=x=20:y=20:w=220:h=6:color=0x2c1fea:t=fill",
        "drawbox=x=20:y=40:w=3:h=200:color=0xff0000:t=fill",
        "drawbox=x=30:y=40:w=2:h=200:color=0x00c000:t=fill",
        "drawbox=x=40:y=40:w=1:h=200:color=0xff00ff:t=fill",
        "drawbox=x=60:y=40:w=160:h=160:color=0x2c1fea:t=3",
        "drawbox=x=90:y=70:w=100:h=100:color=0xffcc00:t=2",
        f"drawbox=x=320:y=120:w=20:h=20:color=black:t=fill:enable='gte(n,{REVEAL_FRAME})'",
        f"drawbox=x=300:y=100:w=100:h=60:color=0x2c1fea:t=fill:enable='gte(n,{FULL_FRAME})'",
    ]
    ffmpeg.run(
        "-f", "lavfi", "-i", f"color=c=white:s={W}x{H}:r={FPS}:d=6," + ",".join(boxes),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "250", "-crf", "18", str(out),
    )  # fmt: skip
    return out


def _grid(t: float) -> float:
    """The time of the first frame at or after t."""
    return round(math.ceil(t * FPS - 1e-6) / FPS, 3)


@pytest.mark.parametrize("ref_t", [4.40, 4.41, 4.43, 4.439, 4.44])
def test_changed_series_reads_zero_on_a_static_colored_card_at_every_grid_phase(card, ref_t):
    series = ffmpeg.changed_series(card, ref_t, ref_t, ref_t + 0.4, fps=FPS, level=12, width=W, height=H)
    assert series, "ffmpeg returned no frames"
    # The first pair is the reference compared with itself, on the frame at or after ref_t.
    assert series[0][0] == _grid(ref_t)
    # Nothing on the card changes before 5.00 s, so no frame may report a changed pixel.
    assert [p for _, p in series] == [0.0] * len(series)


def test_changed_series_times_are_the_frames_own_positions(card):
    series = ffmpeg.changed_series(card, 4.93, 4.93, 5.2, fps=FPS, level=12, width=W, height=H)
    shares = dict(series)
    assert shares[4.96] == 0.0
    assert 0.2 < shares[5.0] < 0.45  # the black square alone, 400 px
    assert PANEL_PERCENT * 0.9 < shares[5.04] < PANEL_PERCENT * 1.1


def test_changed_pixels_percent_reads_zero_for_the_same_colored_picture(card):
    assert ffmpeg.changed_pixels_percent(card, 1.0, 4.0, level=12, width=W, height=H) == 0.0
    assert ffmpeg.changed_pixels_percent(card, 4.43, 4.44, level=12, width=W, height=H) == 0.0
    share = ffmpeg.changed_pixels_percent(card, 4.9, 5.5, level=12, width=W, height=H)
    assert PANEL_PERCENT * 0.9 < share < PANEL_PERCENT * 1.1


@pytest.fixture(scope="module")
def moving_card(tmp_path_factory) -> Path:
    """A two-pixel dot moves every frame, and the accent panel appears at 5.04 s."""
    out = tmp_path_factory.mktemp("media") / "moving.mp4"
    graph = (
        f"color=c=white:s={W}x{H}:r={FPS}:d=6,drawbox=x=20:y=20:w=220:h=6:color=0x2c1fea:t=fill,"
        f"drawbox=x=300:y=100:w=100:h=60:color=0x2c1fea:t=fill:enable='gte(n,{FULL_FRAME})'[bg];"
        f"color=c=0x333333:s=2x2:r={FPS}:d=6[dot];[bg][dot]overlay=x='60+3*n':y=230:eval=frame[out0]"
    )
    ffmpeg.run(
        "-f", "lavfi", "-i", graph,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "250", "-crf", "18", str(out),
    )  # fmt: skip
    return out


@pytest.mark.parametrize("before", [4.88, 4.93, 4.959])
def test_onset_ignores_a_few_pixels_of_motion_before_the_reveal(moving_card, before):
    cfg = Settings().verify
    series = ffmpeg.changed_series(moving_card, before, before, 5.2, fps=FPS, level=12, width=W, height=H)
    motion = max(p for t, p in series if t < FULL_FRAME / FPS)
    # The dot is a real change of a few pixels, and it must stay under the onset threshold.
    assert 0.0 < motion < cfg.onset_percent
    assert first_change_offset(moving_card, before, 5.7, FULL_FRAME / FPS, cfg, FPS) == 0


@pytest.mark.parametrize("before", [4.88, 4.89, 4.90, 4.92, 4.93, 4.959])
def test_onset_is_the_first_revealed_frame_at_every_grid_phase(card, before):
    cfg = Settings().verify
    reveal = REVEAL_FRAME / FPS
    assert first_change_offset(card, before, 5.7, reveal, cfg, FPS) == 0
    # A cue between two frames reports the distance to the frame that shows the reveal.
    assert first_change_offset(card, before, 5.7, reveal + 0.02, cfg, FPS) == -20
