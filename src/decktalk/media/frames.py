"""Frame statistics on top of ffmpeg: luma, single frames, and changed-pixel comparisons.

Every comparison reads the luma plane only. An RGB image would carry the decoder's chroma
upsampling and clipping, and comparing it with a frame that never left YUV reports changed pixels
on colored edges that did not change.
"""

from __future__ import annotations

import math
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg


@dataclass(frozen=True)
class FrameStats:
    pts: float
    yavg: float
    ymax: float
    uavg: float
    vavg: float


def frame_stats(path: Path, seconds: float) -> list[FrameStats]:
    """Per-frame signalstats for the first `seconds` of the file."""
    out = ffmpeg.stderr("-t", str(seconds), "-i", str(path), "-vf", "signalstats,metadata=print", "-f", "null", "-")
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

LOOP_TAIL_SECONDS = 0.2
"""Truth: the looped reference outlasts the span it is compared against, so the shortest input ends the run."""


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
    err = ffmpeg.stderr(*seek.before, "-i", str(path), "-vf", vf, "-frames:v", "1", "-f", "null", "-")
    yavg = re.search(r"YAVG=([0-9.]+)", err)
    ymax = re.search(r"YMAX=([0-9.]+)", err)
    return (float(yavg.group(1)) if yavg else 0.0, float(ymax.group(1)) if ymax else 0.0)


def write_luma_frame(path: Path, t: float, target: Path, *, width: int, height: int) -> None:
    """Write the luma plane of the first frame at or after `t`, scaled to width x height, as a grayscale PNG.

    The comparisons below read luma only. An RGB image would carry the decoder's chroma
    upsampling and clipping, and comparing it with a frame that never left YUV reports
    changed pixels on colored edges that did not change.
    """
    seek = frame_seek(t)
    vf = f"{seek.trim()},scale={width}:{height},format=gray"
    ffmpeg.run(*seek.before, "-i", str(path), "-vf", vf, "-frames:v", "1", str(target))


def _changed_mask(level: int) -> str:
    """Filters that turn a luma difference into a mask of changed pixels and print its average."""
    return f"lut=c0='if(gt(val,{level}),255,0)',signalstats,metadata=print"


def changed_pixels_percent(path: Path, t1: float, t2: float, *, level: int, width: int, height: int) -> float:
    """Share (0-100) of pixels whose luma differs by more than `level` between the frames at t1 and t2.

    Each frame is extracted once as a grayscale image and the two images are compared, which
    every ffmpeg build handles the same way and costs two keyframe seeks.
    """
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a.png", Path(tmp) / "b.png"
        for t, target in ((t1, a), (t2, b)):
            write_luma_frame(path, t, target, width=width, height=height)
        err = ffmpeg.stderr(
            "-i", str(a), "-i", str(b), "-filter_complex",
            f"[0:v]format=gray[a];[1:v]format=gray[b];[a][b]blend=all_mode=difference,{_changed_mask(level)}",
            "-frames:v", "1", "-f", "null", "-",
        )  # fmt: skip
    m = re.search(r"YAVG=([0-9.]+)", err)
    return (float(m.group(1)) / 255 * 100) if m else 0.0


def changed_images_percent(a: Path, b: Path, *, level: int, width: int, height: int) -> float:
    """Share (0-100) of pixels whose luma differs by more than `level` between two still images.

    Both images are scaled to width x height and read as luma, as the frames of changed_pixels_percent are.
    """
    err = ffmpeg.stderr(
        "-i", str(a), "-i", str(b), "-filter_complex",
        f"[0:v]scale={width}:{height},format=gray[a];[1:v]scale={width}:{height},format=gray[b];"
        f"[a][b]blend=all_mode=difference,{_changed_mask(level)}",
        "-frames:v", "1", "-f", "null", "-",
    )  # fmt: skip
    m = re.search(r"YAVG=([0-9.]+)", err)
    return (float(m.group(1)) / 255 * 100) if m else 0.0


def changed_series(
    path: Path, ref_t: float, start: float, end: float, *, fps: int, level: int, width: int, height: int
) -> list[tuple[float, float]]:
    """Changed share against the frame at ref_t for every frame from start to end, as (time, percent) pairs.

    The reference frame is the first frame at or after ref_t. It is extracted once as a
    grayscale image and looped for the span, which every ffmpeg build handles the same way,
    and one run then compares the luma of each frame of the span with it. Times are the
    frames' own positions on the 1/fps grid. When start is ref_t, the first pair is the
    reference compared with itself, and its share is zero.
    """
    span = max(end - start, 0.0)
    if span <= 0:
        return []
    seek = frame_seek(start)
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "ref.png"
        write_luma_frame(path, ref_t, ref, width=width, height=height)
        fc = (
            f"[0:v]format=gray[r];"
            f"[1:v]{seek.trim(span)},scale={width}:{height},format=gray[b];"
            f"[r][b]blend=all_mode=difference:shortest=1,{_changed_mask(level)}"
        )
        err = ffmpeg.stderr(
            "-loop", "1", "-framerate", str(fps), "-t", f"{span + LOOP_TAIL_SECONDS:.3f}", "-i", str(ref),
            *seek.before, "-i", str(path),
            "-filter_complex", fc, "-f", "null", "-",
        )  # fmt: skip
    first = math.ceil(start * fps - 1e-6) / fps
    out: list[tuple[float, float]] = []
    pts: float | None = None
    for line in err.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            pts = float(m.group(1))
            continue
        mm = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if mm and pts is not None:
            out.append((round(first + pts, 3), round(float(mm.group(1)) / 255 * 100, 4)))
            pts = None
    return out
