"""`RecordingLog`, everything `record` did for one section and everything it judged about the result.

    build/recordings/NN.json   one log per recorded section, written when that section is finished

The log is the recorder's whole account of one section: what it opened, which project files the page
loaded, how long it asked for, every page error and frame gap it saw, what each cue and each spoken
reveal did, where narration t=0 landed in the webm, and the checks against the frames. One command
writes all of it, so the measurement always belongs to the recording beside it and a reader of a
long run sees each section's log as soon as that section is done.

`input_hash` is what the section was recorded from: the page URL with its cues and words, the frame
geometry, the markup of the one scene the section plays, the rest of its page, which every scene
shares, and the content of every file the page loaded. A section whose hash is unchanged would be
recorded again for nothing, so `record` skips it, and the same hash is how a reader of the project
tells a recording that is still good from one that is stale.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self

from ..jsonio import read_json, write_json
from ..verdicts import Verdict

HASH_DIGITS = 16  # Enough of a sha256 that two different recordings never collide in one project.
GONE = "gone"  # What a file the page asked for and the project no longer has hashes to.


def file_digest(path: Path) -> str:
    """The first hex digits of a file's sha256, or `gone` when the project no longer has it."""
    if not path.is_file():
        return GONE
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:HASH_DIGITS]


def text_digest(text: str) -> str:
    """The first hex digits of a string's sha256, which is how a slice of a page joins a key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_DIGITS]


def input_hash(parts: Sequence[str], files: Mapping[str, Path]) -> str:
    """The digest of what a section is recorded from: these strings, and the content of these files.

    `files` maps each project-relative name to the file on disk, so a page that swaps one picture for
    another moves the hash although no line of HTML changed. The names are sorted, so the order the
    page happened to ask for them in is not part of the key.
    """
    lines = [*parts, *(f"{name}:{file_digest(files[name])}" for name in sorted(files))]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:HASH_DIGITS]


def gap_time(value: Any) -> float | None:
    """A frame gap's time in seconds, or None when the gap ended before the narration clock started.

    The page reports such a gap at negative infinity, so any value that is not a finite number becomes None.
    """
    if value is None:
        return None
    at = float(value)
    return at if math.isfinite(at) else None


@dataclass(frozen=True)
class Luma:
    """How bright a recording is at a tenth, a half and nine tenths of its length."""

    y10: float
    y50: float
    y90: float
    max50: float  # The brightest pixel at the half-way frame, which a dark slide still has.


@dataclass(frozen=True)
class RecordingChecks:
    """What the frames of one recording were judged on, and every verdict against them."""

    duration_seconds: float
    wanted_seconds: float
    luma: Luma
    verdicts: tuple[Verdict, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.verdicts

    def to_json(self) -> dict[str, Any]:
        """The checks as the log stores them, with each verdict as the object every payload carries."""
        return {
            "duration_seconds": round(self.duration_seconds, 3),
            "wanted_seconds": round(self.wanted_seconds, 3),
            "luma": {k: round(v, 2) for k, v in asdict(self.luma).items()},
            "verdicts": [v.to_dict() for v in self.verdicts],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            duration_seconds=float(data["duration_seconds"]),
            wanted_seconds=float(data["wanted_seconds"]),
            luma=Luma(**{k: float(v) for k, v in data["luma"].items()}),
            verdicts=tuple(Verdict.from_dict(v) for v in data.get("verdicts", ())),
        )


@dataclass
class RecordingLog:
    """What `record` did for one section, where narration t=0 sits in the webm, and how it checked out."""

    url: str
    requested_seconds: float
    settle_seconds: float
    load_seconds: float
    clock_start_seconds: float  # wall-clock seconds from the recorder's start to t=0, the recorder's own estimate
    assets: list[str] = field(default_factory=list)  # every project file the page loaded, project-relative
    external: list[str] = field(default_factory=list)  # every other origin the page reached for while recording
    input_hash: str = ""  # the page, its assets, its words and its cues, which is what a skip is keyed on
    t0_seconds: float | None = None  # first clean frame after the magenta cover: narration t=0
    t0_method: str | None = None  # one sentence naming how t=0 was found, for a reader of the log
    t0_guessed: bool = False  # no cover was found, so t=0 is an estimate and every reveal in the section moves
    checks: RecordingChecks | None = None  # the frame checks, written with the rest of the log
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
        if isinstance(recording_log.checks, dict):
            recording_log.checks = RecordingChecks.from_json(recording_log.checks)
        return recording_log

    def save(self, path: Path) -> None:
        """Write standard JSON, with null for a gap time that is not a finite number."""
        d = asdict(self)
        d["frame_gaps"] = [[gap_time(at), ms] for at, ms in self.frame_gaps]
        d["checks"] = None if self.checks is None else self.checks.to_json()
        write_json(path, d)

    @property
    def trim_seconds(self) -> float:
        """Where the assembler cuts the head off this recording, which is narration t=0 in the webm."""
        return self.t0_seconds if self.t0_seconds is not None else self.clock_start_seconds
