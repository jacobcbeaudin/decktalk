"""A narrated section whose picture an external command draws, which is the second source of a section's picture.

A page section's picture is the page, stepped one frame at a time. A section that names a
`render_command` is narrated exactly as a page section is, so the take decides its length, `cue`
resolves its cues from its words and the narration runs through it without a pause. What draws its
picture is a command instead: `record` writes the section's motion document, which is the frame rate,
the size, the length, every spoken word and every cue's second, and runs the command once to write
the video. Remotion, a three.js page under a frame stepper of its own, or a script that composes an
ffmpeg filter graph all fit, because the contract is one JSON file in and one video out.

A command may also write the landings of what it drew, in the shape a page reports its lanes, and
verify then masks and measures them as it does a page's. Without them verify cannot measure the
section's cues, and it says so on the stream rather than passing them.

The words a command is given are replaced before it runs:

    {motion}    the motion document it reads
    {out}       the video it writes
    {landings}  where it may write its landings
    {python}    the Python this run is
    {ffmpeg}    the ffmpeg this run encodes with
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from decktalk.errors import InputError, ToolError
from decktalk.media import ffmpeg
from decktalk.media.browser import UNTRUSTED
from decktalk.media.environment import child_environment
from decktalk.media.pagereport import MotionLane
from decktalk.results import Word
from decktalk.toolchain import tail, traced

log = logging.getLogger(__name__)

MOTION_FILE = "motion.json"
"""What the motion document is called in a section's own render directory."""

LANDINGS_FILE = "landings.json"
"""Where a command may write the landings of what it drew, in the same directory."""

LANES = TypeAdapter(tuple[MotionLane, ...])
"""The landings file read as a page's lanes are, so verify reads one shape whoever drew the picture."""


@dataclass(frozen=True)
class Rendered:
    """What one run of a section's command left: how long it took and the lanes it said it drew."""

    seconds: float
    motions: tuple[MotionLane, ...]


def admitted(policy: str, *, number: int) -> None:
    """Refuse a section's command on a host that does not trust the project, which names the program it runs."""
    if policy == UNTRUSTED:
        raise InputError(
            f"section {number} names a render_command, and this machine treats every page as untrusted, so it "
            "runs no program a project names.",
            hint="Render the section on a machine that trusts the project, or make it a page section.",
        )


def motion_document(
    *, fps: int, width: int, height: int, seconds: float, words: Sequence[Word], cues: Mapping[str, float]
) -> dict[str, object]:
    """What a section's command reads: the frame, the length, every spoken word and every cue's second.

    Every second is on the section's own clock, which is the clock the take's words and the cue
    seconds are already on, so a renderer keys a keyframe on a cue as `round(cue * fps)` frames.
    """
    return {
        "fps": fps,
        "width": width,
        "height": height,
        "seconds": round(seconds, 3),
        "frames": round(seconds * fps),
        "words": [{"word": word.word, "start": word.start, "end": word.end} for word in words],
        "cues": dict(cues),
    }


def filled(command: Sequence[str], words: Mapping[str, Callable[[], str]]) -> list[str]:
    """The command with every placeholder it names replaced, each path worked out only when it is named."""
    out: list[str] = []
    for word in command:
        for name, value in words.items():
            mark = f"{{{name}}}"
            if mark in word:
                word = word.replace(mark, value())
        out.append(word)
    return out


def run_command(
    command: Sequence[str],
    *,
    root: Path,
    work: Path,
    out: Path,
    document: Mapping[str, object],
    timeout: float,
    label: str,
) -> Rendered:
    """Write the motion document, run the command from the project's root, and read what it left.

    The command runs with the scrubbed environment every tool gets, never the process's own, so a
    credential a host left in its environment does not reach it. A command that fails, outlives the
    machine's tool limit or writes no video is a `ToolError` naming the section.
    """
    work.mkdir(parents=True, exist_ok=True)
    motion = work / MOTION_FILE
    landings = work / LANDINGS_FILE
    motion.write_text(json.dumps(document, indent=1), encoding="utf-8")
    landings.unlink(missing_ok=True)
    out.unlink(missing_ok=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    words: dict[str, Callable[[], str]] = {
        "motion": lambda: str(motion),
        "out": lambda: str(out),
        "landings": lambda: str(landings),
        "python": lambda: sys.executable,
        "ffmpeg": ffmpeg.ffmpeg,
    }
    began = time.monotonic()
    try:
        done = subprocess.run(
            filled(command, words), cwd=root, env=child_environment(), capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise ToolError(
            f"the render_command of {label} ran past the {timeout:g} seconds tools.timeout_seconds allows."
        ) from None
    except OSError as cannot:
        raise ToolError(f"the render_command of {label} could not start ({cannot}).") from None
    argv = filled(command, words)
    traced(
        log,
        f"the render_command of {label}",
        argv,
        code=done.returncode,
        seconds=time.monotonic() - began,
        said=done.stderr,
    )
    if done.returncode != 0:
        raise ToolError(f"the render_command of {label} failed with exit {done.returncode}: {tail(done.stderr)}")
    if not out.is_file():
        raise ToolError(f"the render_command of {label} wrote no video at {out.name}.")
    return Rendered(seconds=round(time.monotonic() - began, 3), motions=read_landings(landings, label))


def read_landings(path: Path, label: str) -> tuple[MotionLane, ...]:
    """The lanes a command said it drew, or none when it wrote none, refused when it wrote something else."""
    if not path.is_file():
        return ()
    try:
        return LANES.validate_json(path.read_bytes())
    except ValidationError as unread:
        raise ToolError(
            f"the render_command of {label} wrote {path.name} in a shape no lane has: {unread.error_count()} problems."
        ) from None


__all__ = [
    "LANDINGS_FILE",
    "LANES",
    "MOTION_FILE",
    "Rendered",
    "admitted",
    "filled",
    "motion_document",
    "read_landings",
    "run_command",
]
