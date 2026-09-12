"""External tools: ffmpeg/ffprobe resolution and small subprocess helpers.

ffmpeg and ffprobe come from PATH when present; otherwise the static-ffmpeg package
fetches platform binaries on first use (`cuecut setup` does this ahead of time).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def ffmpeg_paths() -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables. Env CUECUT_FFMPEG / CUECUT_FFPROBE override."""
    env_ff, env_fp = os.environ.get("CUECUT_FFMPEG"), os.environ.get("CUECUT_FFPROBE")
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
        sys.exit(
            "error: ffmpeg/ffprobe not found on PATH and static-ffmpeg could not provide them "
            f"({exc}). Run `cuecut setup` with network access, or install ffmpeg."
        )


def ffmpeg() -> str:
    return ffmpeg_paths()[0]


def ffprobe() -> str:
    return ffmpeg_paths()[1]


def run(cmd: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def ff(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """ffmpeg -hide_banner -loglevel error -y ARGS (stdout/stderr captured)."""
    return run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], capture=True, check=check)


def ff_stderr(*args: str) -> str:
    """ffmpeg run whose useful output is on stderr (filters with metadata=print, loudnorm)."""
    return run([ffmpeg(), "-hide_banner", "-nostats", *args], capture=True, check=False).stderr


def ffprobe_duration(path: Path | str) -> float:
    result = run(
        [
            ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture=True,
    )
    return round(float(result.stdout.strip()), 3)


def has_audio(path: Path | str) -> bool:
    result = run(
        [
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
        ],
        capture=True,
    )
    return bool(result.stdout.strip())


def fmt_mmss(seconds: float | None) -> str:
    if seconds is None:
        return "  --  "
    return f"{int(seconds // 60)}:{int(round(seconds % 60)):02d}"


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def warn(msg: str) -> None:
    print(f"warn: {msg}", file=sys.stderr)


def die(msg: str) -> None:
    sys.exit(f"error: {msg}")
