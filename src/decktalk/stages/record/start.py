"""Where narration t=0 sits in a recording.

The recorder covers the page in magenta from its first paint until it starts the narration clock,
so the first clean frame after the magenta run is narration t=0 no matter when Chromium's capture
actually began. Without a cover the fallback is the first painted frame plus the settle, and failing
that a fixed guess, and both fallbacks say so, because a guessed start moves every reveal in the
section.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...media import frames
from ...settings import RecordConfig

DEFAULT_FRAME_SECONDS = 0.04  # One frame at 25 fps, used when a recording is too short to measure its own.
MIN_FRAME_SECONDS = 0.02  # No recording runs faster than 50 fps, so a shorter measured step is noise.


@dataclass(frozen=True)
class Start:
    """Where narration t=0 sits in one recording, how it was found, and whether it was measured."""

    seconds: float
    method: str  # One sentence for a reader, which nothing matches a verdict against.
    guessed: bool = False  # No magenta cover was found, so the start is an estimate and every reveal moves.


def is_magenta(frame: frames.FrameStats, cfg: RecordConfig) -> bool:
    """Whether one frame is the recorder's cover: mid luma with both chroma planes high."""
    return (
        cfg.cover_luma_min < frame.yavg < cfg.cover_luma_max
        and frame.uavg > cfg.cover_chroma_min
        and frame.vavg > cfg.cover_chroma_min
    )


def frame_seconds(rows: list[frames.FrameStats]) -> float:
    """How long one frame of this recording lasts, measured on the frames that were read."""
    if len(rows) < 2:
        return DEFAULT_FRAME_SECONDS
    return max(MIN_FRAME_SECONDS, (rows[-1].pts - rows[0].pts) / (len(rows) - 1))


def find_start(webm: Path, settle: float, cfg: RecordConfig) -> Start:
    """Narration t=0 in `webm`: the frame after the last magenta frame, or the best estimate of it."""
    rows = frames.frame_stats(webm, cfg.cover_scan_seconds)
    if not rows:
        return Start(round(cfg.fallback_first_paint_seconds + settle, 3), "no frames read", guessed=True)
    magenta = [f.pts for f in rows if is_magenta(f, cfg)]
    if magenta:
        return Start(round(magenta[-1] + frame_seconds(rows), 3), f"cover ({len(magenta)} magenta frames)")
    painted = [f.pts for f in rows if f.ymax > cfg.painted_ymax and f.yavg < cfg.painted_yavg_max]
    if painted:
        return Start(round(painted[0] + settle, 3), "first paint + settle", guessed=True)
    return Start(round(cfg.fallback_first_paint_seconds + settle, 3), "fallback guess + settle", guessed=True)
