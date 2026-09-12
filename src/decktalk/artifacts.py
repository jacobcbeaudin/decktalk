"""Typed build artifacts and their JSON files under build/.

These file shapes are part of the public contract: the page runtime and users' own
scripts read them. Field names match the JSON keys.

    build/audio/manifest.json     Manifest: one entry per narrated section
    build/audio/NN-slug.words.json  list[Word]
    build/audio/timeline.json     Timeline: absolute section and word times in narration.mp3
    build/audio/beats.json        Beats: {"NN": "cue@seconds,..."} relative to the section start
    build/rec/NN-scene.json       Sidecar: what the recorder did and where t=0 landed
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Write atomically: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=indent) + "\n")
    tmp.replace(path)


@dataclass(frozen=True)
class Word:
    word: str
    start: float
    end: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(word=str(d["word"]), start=float(d["start"]), end=float(d["end"]))


def read_words(path: Path) -> list[Word]:
    if not path.exists():
        return []
    return [Word.from_dict(w) for w in _read_json(path)]


def write_words(path: Path, words: list[Word]) -> None:
    _write_json(path, [asdict(w) for w in words], indent=1)


@dataclass
class ManifestSegment:
    index: int
    title: str
    file: str
    words_file: str
    hash: str
    words: int
    est_seconds: float
    duration_seconds: float
    target_seconds: float | None = None
    speech_end_seconds: float | None = None
    tail_padded_seconds: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Manifest:
    script: str
    model: str
    output_format: str
    estimated: bool = False
    estimate_basis: str = ""
    segments: dict[str, ManifestSegment] = field(default_factory=dict)
    total_seconds: float = 0.0

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = _read_json(path)
        segs = {k: ManifestSegment.from_dict(v) for k, v in d.get("segments", {}).items()}
        return cls(
            script=str(d.get("script", "")),
            model=str(d.get("model", "")),
            output_format=str(d.get("output_format", "")),
            estimated=bool(d.get("estimated", False)),
            estimate_basis=str(d.get("estimate_basis", "")),
            segments=dict(sorted(segs.items())),
            total_seconds=float(d.get("total_seconds", 0.0)),
        )

    def save(self, path: Path) -> None:
        self.segments = dict(sorted(self.segments.items()))
        self.total_seconds = round(sum(s.duration_seconds for s in self.segments.values()), 3)
        _write_json(path, asdict(self))


@dataclass
class TimelineSection:
    title: str
    start: float
    end: float
    duration: float
    speech_end: float | None
    words: list[Word] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            title=str(d["title"]),
            start=float(d["start"]),
            end=float(d["end"]),
            duration=float(d["duration"]),
            speech_end=None if d.get("speech_end") is None else float(d["speech_end"]),
            words=[Word.from_dict(w) for w in d.get("words", [])],
        )


@dataclass
class Timeline:
    """Section and word times, absolute in narration.mp3."""

    narration: str
    total_seconds: float
    sections: dict[str, TimelineSection]
    estimated: bool = False

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = _read_json(path)
        return cls(
            narration=str(d.get("narration", "narration.mp3")),
            total_seconds=float(d.get("total_seconds", 0.0)),
            estimated=bool(d.get("estimated", False)),
            sections={k: TimelineSection.from_dict(v) for k, v in d.get("sections", {}).items()},
        )

    def save(self, path: Path) -> None:
        _write_json(path, asdict(self), indent=1)

    def span(self, key: str) -> float | None:
        sec = self.sections.get(key)
        return None if sec is None else sec.end - sec.start

    @property
    def keys(self) -> list[str]:
        return sorted(self.sections)


@dataclass
class Beats:
    """Resolved cue times per section, seconds relative to the section's start."""

    sections: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Self:
        if not path.exists():
            return cls()
        return cls({k: parse_beats_string(v) for k, v in _read_json(path).items()})

    def save(self, path: Path) -> None:
        _write_json(path, {k: self.query(k) for k in sorted(self.sections) if self.sections[k]})

    def query(self, key: str) -> str | None:
        """The ?beats= value for a section, or None when it has no resolved cues."""
        cues = self.sections.get(key)
        if not cues:
            return None
        return ",".join(f"{cue}@{t}" for cue, t in cues.items())

    def get(self, key: str, cue: str) -> float | None:
        return self.sections.get(key, {}).get(cue)


def parse_beats_string(value: str) -> dict[str, float]:
    """'a@1.5,b@2' -> {'a': 1.5, 'b': 2.0}; malformed items are skipped."""
    out: dict[str, float] = {}
    for item in value.split(","):
        item = item.strip()
        if not item or "@" not in item:
            continue
        cue, _, t = item.rpartition("@")
        try:
            out[cue] = float(t)
        except ValueError:
            continue
    return out


@dataclass
class Sidecar:
    """What the recorder did for one section, and where narration t=0 sits in the webm."""

    url: str
    requested_seconds: float
    settle_seconds: float
    load_seconds: float
    lead_seconds: float  # wall-clock estimate from the recorder
    lead_in_seconds: float | None = None  # first clean frame after the magenta cover: narration t=0
    lead_method: str | None = None

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = _read_json(path)
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        _write_json(path, asdict(self))

    @property
    def trim_seconds(self) -> float:
        return self.lead_in_seconds if self.lead_in_seconds is not None else self.lead_seconds
