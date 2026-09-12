"""Stage 2: cue phrases become narration timestamps (cues.json + words -> build/audio/beats.json).

cues.json:
    {"sections": {"3": {"min_seconds": 25,
                        "cues": [{"step": "3.2", "on": "On a typical"},
                                 {"step": "3.x", "on": "Zero", "occurrence": 2, "case_sensitive": true},
                                 {"step": "15.2", "on": "$end", "offset": 0.3}]}}}

    step    a cue id the page understands (decktalk-runtime.js); "cue" is accepted as a synonym
    on      a word or short phrase from that section's narration: first occurrence,
            case-insensitive, punctuation ignored. "$start" = 0, "$end" = end of speech.
    occurrence / case_sensitive / offset (seconds) refine the match.

Unresolved cues are reported and left out; the recording still runs.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..artifacts import Beats, Word, read_words
from ..errors import ConfigError, MissingInputError
from ..project import Project

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cue:
    step: str
    on: str
    occurrence: int = 1
    case_sensitive: bool = False
    offset: float = 0.0


@dataclass(frozen=True)
class SectionCues:
    number: int
    cues: tuple[Cue, ...]
    min_seconds: float | None = None


def load_cues(project: Project) -> list[SectionCues]:
    """Parsed and validated cues.json; [] when the file does not exist."""
    if not project.cues.exists():
        return []
    try:
        data = json.loads(project.cues.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{project.cues}: {exc}") from exc
    sections_raw = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections_raw, dict):
        raise ConfigError(f"{project.cues}: expected a top-level 'sections' object")
    known = {s.number for s in project.sections}
    out: list[SectionCues] = []
    for num_raw, spec in sections_raw.items():
        try:
            number = int(num_raw)
        except ValueError as exc:
            raise ConfigError(f"{project.cues}: section key {num_raw!r} is not a number") from exc
        if number not in known:
            raise ConfigError(f"{project.cues}: section {number} is not in decktalk.toml")
        if not isinstance(spec, dict):
            raise ConfigError(f"{project.cues}: section {number} must be an object")
        cues: list[Cue] = []
        for i, raw in enumerate(spec.get("cues", [])):
            where = f"{project.cues}: section {number}, cue #{i + 1}"
            if not isinstance(raw, dict):
                raise ConfigError(f"{where}: must be an object")
            step = raw.get("step", raw.get("cue"))
            on = raw.get("on")
            if not isinstance(step, str) or not step:
                raise ConfigError(f"{where}: needs a non-empty 'step' (the cue id)")
            if not isinstance(on, str) or not on:
                raise ConfigError(f"{where}: needs a non-empty 'on' (a spoken phrase, $start or $end)")
            cues.append(
                Cue(
                    step=step,
                    on=on,
                    occurrence=int(raw.get("occurrence", 1)),
                    case_sensitive=bool(raw.get("case_sensitive", False)),
                    offset=float(raw.get("offset", 0.0)),
                )
            )
        need = spec.get("min_seconds")
        out.append(SectionCues(number=number, cues=tuple(cues), min_seconds=None if need is None else float(need)))
    return sorted(out, key=lambda s: s.number)


def norm(token: str, case_sensitive: bool = False) -> str:
    token = re.sub(r"[^0-9A-Za-z']", "", token)
    return token if case_sensitive else token.lower()


def find_phrase(words: list[Word], phrase: str, occurrence: int = 1, case_sensitive: bool = False) -> int | None:
    """Index of the first word of the n-th occurrence of phrase, or None."""
    target = [t for t in (norm(t, case_sensitive) for t in phrase.split()) if t]
    if not target:
        return None
    normalized = [norm(w.word, case_sensitive) for w in words]
    seen = 0
    for i in range(len(normalized) - len(target) + 1):
        if normalized[i : i + len(target)] == target:
            seen += 1
            if seen == occurrence:
                return i
    return None


def resolve_cue(cue: Cue, words: list[Word]) -> float | None:
    anchor = anchor_time(cue, words)
    return None if anchor is None else round(anchor + cue.offset, 2)


def anchor_time(cue: Cue, words: list[Word]) -> float | None:
    """The moment the cue is anchored to, before its offset: the matched word's start, or an edge."""
    if cue.on == "$start":
        return 0.0
    if cue.on == "$end":
        return words[-1].end if words else None
    idx = find_phrase(words, cue.on, cue.occurrence, cue.case_sensitive)
    return None if idx is None else words[idx].start


def write_anchors(path: Path, anchors: dict[str, dict[str, float]]) -> None:
    """Where each cue's word starts, without the cue's offset. verify uses it to find the word's click."""
    import json as _json

    path.write_text(_json.dumps(anchors, indent=1) + "\n")


def read_anchors(path: Path) -> dict[str, dict[str, float]]:
    import json as _json

    return _json.loads(path.read_text()) if path.exists() else {}


@dataclass
class SectionBeats:
    key: str
    speech_end: float
    min_seconds: float | None
    resolved: dict[str, float]
    notes: list[str] = field(default_factory=list)
    skipped: str | None = None  # why nothing was resolved (no narration)


@dataclass
class BeatsResult:
    beats: Beats
    sections: list[SectionBeats]
    unresolved: int
    estimated: bool

    @property
    def problems(self) -> list[str]:
        return [f"section {s.key}: {n}" for s in self.sections for n in s.notes]


def resolve_beats(project: Project) -> BeatsResult:
    manifest = project.manifest()
    if manifest is None:
        raise MissingInputError(f"{project.manifest_path} not found; run `decktalk narrate` first")
    specs = load_cues(project)
    if not specs:
        log.info("no cues file at %s; pages will run their built-in timing", project.cues)
    beats = Beats()
    anchors: dict[str, dict[str, float]] = {}
    rows: list[SectionBeats] = []
    unresolved = 0
    for spec in specs:
        key = f"{spec.number:02d}"
        entry = manifest.segments.get(key)
        if entry is None:
            rows.append(
                SectionBeats(
                    key=key,
                    speech_end=0.0,
                    min_seconds=spec.min_seconds,
                    resolved={},
                    skipped="no narration (clip section, or not rendered)",
                )
            )
            continue
        words = read_words(project.audio_dir / entry.words_file)
        speech_end = words[-1].end if words else entry.duration_seconds
        row = SectionBeats(key=key, speech_end=speech_end, min_seconds=spec.min_seconds, resolved={})
        for cue in spec.cues:
            if not words and cue.on != "$start":
                row.notes.append(
                    f"{cue.step}: no words ({'estimated manifest' if manifest.estimated else 'missing words file'})"
                )
                unresolved += 1
                continue
            t = resolve_cue(cue, words)
            if t is None:
                row.notes.append(f"{cue.step}: phrase not found: {cue.on!r}")
                unresolved += 1
                continue
            if t > entry.duration_seconds:
                row.notes.append(f"{cue.step}: {t}s is past the end of the audio ({entry.duration_seconds}s)")
            row.resolved[cue.step] = t
            anchor = anchor_time(cue, words)
            if anchor is not None:
                anchors.setdefault(key, {})[cue.step] = round(anchor, 2)
        if spec.min_seconds is not None and speech_end < spec.min_seconds:
            row.notes.append(
                f"speech {speech_end:.1f}s is {spec.min_seconds - speech_end:.1f}s shorter than the visuals need"
            )
        if row.resolved:
            beats.sections[key] = row.resolved
        rows.append(row)
    beats.save(project.beats_path)
    write_anchors(project.beats_path.with_name("beats.anchors.json"), anchors)
    log.info("wrote %s (%d sections with cues; %d unresolved)", project.beats_path, len(beats.sections), unresolved)
    return BeatsResult(beats=beats, sections=rows, unresolved=unresolved, estimated=manifest.estimated)
