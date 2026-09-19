"""Frame statistics on top of ffmpeg: luma, single frames, and changed-pixel comparisons.

Every comparison reads the luma plane only. An RGB image would carry the decoder's chroma
upsampling and clipping, and comparing it with a frame that never left YUV reports changed pixels
on colored edges that did not change.
"""

from __future__ import annotations

import math
import re
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


def luma_at(path: Path, t: float, *, crop: str | None = None) -> tuple[float, float]:
    """(YAVG, YMAX) of the frame at t, optionally after crop=w:h:x:y."""
    vf = (f"crop={crop}," if crop else "") + "signalstats,metadata=print"
    err = ffmpeg.stderr("-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", vf, "-f", "null", "-")
    yavg = re.search(r"YAVG=([0-9.]+)", err)
    ymax = re.search(r"YMAX=([0-9.]+)", err)
    return (float(yavg.group(1)) if yavg else 0.0, float(ymax.group(1)) if ymax else 0.0)


def frame_seek(t: float) -> tuple[list[str], str]:
    """A coarse input seek and the exact output seek that follows it, for one frame at `t`.

    Seeking before the input is fast but some builds land on a keyframe rather than the
    frame asked for, and seeking after the input is exact but decodes from wherever the
    input starts. Jumping to a little before `t` on the input and then seeking the small
    remainder on the output is both quick and exact on every build.
    """
    coarse = max(0.0, t - 3.0)
    return ["-ss", f"{coarse:.3f}"], f"{t - coarse:.3f}"


def write_luma_frame(path: Path, t: float, target: Path, *, width: int, height: int) -> None:
    """Write the luma plane of the first frame at or after `t`, scaled to width x height, as a grayscale PNG.

    The comparisons below read luma only. An RGB image would carry the decoder's chroma
    upsampling and clipping, and comparing it with a frame that never left YUV reports
    changed pixels on colored edges that did not change.
    """
    pre, rest = frame_seek(t)
    vf = f"scale={width}:{height},format=gray"
    ffmpeg.run(*pre, "-i", str(path), "-ss", rest, "-frames:v", "1", "-vf", vf, str(target))


def _changed_mask(level: int) -> str:
    """Filters that turn a luma difference into a mask of changed pixels and print its average."""
    return f"lut=c0='if(gt(val,{level}),255,0)',signalstats,metadata=print"


def changed_pixels_percent(path: Path, t1: float, t2: float, *, level: int, width: int, height: int) -> float:
    """Share (0-100) of pixels whose luma differs by more than `level` between the frames at t1 and t2.

    Each frame is extracted once as a grayscale image and the two images are compared, which
    every ffmpeg build handles the same way and costs two keyframe seeks.
    """
    import tempfile

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
    import tempfile

    span = max(end - start, 0.0)
    if span <= 0:
        return []
    pre_s, rest_s = frame_seek(start)
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "ref.png"
        write_luma_frame(path, ref_t, ref, width=width, height=height)
        fc = (
            f"[0:v]format=gray[r];"
            f"[1:v]trim=start={rest_s}:duration={span:.3f},setpts=PTS-STARTPTS,scale={width}:{height},format=gray[b];"
            f"[r][b]blend=all_mode=difference:shortest=1,{_changed_mask(level)}"
        )
        err = ffmpeg.stderr(
            "-loop", "1", "-framerate", str(fps), "-t", f"{span + 0.2:.3f}", "-i", str(ref),
            *pre_s, "-i", str(path),
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
