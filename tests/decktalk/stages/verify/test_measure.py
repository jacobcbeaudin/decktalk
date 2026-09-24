"""The ffmpeg calls behind the cue plan: the section starts, the probes and the click search."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.media import ffmpeg, frames
from decktalk.settings import Settings
from decktalk.stages.verify.measure import assembled_starts, click_offset_ms, first_change_offset
from support.media_cards import FPS, FULL_FRAME, REVEAL_FRAME, H, W, write_card


def test_click_search_stays_inside_the_section(monkeypatch):
    from decktalk.media import audio as audio_module

    calls: list[tuple[float, float]] = []

    def fake_span(path, start, seconds, *, sample_rate=48000):
        calls.append((round(start, 3), round(seconds, 3)))
        return [0] * 100 + [2000] + [0] * 100

    monkeypatch.setattr(audio_module, "pcm_span", fake_span)
    assert click_offset_ms(Path("f.mp4"), 10.0, 0.25) is not None
    assert click_offset_ms(Path("f.mp4"), 5.1, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 8.9, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 9.5, 0.25, floor=5.0, ceiling=9.0) is None
    assert calls == [(9.75, 0.5), (5.0, 0.35), (8.65, 0.35)]


def test_the_section_starts_are_the_cumulative_lengths_of_the_assembled_sections(verify_project):
    project = verify_project({"01": "a@1.0"})
    starts, total = assembled_starts(project)
    assert starts == {"01": 0.0, "02": 5.0} and total == 10.0
    # Section 3 has no sections/03.mp4, so it is not counted and not a start.
    assert "03" not in starts


@pytest.fixture(scope="module")
def card(tmp_path_factory) -> Path:
    return write_card(tmp_path_factory.mktemp("media") / "card.mp4")


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


@pytest.fixture(scope="module")
def ringing_card(tmp_path_factory) -> Path:
    """A 1080p card whose accent panel appears at 5.00 s, after two frames of encoder-like ringing.

    On the two frames before the panel, a checkerboard of 4 px cells 30 levels either side of the
    gray card covers the panel's area, the way x264 leaves a little ringing on a still picture just
    before a change. It survives the scale to 480 by 270 but averages to nothing over an 8 by 8
    block, so it is not a reveal.
    """
    out = tmp_path_factory.mktemp("media") / "ringing.mp4"
    ring = f"between(N,{REVEAL_FRAME - 2},{REVEAL_FRAME - 1})*between(X,1200,1599)*between(Y,400,639)"
    graph = (
        f"color=c=gray:s=1920x1080:r={FPS}:d=6,format=gray,"
        f"geq=lum='if({ring},158-60*mod(floor(X/4)+floor(Y/4)\\,2),128)',"
        f"drawbox=x=1200:y=400:w=400:h=240:color=0x2c1fea:t=fill:enable='gte(n,{REVEAL_FRAME})',format=yuv420p"
    )
    ffmpeg.run("-f", "lavfi", "-i", graph, "-c:v", "libx264", "-qp", "0", "-g", "250", str(out))  # fmt: skip
    return out


@pytest.mark.media
@pytest.mark.parametrize("before", [4.88, 4.93, 4.959])
def test_onset_ignores_a_few_pixels_of_motion_before_the_reveal(moving_card, before):
    cfg = Settings().verify
    series = frames.changed_series(moving_card, before, before, 5.2, fps=FPS, level=12, width=W, height=H)
    motion = max(p for t, p in series if t < FULL_FRAME / FPS)
    # The dot is a real change of a few pixels, and it must stay under the onset threshold.
    assert 0.0 < motion < cfg.onset_percent
    assert first_change_offset(moving_card, before, 5.7, FULL_FRAME / FPS, cfg, FPS) == 0


@pytest.mark.media
def test_onset_ignores_encoder_ringing_before_the_reveal(ringing_card):
    cfg = Settings().verify
    before = 4.84
    # At the comparison size the ringing is a real change of far more than onset_percent ...
    series = frames.changed_series(ringing_card, before, before, 5.2, fps=FPS, level=12, width=W, height=H)
    assert max(p for t, p in series if t < REVEAL_FRAME / FPS) > 10 * cfg.onset_percent
    # ... but no 8 by 8 block changes, so the onset scan still finds the panel on its own frame.
    assert first_change_offset(ringing_card, before, 5.7, REVEAL_FRAME / FPS, cfg, FPS) == 0


@pytest.mark.media
@pytest.mark.parametrize("before", [4.88, 4.89, 4.90, 4.92, 4.93, 4.959])
def test_onset_is_the_first_revealed_frame_at_every_grid_phase(card, before):
    cfg = Settings().verify
    reveal = REVEAL_FRAME / FPS
    assert first_change_offset(card, before, 5.7, reveal, cfg, FPS) == 0
    # A cue between two frames reports the distance to the frame that shows the reveal.
    assert first_change_offset(card, before, 5.7, reveal + 0.02, cfg, FPS) == -20
