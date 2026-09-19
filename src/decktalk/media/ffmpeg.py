"""Finding ffmpeg and ffprobe for this machine, running them, and probing what they read.

The environment override wins, the pinned build that `toolchain/ffmpeg_fetch.py` downloads comes
next, and a build on PATH is the fallback, so every machine renders with the same ffmpeg unless it
is told otherwise. Every invocation checks the return code, so a failed render says what ffmpeg
said rather than leaving an empty file behind.

`audio.py` and `frames.py` build on the four calls here: `run`, `stderr`, `probe_duration` and
`decoded_duration`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ..errors import ToolError
from ..toolchain import ffmpeg_fetch

log = logging.getLogger(__name__)


def _env_paths() -> tuple[str, str] | None:
    """The pair the environment names, when both variables are set and both name a file that is there."""
    env_ff, env_fp = os.environ.get("DECKTALK_FFMPEG"), os.environ.get("DECKTALK_FFPROBE")
    if not (env_ff and env_fp):
        return None
    return (env_ff, env_fp) if not env_missing() else None


def env_missing() -> list[str]:
    """The variables that name a file which is not there, so a typo is reported and never resolved.

    `doctor` reports this and `ffmpeg_paths` refuses on it, which is the difference between a command
    whose work is to report what a machine has and one that needs the tool to do anything at all.
    """
    named = ("DECKTALK_FFMPEG", "DECKTALK_FFPROBE")
    return [name for name in named if (value := os.environ.get(name)) and not Path(value).is_file()]


def _path_pair() -> tuple[str, str] | None:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (ff, fp) if ff and fp else None


@lru_cache(maxsize=1)
def ffmpeg_paths() -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables.

    The environment override wins. The pinned build comes next, fetched when it is not on disk yet,
    so every machine renders with the same ffmpeg. A build on PATH is the fallback when there is no
    pinned build for this platform or the download cannot run. A download whose digest does not
    match is never used and never falls back, because that is the one failure that must stop a run.
    """
    if missing := env_missing():
        raise ToolError(
            f"{', '.join(missing)} names a file that is not there.",
            hint="Point the variable at an executable, or unset it to use the pinned build.",
        )
    if env := _env_paths():
        return env
    if installed := ffmpeg_fetch.installed_pinned():
        return installed
    on_path = _path_pair()
    if ffmpeg_fetch.pinned_build() is None:
        if on_path:
            return on_path
        raise ToolError(
            f"ffmpeg/ffprobe not found: DeckTalk pins no build for {ffmpeg_fetch.platform_key()} and none is on "
            "PATH. Install ffmpeg, or set DECKTALK_FFMPEG and DECKTALK_FFPROBE."
        )
    try:
        return ffmpeg_fetch.fetch_ffmpeg()
    except OSError as exc:
        if on_path:
            log.warning("could not download the pinned ffmpeg (%s), so %s is used instead", exc, on_path[0])
            return on_path
        raise ToolError(
            f"ffmpeg/ffprobe not found: the pinned build could not be downloaded ({exc}) and none is on PATH. "
            "Run `decktalk install` with network access, or install ffmpeg."
        ) from exc


def unnamed_paths() -> tuple[str, str] | None:
    """The pair a machine has without the environment override, which is what `doctor` falls back to.

    A variable that names a file which is not there tells nothing about the other component, so the
    row for the component that is fine still reports the build it would really use.
    """
    return ffmpeg_fetch.installed_pinned() or _path_pair()


def installed_paths() -> tuple[str, str] | None:
    """The (ffmpeg, ffprobe) pair that ffmpeg_paths() would return without downloading anything.

    None means only a fetch could provide them. `decktalk doctor` reports on that instead of
    triggering it.
    """
    return _env_paths() or ffmpeg_fetch.installed_pinned() or _path_pair()


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
    """The length the container reports, in seconds."""
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
        raise ToolError(
            f"ffprobe could not read {Path(path).name}.",
            hint=proc.stderr.strip()[-200:] or None,
            path=Path(path),
        )
    return round(float(proc.stdout.strip()), 3)


def decoded_duration(path: Path | str, *, sample_rate: int = 48000) -> float:
    """The length of the audio as it decodes, in seconds.

    A container's reported duration can include encoder padding that the decoder trims,
    which for MP3 is 30 to 50 ms per file. Positions in a concatenated track add up from
    decoded lengths, so anything that maps section times onto that track must use these.
    """
    out = subprocess.run(
        [ffmpeg(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True,
    )
    if out.returncode != 0:
        raise ToolError(
            f"ffmpeg could not decode {Path(path).name}.",
            hint=out.stderr.decode(errors="replace").strip()[-200:] or None,
            path=Path(path),
        )
    return round(len(out.stdout) / 2 / sample_rate, 4)


def has_audio(path: Path | str) -> bool:
    """Whether the file carries an audio stream at all."""
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
