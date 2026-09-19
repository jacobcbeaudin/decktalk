"""`RecordingLog`, what the recorder did for one section and where narration t=0 sits in its webm.

    build/recordings/NN.json   one log per recorded section, written once by the recorder

The log is the recorder's own account of the run: what it opened, how long it asked for, every
page error and frame gap it saw, and what each cue and each spoken reveal did. `measure` fills in
where narration t=0 landed, and the assembler trims that much off the head of the recording.

The measurement is tied to the recording by `t0_hash`, the digest of the webm it was read from, so
a recording made again after a measurement is caught rather than cut at the wrong place.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

from ..jsonio import read_json, relative, write_json


def gap_time(value: Any) -> float | None:
    """A frame gap's time in seconds, or None when the gap ended before the narration clock started.

    The page reports such a gap at negative infinity, so any value that is not a finite number becomes None.
    """
    if value is None:
        return None
    at = float(value)
    return at if math.isfinite(at) else None


@dataclass
class RecordingLog:
    """What the recorder did for one section, and where narration t=0 sits in the webm."""

    url: str
    requested_seconds: float
    settle_seconds: float
    load_seconds: float
    clock_start_seconds: float  # wall-clock seconds from the recorder's start to t=0, the recorder's own estimate
    t0_seconds: float | None = None  # first clean frame after the magenta cover: narration t=0
    t0_method: str | None = None
    t0_hash: str | None = None  # the sha256 prefix of the webm that measure read, so a stale measure shows
    warnings: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)  # uncaught exceptions, or no runtime catalog at all
    # (seconds, ms) where the page stalled. The time is None for a gap that ended before narration t=0.
    frame_gaps: list[tuple[float | None, int]] = field(default_factory=list)
    spoken_log: list[dict[str, Any]] = field(default_factory=list)  # what each data-text="spoken" element matched
    # Each cue as the page ran it: id, due, ran, and the start of its frame and the two after (seconds).
    cue_log: list[dict[str, Any]] = field(default_factory=list)
    # Animation frames over 50 ms after t=0: start, ms, render, and presented (seconds, ms for the length).
    long_frames: list[dict[str, Any]] = field(default_factory=list)

    @property
    def worst_stall_ms(self) -> int:
        """The longest stall a viewer can see, counting only the part of each gap after narration t=0.

        Frames before t=0 sit under the cover and are trimmed from the cut, so a scene may warm
        up there. A gap is recorded when it ends, so a gap that began before t=0 counts only its
        milliseconds after t=0, and a gap that ended before t=0 counts nothing. Such a gap has no
        time on the narration clock, so its time is None.
        """
        visible = (0 if at is None else min(ms, at * 1000) for at, ms in self.frame_gaps)
        return int(max((v for v in visible if v > 0), default=0))

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = read_json(path)
        recording_log = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        recording_log.frame_gaps = [(at, int(ms)) for at, ms in recording_log.frame_gaps]
        return recording_log

    def save(self, path: Path) -> None:
        """Write standard JSON, with null for a gap time that is not a finite number."""
        d = asdict(self)
        d["frame_gaps"] = [[gap_time(at), ms] for at, ms in self.frame_gaps]
        write_json(path, d)

    @property
    def trim_seconds(self) -> float:
        return self.t0_seconds if self.t0_seconds is not None else self.clock_start_seconds


def recording_hash(path: Path) -> str:
    """The first 16 hex digits of the file's sha256, which ties a measurement to one recording."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def stale_measure(webm: Path, recording_log: RecordingLog | None, root: Path | None = None) -> str | None:
    """Why the recording log's narration t=0 does not belong to this recording, or None when it does.

    `record` writes a recording log with no measurement, and `measure` fills it in with the hash of
    the webm it read.
    """
    name = relative(webm, root) if root is not None else webm.name
    if recording_log is None:
        return f"{name} has no recording log, so `measure` never found its narration t=0"
    if recording_log.t0_seconds is None:
        return (
            f"{name} was never measured, so the cut would trim the recorder's wall-clock estimate "
            f"of {recording_log.clock_start_seconds:g}s"
        )
    if recording_log.t0_hash != recording_hash(webm):
        return f"{name} changed after `measure` read it"
    return None
