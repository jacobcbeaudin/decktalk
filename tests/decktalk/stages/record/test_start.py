"""Finding narration t=0 in a recording, from the magenta cover the recorder painted over the page."""

from __future__ import annotations

from pathlib import Path

from decktalk.media import frames
from decktalk.settings import RecordConfig
from decktalk.stages.record.start import DEFAULT_FRAME_SECONDS, Start, find_start, frame_seconds, is_magenta

CFG = RecordConfig()
WEBM = Path("01.webm")


def stats(pts: float, yavg: float, uavg: float = 128.0, vavg: float = 128.0, ymax: float = 255.0):
    return frames.FrameStats(pts=pts, yavg=yavg, ymax=ymax, uavg=uavg, vavg=vavg)


def test_a_cover_frame_is_mid_luma_with_both_chroma_planes_high():
    assert is_magenta(stats(0.0, 105.0, 200.0, 210.0), CFG)
    assert not is_magenta(stats(0.0, 105.0, 128.0, 210.0), CFG)  # one plane is ordinary
    assert not is_magenta(stats(0.0, 10.0, 200.0, 210.0), CFG)  # too dark to be the cover


def test_one_frame_lasts_what_the_recording_says_it_lasts():
    rows = [stats(k * 0.04, 100.0) for k in range(6)]
    assert frame_seconds(rows) == 0.04
    assert frame_seconds(rows[:1]) == DEFAULT_FRAME_SECONDS
    assert frame_seconds([]) == DEFAULT_FRAME_SECONDS


def test_the_first_frame_after_the_cover_is_narration_zero(monkeypatch):
    rows = [stats(k * 0.04, 105.0, 200.0, 210.0) for k in range(5)] + [stats(0.20 + k * 0.04, 90.0) for k in range(5)]
    monkeypatch.setattr(frames, "frame_stats", lambda path, seconds: rows)
    start = find_start(WEBM, 0.6, CFG)
    assert start == Start(0.2, "cover (5 magenta frames)") and not start.guessed


def test_without_a_cover_the_first_painted_frame_plus_the_settle_is_the_guess(monkeypatch):
    rows = [stats(0.0, 2.0, ymax=5.0), stats(0.08, 90.0), stats(0.12, 90.0)]
    monkeypatch.setattr(frames, "frame_stats", lambda path, seconds: rows)
    start = find_start(WEBM, 0.6, CFG)
    assert start.seconds == 0.68 and start.method == "first paint + settle" and start.guessed


def test_with_no_paint_and_no_frames_the_fallback_says_it_is_a_guess(monkeypatch):
    monkeypatch.setattr(frames, "frame_stats", lambda path, seconds: [stats(0.0, 2.0, ymax=5.0)])
    dark = find_start(WEBM, 0.6, CFG)
    assert dark.seconds == round(CFG.fallback_first_paint_seconds + 0.6, 3) and dark.guessed
    assert dark.method == "fallback guess + settle"
    monkeypatch.setattr(frames, "frame_stats", lambda path, seconds: [])
    empty = find_start(WEBM, 0.6, CFG)
    assert empty.seconds == dark.seconds and empty.method == "no frames read"
