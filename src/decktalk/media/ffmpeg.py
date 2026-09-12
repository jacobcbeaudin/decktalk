"""ffmpeg and ffprobe: resolution, invocation, probing, and the frame-analysis helpers.

Binaries come from PATH when present; otherwise the static-ffmpeg package fetches
platform builds on first use (`decktalk setup` does this ahead of time).
DECKTALK_FFMPEG and DECKTALK_FFPROBE override both.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..errors import ToolError

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def ffmpeg_paths() -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables."""
    env_ff, env_fp = os.environ.get("DECKTALK_FFMPEG"), os.environ.get("DECKTALK_FFPROBE")
    if env_ff and env_fp:
        return env_ff, env_fp
    on_path = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if on_path[0] and on_path[1]:
        return on_path[0], on_path[1]
    try:
        from static_ffmpeg import run as static_run

        ff, fp = static_run.get_or_fetch_platform_executables_else_raise()
        return str(ff), str(fp)
    except Exception as exc:  # pragma: no cover - network / platform dependent
        raise ToolError(
            "ffmpeg/ffprobe not found on PATH and static-ffmpeg could not provide them "
            f"({exc}). Run `decktalk setup` with network access, or install ffmpeg."
        ) from exc


def ffmpeg() -> str:
    return ffmpeg_paths()[0]


def ffprobe() -> str:
    return ffmpeg_paths()[1]


def run(*args: str) -> None:
    """ffmpeg -hide_banner -loglevel error -y ARGS. Raises ToolError with ffmpeg's message."""
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-6:]
        raise ToolError("ffmpeg failed: " + " | ".join(tail))


def stderr(*args: str) -> str:
    """ffmpeg run whose useful output is on stderr (metadata=print, silencedetect, loudnorm)."""
    cmd = [ffmpeg(), "-hide_banner", "-nostats", *args]
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def probe_duration(path: Path | str) -> float:
    cmd = [
        ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ToolError(f"ffprobe could not read {path}: {proc.stderr.strip()[-200:]}")
    return round(float(proc.stdout.strip()), 3)


def has_audio(path: Path | str) -> bool:
    cmd = [
        ffprobe(),
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(path),
    ]
    return bool(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())


# ---- audio helpers -----------------------------------------------------------------


def write_silence(out: Path, seconds: float, *, sample_rate: int, bitrate: str) -> None:
    run(
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={sample_rate}:cl=mono",
        "-t",
        f"{seconds:.3f}",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        str(out),
    )


def trailing_silence(path: Path, *, noise_db: int = -35, min_run: float = 0.05) -> float:
    """Seconds of silence at the end of an audio file."""
    duration = probe_duration(path)
    err = stderr("-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_run}", "-f", "null", "-")
    starts = re.findall(r"silence_start: ([0-9.]+)", err)
    ends = re.findall(r"silence_end: ([0-9.]+)", err)
    if not starts:
        return 0.0
    if len(ends) < len(starts) or float(ends[-1]) >= duration - 0.05:
        return round(duration - float(starts[-1]), 3)
    return 0.0


def pad_tail(path: Path, seconds: float, *, bitrate: str) -> None:
    tmp = path.with_suffix(".pad.mp3")
    run("-i", str(path), "-af", f"apad=pad_dur={seconds}", "-c:a", "libmp3lame", "-b:a", bitrate, str(tmp))
    tmp.replace(path)


def concat_audio(files: list[Path], out: Path, *, bitrate: str, sample_rate: int) -> None:
    inputs: list[str] = []
    for f in files:
        inputs += ["-i", str(f)]
    labels = "".join(f"[{i}:a]" for i in range(len(files)))
    run(
        *inputs,
        "-filter_complex",
        f"{labels}concat=n={len(files)}:v=0:a=1[a]",
        "-map",
        "[a]",
        "-c:a",
        "libmp3lame",
        "-b:a",
        bitrate,
        "-ar",
        str(sample_rate),
        str(out),
    )


def crossfade_join(parts: list[Path], out: Path, *, crossfade_seconds: float, bitrate: str) -> None:
    if len(parts) == 1:
        shutil.copyfile(parts[0], out)
        return
    inputs: list[str] = []
    for p in parts:
        inputs += ["-i", str(p)]
    chain, prev = "", "[0:a]"
    for i in range(1, len(parts)):
        label = "[a]" if i == len(parts) - 1 else f"[m{i}]"
        chain += f"{prev}[{i}:a]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri{label};"
        prev = label
    run(*inputs, "-filter_complex", chain.rstrip(";"), "-map", "[a]", "-c:a", "libmp3lame", "-b:a", bitrate, str(out))


# ---- frame analysis ------------------------------------------------------------------


@dataclass(frozen=True)
class FrameStats:
    pts: float
    yavg: float
    ymax: float
    uavg: float
    vavg: float


def frame_stats(path: Path, seconds: float) -> list[FrameStats]:
    """Per-frame signalstats for the first `seconds` of the file."""
    out = stderr("-t", str(seconds), "-i", str(path), "-vf", "signalstats,metadata=print", "-f", "null", "-")
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
    err = stderr("-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-vf", vf, "-f", "null", "-")
    yavg = re.search(r"YAVG=([0-9.]+)", err)
    ymax = re.search(r"YMAX=([0-9.]+)", err)
    return (float(yavg.group(1)) if yavg else 0.0, float(ymax.group(1)) if ymax else 0.0)


def changed_pixels_percent(path: Path, t1: float, t2: float, *, level: int, width: int, height: int) -> float:
    """Share (0-100) of pixels whose luma differs by more than `level` between the frames at t1 and t2."""
    fc = (
        f"[0:v]trim=start={t1:.3f}:duration=0.05,setpts=PTS-STARTPTS,scale={width}:{height}[a];"
        f"[1:v]trim=start={t2:.3f}:duration=0.05,setpts=PTS-STARTPTS,scale={width}:{height}[b];"
        f"[a][b]blend=all_mode=difference,lutyuv=y='if(gt(val,{level}),255,0)':u=128:v=128,signalstats,metadata=print"
    )
    err = stderr("-i", str(path), "-i", str(path), "-filter_complex", fc, "-frames:v", "1", "-f", "null", "-")
    m = re.search(r"YAVG=([0-9.]+)", err)
    return (float(m.group(1)) / 255 * 100) if m else 0.0


# ---- loudness --------------------------------------------------------------------------


@dataclass(frozen=True)
class Loudness:
    i: float
    tp: float
    lra: float
    thresh: float
    offset: float


def measure_loudness(path: Path, *, i: float, tp: float, lra: float) -> Loudness:
    err = stderr(
        "-i", str(path), "-map", "0:a", "-af", f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json", "-f", "null", "-"
    )

    def field(name: str) -> float:
        m = re.search(rf'"{name}"\s*:\s*"([-0-9.]+)"', err)
        return float(m.group(1)) if m else 0.0

    return Loudness(
        i=field("input_i"),
        tp=field("input_tp"),
        lra=field("input_lra"),
        thresh=field("input_thresh"),
        offset=field("target_offset"),
    )
