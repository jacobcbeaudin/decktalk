"""Finding ffmpeg and ffprobe for this machine, running them, and probing what they read.

The environment override wins, the pinned build that `toolchain/ffmpeg_fetch.py` downloads comes
next, and a build on PATH is the fallback, so every machine renders with the same ffmpeg unless it
is told otherwise. The override is a pair: a machine that names one half and not the other is
refused rather than quietly rendered with a build it did not ask for.

Every call goes through `_checked`, so a return code other than zero raises `ToolError` carrying
the tail of what the tool said. A measurement that read a failure as silence, as blackness or as a
missing audio stream would turn a broken tool into a verdict about the film, which is the one
mistake this module exists to prevent.

`audio.py` and `frames.py` build on the five calls here: `run`, `stderr`, `raw`, `probe_duration`
and `has_audio`.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from ..errors import ToolError
from ..findings import Location
from ..toolchain import ffmpeg_fetch

log = logging.getLogger(__name__)

FFMPEG_VARIABLE = "DECKTALK_FFMPEG"
FFPROBE_VARIABLE = "DECKTALK_FFPROBE"
OVERRIDE = (FFMPEG_VARIABLE, FFPROBE_VARIABLE)
"""The two variables that name a build of the caller's own, which are set together or not at all."""

TAIL_LINES = 6
"""How many of a tool's last lines an error quotes, which is where it says what it could not do."""

HINT_CHARS = 200
"""How much of a probe's complaint a hint carries, because a hint is read beside the sentence."""


def _tail(err: bytes) -> str:
    """The last lines of what a tool wrote, as one line, which is where the reason for a failure is."""
    lines = err.decode(errors="replace").strip().splitlines()
    return " | ".join(line.strip() for line in lines[-TAIL_LINES:]) or "it said nothing"


def _checked(cmd: list[str], what: str, *, location: Location | None = None) -> subprocess.CompletedProcess[bytes]:
    """Run one tool call and hand back what it wrote, or raise `ToolError` carrying the tail of its complaint.

    Every invocation in this package comes through here, so no caller can read a failed run as a
    measurement. The output is captured as bytes, because a decoder writes samples to stdout and a
    filter writes its report to stderr in the same call shape.
    """
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise ToolError(f"{what} failed: {_tail(proc.stderr)}", location=location)
    return proc


def _at(path: Path | str) -> Location:
    """Where a file failure happened, which is the file the tool was given."""
    return Location(where=Path(path).name, file=Path(path))


def _env_paths() -> tuple[str, str] | None:
    """The pair the environment names, when both variables are set and both name a file that is there."""
    env_ff, env_fp = os.environ.get(FFMPEG_VARIABLE), os.environ.get(FFPROBE_VARIABLE)
    if not (env_ff and env_fp):
        return None
    return (env_ff, env_fp) if not env_missing() else None


def env_missing() -> list[str]:
    """The variables that name a file which is not there, so a typo is reported and never resolved.

    `doctor` reports this and `ffmpeg_paths` refuses on it, which is the difference between a command
    whose work is to report what a machine has and one that needs the tool to do anything at all.
    """
    return [name for name in OVERRIDE if (value := os.environ.get(name)) and not Path(value).is_file()]


def env_unpaired() -> list[str]:
    """The half of the override that is not set, when the other half is, which is never a usable pair.

    ffmpeg and ffprobe are one build, so naming one of them and leaving the other to PATH renders
    with two builds. The half that is missing is named rather than ignored, because a caller who set
    one variable meant to set both and would otherwise never learn that nothing happened.
    """
    set_names = [name for name in OVERRIDE if os.environ.get(name)]
    return [] if len(set_names) != 1 else [name for name in OVERRIDE if name not in set_names]


def _path_pair() -> tuple[str, str] | None:
    ff, fp = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return (ff, fp) if ff and fp else None


def _refuse_a_half_override() -> None:
    """Refuse an override that names one executable of the pair, naming the one it left out."""
    if unpaired := env_unpaired():
        raise ToolError(
            f"{', '.join(unpaired)} is not set, and ffmpeg and ffprobe have to come from one build.",
            hint=f"Set {' and '.join(OVERRIDE)} together, or unset both to use the pinned build.",
        )


@lru_cache(maxsize=1)
def ffmpeg_paths() -> tuple[str, str]:
    """(ffmpeg, ffprobe) executables.

    The environment override wins, and half an override is refused. The pinned build comes next,
    fetched when it is not on disk yet, so every machine renders with the same ffmpeg. A build on
    PATH is the fallback when there is no pinned build for this platform or the download cannot run.
    A download whose digest does not match is never used and never falls back, because that is the
    one failure that must stop a run.
    """
    _refuse_a_half_override()
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
            f"PATH. Install ffmpeg, or set {' and '.join(OVERRIDE)}."
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

    None means only a fetch could provide them, or that the override names half a build. `decktalk
    doctor` reports on that instead of triggering it.
    """
    if env_unpaired():
        return None
    return _env_paths() or ffmpeg_fetch.installed_pinned() or _path_pair()


def ffmpeg() -> str:
    return ffmpeg_paths()[0]


def ffprobe() -> str:
    return ffmpeg_paths()[1]


def run(*args: str) -> None:
    """ffmpeg -hide_banner -loglevel error -y ARGS, which writes a file and reports nothing."""
    _checked([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], "ffmpeg")


def stderr(*args: str) -> str:
    """ffmpeg run whose useful output is on stderr (metadata=print, silencedetect, loudnorm).

    A filter reports on stderr and exits zero, so a non-zero code here is the tool failing rather
    than the film measuring badly, and the caller is told so instead of reading an empty report.
    """
    proc = _checked([ffmpeg(), "-hide_banner", "-nostats", *args], "ffmpeg")
    return proc.stderr.decode(errors="replace")


def raw(*args: str) -> bytes:
    """ffmpeg run whose useful output is the bytes on stdout, such as decoded samples."""
    return _checked([ffmpeg(), "-v", "error", *args], "ffmpeg").stdout


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
    proc = _checked(cmd, f"ffprobe on {Path(path).name}", location=_at(path))
    text = proc.stdout.decode(errors="replace").strip()
    if not text:
        raise ToolError(
            f"ffprobe could not read the length of {Path(path).name}.",
            hint=proc.stderr.decode(errors="replace").strip()[-HINT_CHARS:] or None,
            location=_at(path),
        )
    return round(float(text), 3)


def has_audio(path: Path | str) -> bool:
    """Whether the file carries an audio stream at all.

    A failed probe is a tool failure and never an answer, because reading it as no audio would let a
    broken ffprobe publish a verdict about a film it never opened.
    """
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
    proc = _checked(cmd, f"ffprobe on {Path(path).name}", location=_at(path))
    return bool(proc.stdout.decode(errors="replace").strip())
