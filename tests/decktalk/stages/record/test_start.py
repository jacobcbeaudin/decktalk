"""Where narration t=0 sits in a recording, and what happens when nothing marks it.

The recorder's cover is the only measurement of the start there is. Every other answer is an
estimate, so every test here also asserts that an estimate says so, because a guessed start moves
every reveal in its section.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.media import frames
from decktalk.settings import RecordConfig
from decktalk.stages.record.start import (
    DEFAULT_FRAME_SECONDS,
    MIN_FRAME_SECONDS,
    find_start,
    frame_seconds,
    is_cover,
)

SETTLE = 0.5
"""The settle a test passes, which every fallback adds to its own guess."""


def a_frame(pts: float, *, yavg: float = 100.0, ymax: float = 200.0, chroma: float = 200.0) -> frames.FrameStats:
    """One frame of signalstats, which is a cover frame unless a test says otherwise."""
    return frames.FrameStats(pts=pts, yavg=yavg, ymax=ymax, uavg=chroma, vavg=chroma)


@pytest.fixture
def settings() -> RecordConfig:
    return RecordConfig()


def read_as(monkeypatch: pytest.MonkeyPatch, rows: list[frames.FrameStats]) -> None:
    """Answer the frame scan with these rows, so no ffmpeg runs and no file has to exist."""
    monkeypatch.setattr(frames, "frame_stats", lambda _path, _seconds: rows)


def test_a_cover_frame_is_mid_luma_with_both_chroma_planes_high(settings: RecordConfig) -> None:
    assert is_cover(a_frame(0.0), settings)


def test_a_dark_frame_is_not_the_cover(settings: RecordConfig) -> None:
    assert not is_cover(a_frame(0.0, yavg=settings.cover_luma_min - 1), settings)


def test_a_grey_frame_is_not_the_cover_because_its_chroma_is_flat(settings: RecordConfig) -> None:
    assert not is_cover(a_frame(0.0, chroma=settings.cover_chroma_min - 1), settings)


def test_one_frame_cannot_measure_its_own_step() -> None:
    assert frame_seconds([a_frame(0.0)]) == DEFAULT_FRAME_SECONDS


def test_the_step_is_measured_over_the_frames_that_were_read() -> None:
    rows = [a_frame(0.0), a_frame(0.08), a_frame(0.16)]
    assert frame_seconds(rows) == pytest.approx(0.08)


def test_a_step_under_the_fastest_capture_reads_as_noise() -> None:
    assert frame_seconds([a_frame(0.0), a_frame(0.001)]) == MIN_FRAME_SECONDS


def test_the_start_is_the_frame_after_the_last_cover_frame(
    monkeypatch: pytest.MonkeyPatch, settings: RecordConfig, tmp_path: Path
) -> None:
    read_as(monkeypatch, [a_frame(0.0), a_frame(0.04), a_frame(0.08, chroma=128.0)])
    start = find_start(tmp_path / "01.webm", SETTLE, settings)
    assert start.seconds == pytest.approx(0.08)
    assert not start.guessed
    assert "cover" in start.method


def test_no_cover_falls_back_to_the_first_painted_frame_and_says_so(
    monkeypatch: pytest.MonkeyPatch, settings: RecordConfig, tmp_path: Path
) -> None:
    painted = a_frame(
        0.2, yavg=settings.painted_mean_luma_max - 1, ymax=settings.painted_peak_luma_min + 1, chroma=128.0
    )
    read_as(monkeypatch, [a_frame(0.0, yavg=0.0, ymax=0.0, chroma=128.0), painted])
    start = find_start(tmp_path / "01.webm", SETTLE, settings)
    assert start.seconds == pytest.approx(0.2 + SETTLE)
    assert start.guessed


def test_a_recording_with_no_picture_at_all_falls_back_to_the_fixed_guess(
    monkeypatch: pytest.MonkeyPatch, settings: RecordConfig, tmp_path: Path
) -> None:
    read_as(monkeypatch, [a_frame(0.0, yavg=0.0, ymax=0.0, chroma=128.0)])
    start = find_start(tmp_path / "01.webm", SETTLE, settings)
    assert start.seconds == pytest.approx(settings.fallback_first_paint_seconds + SETTLE)
    assert start.guessed


def test_a_recording_whose_frames_cannot_be_read_is_a_guess(
    monkeypatch: pytest.MonkeyPatch, settings: RecordConfig, tmp_path: Path
) -> None:
    read_as(monkeypatch, [])
    start = find_start(tmp_path / "01.webm", SETTLE, settings)
    assert start.guessed
    assert start.seconds == pytest.approx(settings.fallback_first_paint_seconds + SETTLE)
