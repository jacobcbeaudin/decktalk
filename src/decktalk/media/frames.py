"""Frame statistics on top of ffmpeg: luma, single frames, and changed-pixel comparisons.

Every comparison reads the luma plane only. An RGB image would carry the decoder's chroma
upsampling and clipping, and comparing it with a frame that never left YUV reports changed pixels
on colored edges that did not change.

A film is measured by decoding it once per size and keeping only the frames a measurement planned
to read. `Wanted` is that plan, `decode` streams the film through it, and `Decoded` answers every
comparison in this process. One ffmpeg call per frame compared would read the same frame up to six times,
and holding every frame of a long film would cost more memory than the render it checks.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from functools import cache
from pathlib import Path

from . import ffmpeg


@dataclass(frozen=True)
class FrameStats:
    pts: float
    yavg: float
    ymax: float
    uavg: float
    vavg: float


STATS_WIDTH = 480
"""Calibration: the width a frame's statistics are read at, a quarter of 1080p's, as `STATS_HEIGHT` says why."""

STATS_HEIGHT = 270
"""Calibration: the height a frame's statistics are read at, a quarter of 1080p's.

Averages barely move under the scale, and on the starter's recordings every frame read as a cover or
as painted exactly as it did at full size, while the scan took a third of the time. The brightest
pixel falls by a few levels, far inside the thresholds it is compared against.
"""


def frame_stats(path: Path, seconds: float) -> list[FrameStats]:
    """Per-frame signalstats for the first `seconds` of the file, read on a scaled copy of each frame."""
    vf = f"scale={STATS_WIDTH}:{STATS_HEIGHT},signalstats,metadata=print"
    out = ffmpeg.stderr("-t", str(seconds), *ffmpeg.source(path), "-vf", vf, "-f", "null", "-")
    frames: list[FrameStats] = []
    cur: dict[str, float] = {}
    for line in out.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            if "YAVG" in cur:
                frames.append(_stats(cur))
            cur = {"pts": float(m.group(1))}
            continue
        mm = re.search(r"lavfi\.signalstats\.(YAVG|UAVG|VAVG|YMAX)=([0-9.]+)", line)
        if mm and cur:
            cur[mm.group(1)] = float(mm.group(2))
    if "YAVG" in cur:
        frames.append(_stats(cur))
    return frames


def _stats(d: dict[str, float]) -> FrameStats:
    return FrameStats(
        pts=d["pts"],
        yavg=d.get("YAVG", 0.0),
        ymax=d.get("YMAX", 0.0),
        uavg=d.get("UAVG", 128.0),
        vavg=d.get("VAVG", 128.0),
    )


COARSE_SEEK_SECONDS = 3.0
"""Truth: far enough back that a decoder passes a keyframe before `t`, and near enough to stay quick."""


@dataclass(frozen=True)
class Seek:
    """How one frame is reached: a coarse jump that goes before the input, and the exact remainder after it."""

    before: tuple[str, ...]  # the arguments that go ahead of -i, which is the coarse jump
    after: float  # the seconds still to drop once the input is open, which is the exact part

    def trim(self, seconds: float | None = None) -> str:
        """The filters that drop what the coarse jump overshot, so the first frame out is the one asked for.

        The remainder is applied inside the graph rather than on the output, because `metadata=print`
        reports on every frame the graph sees and an output seek drops frames that have already been
        reported. `seconds` bounds the span that follows, for the readers that want more than one frame.
        """
        span = "" if seconds is None else f":duration={seconds:.3f}"
        return f"trim=start={self.after:.3f}{span},setpts=PTS-STARTPTS"


def frame_seek(t: float) -> Seek:
    """The one way this module reaches the frame at `t`, which every reader of a frame goes through.

    Seeking before the input is fast but it lands on the frame the container's own timing says is
    there, and a recording whose frames were stamped by a busy compositor moves that by a frame.
    Jumping to a little before `t` on the input and then dropping the remainder in the graph is both
    quick and exact. Every measurement here compares two frames, so a reader that took the coarse
    jump alone would compare one frame against another frame's neighbour.
    """
    coarse = max(0.0, t - COARSE_SEEK_SECONDS)
    return Seek(before=("-ss", f"{coarse:.3f}"), after=t - coarse)


def luma_at(path: Path, t: float, *, crop: str | None = None) -> tuple[float, float]:
    """(YAVG, YMAX) of the frame at t, optionally after crop=w:h:x:y."""
    seek = frame_seek(t)
    vf = f"{seek.trim()}," + (f"crop={crop}," if crop else "") + "signalstats,metadata=print"
    err = ffmpeg.stderr(*seek.before, *ffmpeg.source(path), "-vf", vf, "-frames:v", "1", "-f", "null", "-")
    yavg = re.search(r"YAVG=([0-9.]+)", err)
    ymax = re.search(r"YMAX=([0-9.]+)", err)
    return (float(yavg.group(1)) if yavg else 0.0, float(ymax.group(1)) if ymax else 0.0)


def _changed_mask(level: int) -> str:
    """Filters that turn a luma difference into a mask of changed pixels and print its average."""
    return f"lut=c0='if(gt(val,{level}),255,0)',signalstats,metadata=print"


def changed_images_percent(a: Path, b: Path, *, level: int, width: int, height: int) -> float:
    """Share (0-100) of pixels whose luma differs by more than `level` between two still images.

    Both images are scaled to width x height and read as luma, as the frames of changed_pixels_percent are.
    """
    err = ffmpeg.stderr(
        *ffmpeg.source(a), *ffmpeg.source(b), "-filter_complex",
        f"[0:v]scale={width}:{height},format=gray[a];[1:v]scale={width}:{height},format=gray[b];"
        f"[a][b]blend=all_mode=difference,{_changed_mask(level)}",
        "-frames:v", "1", "-f", "null", "-",
    )  # fmt: skip
    m = re.search(r"YAVG=([0-9.]+)", err)
    return (float(m.group(1)) / 255 * 100) if m else 0.0


# ---- one decode of a film, and every comparison in process -------------------------------------

GRID_SLACK = 1e-6
"""Truth: far under one frame, so a time that sits on a frame's own stamp names that frame and not the next."""

LANE_BITS = 16
"""Truth: each pixel is compared in a sixteen bit lane of one integer, which holds a difference and its sign."""

LANE_BIAS = 256
"""Truth: added to every lane before the subtraction, so a difference of two bytes never borrows from its neighbour."""

LANE_TOP = 1 << (LANE_BITS - 1)
"""Truth: the top bit of a lane, which a comparison sets when the pixel it holds changed."""


@dataclass(frozen=True)
class Size:
    """The width and height a film is scaled to before a comparison reads it."""

    width: int
    height: int


@dataclass
class Wanted:
    """The frames a measurement will read, as moments and spans at each size, before the film's rate is known.

    A moment names the first frame at or after it, and a span names every frame from its start up to
    but not including its end, which is what the two comparisons below read.
    """

    points: dict[Size, set[float]] = field(default_factory=dict)
    spans: dict[Size, set[tuple[float, float]]] = field(default_factory=dict)

    def point(self, size: Size, *moments: float) -> None:
        """Plan to read the frame at each moment, at one size."""
        self.points.setdefault(size, set()).update(moments)

    def span(self, size: Size, start: float, end: float) -> None:
        """Plan to read every frame from `start` up to `end`, at one size."""
        self.spans.setdefault(size, set()).add((start, end))

    def indices(self, rate: Fraction, count: float) -> dict[Size, set[int]]:
        """The frame numbers the plan names at each size, on a film of `count` frames at `rate`."""
        wanted: dict[Size, set[int]] = {}
        for size in set(self.points) | set(self.spans):
            chosen = {frame_index(t, rate, count) for t in self.points.get(size, ())}
            for start, end in self.spans.get(size, ()):
                chosen.update(span_indices(start, end, rate, count))
            wanted[size] = chosen
        return wanted


def frame_index(t: float, rate: Fraction, count: float) -> int:
    """The first frame at or after `t`, or the last frame of a film `count` frames long when `t` is past it."""
    return int(min(count - 1, max(0, math.ceil(t * rate - GRID_SLACK))))


def span_indices(start: float, end: float, rate: Fraction, count: float) -> range:
    """Every frame from the one at `start` up to, and not including, the one at `end`."""
    stop = int(min(count, max(0, math.ceil(end * rate - GRID_SLACK))))
    return range(frame_index(start, rate, count), stop)


@cache
def _repeated(lanes: int, value: int) -> int:
    """`value` in every one of `lanes` sixteen bit lanes, as one integer, which every frame of one size shares."""
    return int.from_bytes(value.to_bytes(LANE_BITS // 8, "big") * lanes, "big")


def _widened(frame: bytes) -> int:
    """A frame's bytes as one integer, each byte in the low half of its own sixteen bit lane."""
    wide = bytearray(2 * len(frame))
    wide[1::2] = frame
    return int.from_bytes(wide, "big")


def changed_count(a: bytes, b: bytes, level: int) -> int:
    """How many pixels differ by more than `level` between two frames of one size.

    The frames are compared as two large integers, a pixel to a lane, so the whole comparison is a
    handful of arithmetic steps on numbers the interpreter works on in native code. Each lane holds
    256 plus one pixel less the other, which is between 1 and 511 and so never borrows. A pixel that
    rose by more than `level` carries into the lane's top bit when the lane is raised by the rest of
    half a lane, and one that fell by more than `level` leaves the top bit set when the lane is taken
    from half a lane plus what is left below the bias, so one mask and a bit count answer both.
    """
    lanes = len(a)
    one = _repeated(lanes, 1)
    lifted = _widened(a) + LANE_BIAS * one - _widened(b)
    rose = lifted + (LANE_TOP - 1 - LANE_BIAS - level) * one
    fell = (LANE_TOP + LANE_BIAS - 1 - level) * one - lifted
    return ((rose | fell) & (LANE_TOP * one)).bit_count()


@dataclass(frozen=True)
class Decoded:
    """The frames of one film a plan asked for, at each size, and every comparison over them.

    `ends` is how many frames the film holds, as a decode of each size found, or infinity where the
    decode stopped at the last planned frame before the film ended.
    """

    rate: Fraction
    frames: dict[Size, dict[int, bytes]]
    ends: dict[Size, float]

    def at(self, t: float, size: Size) -> bytes:
        """The luma of the first frame at or after `t`, at one size, which the plan must have named."""
        return self.frames[size][frame_index(t, self.rate, self.ends[size])]

    def changed(self, t1: float, t2: float, *, level: int, size: Size) -> float:
        """Share (0-100) of pixels whose luma differs by more than `level` between the frames at t1 and t2."""
        a, b = self.at(t1, size), self.at(t2, size)
        return changed_count(a, b, level) / len(a) * 100

    def series(self, ref_t: float, start: float, end: float, *, level: int, size: Size) -> list[tuple[float, float]]:
        """Changed share against the frame at ref_t for every frame from start up to end, as (time, percent) pairs.

        Times are the frames' own positions on the film's grid, so when start is ref_t the first pair
        is the reference against itself, and its share is zero.
        """
        reference = self.at(ref_t, size)
        kept = self.frames[size]
        rows: list[tuple[float, float]] = []
        for index in span_indices(start, end, self.rate, self.ends[size]):
            share = changed_count(reference, kept[index], level) / len(reference) * 100
            rows.append((round(float(index / self.rate), 3), round(share, 4)))
        return rows


def decode(path: Path, wanted: Wanted) -> Decoded:
    """Every frame `wanted` names, decoded in one pass of the film per size and kept as luma bytes.

    The film is streamed, and a frame the plan did not name is dropped as soon as it has been counted,
    so memory holds the planned frames and nothing else, however long the film runs. The last frame
    a decode reaches is kept as well, because a moment past the end of the film reads the last frame
    there is, as a seek past the end does.
    """
    if not wanted.points and not wanted.spans:
        return Decoded(rate=Fraction(1), frames={}, ends={})
    rate = ffmpeg.probe_rate(path)
    frames: dict[Size, dict[int, bytes]] = {}
    ends: dict[Size, float] = {}
    for size, chosen in wanted.indices(rate, math.inf).items():
        last = max(chosen, default=-1)
        frames[size], seen = _kept(path, size, chosen, last)
        ends[size] = seen if seen <= last else math.inf
    return Decoded(rate=rate, frames=frames, ends=ends)


def _kept(path: Path, size: Size, wanted: set[int], last: int) -> tuple[dict[int, bytes], int]:
    """The frames at `wanted` of one decode of the film at `size`, and how many frames the decode reached."""
    frame_bytes = size.width * size.height
    kept: dict[int, bytes] = {}
    pending = bytearray()
    seen = 0
    final = b""

    def take(chunk: bytes) -> None:
        nonlocal seen, final
        pending.extend(chunk)
        while len(pending) >= frame_bytes:
            frame = bytes(pending[:frame_bytes])
            if seen in wanted:
                kept[seen] = frame
            final = frame
            del pending[:frame_bytes]
            seen += 1

    if last >= 0:
        ffmpeg.stream(
            *ffmpeg.source(path),
            "-frames:v", str(last + 1),
            "-vf", f"scale={size.width}:{size.height},format=gray",
            "-f", "rawvideo", "-",
            into=take,
        )  # fmt: skip
    if seen:
        kept.setdefault(seen - 1, final)
    return kept, seen
