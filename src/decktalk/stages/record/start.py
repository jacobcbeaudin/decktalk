"""Where narration t=0 sits in a recording.

The recorder covers the page in magenta from its first paint until it starts the section clock, so
the first clean frame after the magenta run is narration t=0 no matter when Chromium's capture
actually began. Without a cover the fallback is the first painted frame plus the settle, and failing
that a fixed guess, and both fallbacks say so, because a guessed start moves every reveal in the
section.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.recordings import Start
from decktalk.media import frames
from decktalk.page import CAPTURE_FPS, SECOND_DIGITS
from decktalk.settings import RecordConfig

DEFAULT_FRAME_SECONDS = 1 / CAPTURE_FPS
"""Derived: one frame at the rate the recorder captures at, for a recording too short to measure its own."""

FASTEST_CAPTURE_FPS = 50
"""Truth: no Chromium capture presents faster than this, so a shorter measured step is noise."""

MIN_FRAME_SECONDS = 1 / FASTEST_CAPTURE_FPS
"""Derived: the shortest frame a measurement may claim, which is one frame at the fastest capture rate."""


def is_cover(frame: frames.FrameStats, settings: RecordConfig) -> bool:
    """Whether one frame is the recorder's cover, which is mid luma with both chroma planes high."""
    return (
        settings.cover_luma_min < frame.yavg < settings.cover_luma_max
        and frame.uavg > settings.cover_chroma_min
        and frame.vavg > settings.cover_chroma_min
    )


def frame_seconds(rows: list[frames.FrameStats]) -> float:
    """How long one frame of this recording lasts, measured on the frames that were read."""
    if len(rows) < 2:
        return DEFAULT_FRAME_SECONDS
    return max(MIN_FRAME_SECONDS, (rows[-1].pts - rows[0].pts) / (len(rows) - 1))


def find_start(webm: Path, settle: float, settings: RecordConfig) -> Start:
    """Narration t=0 in `webm`, which is the frame after the last cover frame or the best estimate of it."""
    rows = frames.frame_stats(webm, settings.cover_scan_seconds)
    if not rows:
        fallback = round(settings.fallback_first_paint_seconds + settle, SECOND_DIGITS)
        return Start(
            seconds=fallback,
            method="no frames could be read, so the start is the fallback guess and the settle",
            guessed=True,
        )
    cover = [row.pts for row in rows if is_cover(row, settings)]
    if cover:
        found = round(cover[-1] + frame_seconds(rows), SECOND_DIGITS)
        return Start(seconds=found, method=f"the frame after the last of {len(cover)} cover frames")
    painted = [
        row.pts
        for row in rows
        if row.ymax > settings.painted_peak_luma_min and row.yavg < settings.painted_mean_luma_max
    ]
    if painted:
        return Start(
            seconds=round(painted[0] + settle, SECOND_DIGITS),
            method="the first painted frame and the settle",
            guessed=True,
        )
    fallback = round(settings.fallback_first_paint_seconds + settle, SECOND_DIGITS)
    return Start(
        seconds=fallback,
        method="no cover and no painted frame were found, so the start is the fallback guess",
        guessed=True,
    )


__all__ = ["DEFAULT_FRAME_SECONDS", "MIN_FRAME_SECONDS", "find_start", "frame_seconds", "is_cover"]
