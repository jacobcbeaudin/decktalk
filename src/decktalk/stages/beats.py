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
    verify  false leaves the cue out of a plain `decktalk verify`, for a reveal too small
            or too slow for a frame difference to measure. The default is true.

Unresolved cues are reported and left out, and the recording still runs. A cue id that
appears nowhere in its page as a quoted literal is reported as unknown, because no step
would ever reveal it, and resolve_beats raises UnknownCueError unless allow_unknown is set.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import Beats, Word, read_words
from ..errors import ConfigError, MissingInputError
from ..project import PageSection, Project

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cue:
    step: str
    on: str
    occurrence: int = 1
    case_sensitive: bool = False
    offset: float = 0.0
    verify: bool = True  # False leaves the cue out of a plain `decktalk verify`.


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
        data = json.loads(project.cues.read_text(encoding="utf-8"))
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
            check = raw.get("verify", True)
            if not isinstance(check, bool):
                raise ConfigError(f"{where}: 'verify' must be true or false")
            cues.append(
                Cue(
                    step=step,
                    on=on,
                    occurrence=int(raw.get("occurrence", 1)),
                    case_sensitive=bool(raw.get("case_sensitive", False)),
                    offset=float(raw.get("offset", 0.0)),
                    verify=check,
                )
            )
        need = spec.get("min_seconds")
        out.append(SectionCues(number=number, cues=tuple(cues), min_seconds=None if need is None else float(need)))
    return sorted(out, key=lambda s: s.number)


def page_mentions(html: str, cue_id: str) -> bool:
    """Whether the page names the cue id as a quoted literal, as in data-cue="ID", a cues key, or a handler key."""
    return re.search(r"([\"'`])" + re.escape(cue_id) + r"\1", html) is not None


def unknown_cue_ids(project: Project, specs: list[SectionCues]) -> list[tuple[str, str, str]]:
    """(section key, cue id, page) for every cue id that its page never mentions.

    Clip sections have no page, and a page file that does not exist is reported by the
    recorder instead, so both are skipped.
    """
    pages: dict[str, str] = {}
    out: list[tuple[str, str, str]] = []
    for spec in specs:
        section = project.section(spec.number)
        if not isinstance(section, PageSection):
            continue
        if section.page not in pages:
            path = project.path(section.page)
            if not path.exists():
                continue
            pages[section.page] = path.read_text(encoding="utf-8")
        html = pages[section.page]
        out += [(section.key, cue.step, section.page) for cue in spec.cues if not page_mentions(html, cue.step)]
    return out


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
    path.write_text(json.dumps(anchors, indent=1) + "\n", encoding="utf-8")


def read_anchors(path: Path) -> dict[str, dict[str, float]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@dataclass(frozen=True)
class BeatNote:
    """One structured note. The verdict is UNRESOLVED or UNKNOWN, or None for a warning without a verdict code."""

    cue: str | None
    verdict: str | None
    detail: str

    @property
    def text(self) -> str:
        return f"{self.cue}: {self.detail}" if self.cue else self.detail


@dataclass
class SectionBeats:
    key: str
    speech_end: float
    min_seconds: float | None
    resolved: dict[str, float]
    notes: list[str] = field(default_factory=list)
    skipped: str | None = None  # why nothing was resolved (no narration)
    findings: list[BeatNote] = field(default_factory=list)  # The notes, structured, in the same order.

    def note(self, cue: str | None, verdict: str | None, detail: str) -> None:
        """Record a note both as the table's text line and as a structured finding."""
        finding = BeatNote(cue, verdict, detail)
        self.findings.append(finding)
        self.notes.append(finding.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "speech_end": round(self.speech_end, 3),
            "min_seconds": self.min_seconds,
            "skipped": self.skipped,
            "cues": dict(self.resolved),
            "notes": [{"cue": n.cue, "verdict": n.verdict, "detail": n.detail} for n in self.findings],
        }


@dataclass
class BeatsResult:
    beats: Beats
    sections: list[SectionBeats]
    unresolved: int
    estimated: bool
    unknown: int = 0  # Cue ids that appear nowhere in the page that plays them.
    beats_file: Path | None = None

    @property
    def problems(self) -> list[str]:
        return [f"section {s.key}: {n}" for s in self.sections for n in s.notes]

    @property
    def unknown_problems(self) -> list[str]:
        return [f"section {s.key}: {n.text}" for s in self.sections for n in s.findings if n.verdict == "UNKNOWN"]

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The result as JSON-ready data, with the beats file relative to the project root."""
        beats_file = None
        if self.beats_file is not None:
            beats_file = (
                self.beats_file.relative_to(root).as_posix()
                if self.beats_file.is_relative_to(root)
                else self.beats_file.as_posix()
            )
        return {
            "estimated": self.estimated,
            "beats_file": beats_file,
            "unresolved": self.unresolved,
            "unknown": self.unknown,
            "sections": [s.to_dict() for s in self.sections],
        }


def unknown_message(result: BeatsResult) -> str:
    """Why the build stops on unknown cue ids, with the first one as the example fix."""
    first = next(n for s in result.sections for n in s.findings if n.verdict == "UNKNOWN")
    page = first.detail.removeprefix("not in ")
    return (
        f"{result.unknown} cue id(s) in cues.json appear nowhere in the page that plays them, so the page would "
        f'never reveal them. Add data-cue="{first.cue}" to the step in {page}, fix the id in cues.json, or pass '
        "--allow-unknown:\n  " + "\n  ".join(result.unknown_problems)
    )


class UnknownCueError(ConfigError):
    """Cue ids that no page mentions. The result is attached, so a caller can still print its table."""

    def __init__(self, result: BeatsResult) -> None:
        super().__init__(unknown_message(result))
        self.result = result


def resolve_beats(project: Project, *, allow_unknown: bool = False) -> BeatsResult:
    """Resolve every cue phrase, write beats.json, and report the cue ids that their page never mentions.

    beats.json is written either way. Unknown cue ids then raise UnknownCueError, which
    carries the result, unless allow_unknown is set.
    """
    manifest = project.manifest()
    if manifest is None:
        raise MissingInputError(
            f"{project.manifest_path} not found. Run `decktalk narrate` (or `decktalk narrate --silent`) first."
        )
    specs = load_cues(project)
    if not specs:
        log.info("no cues file at %s; pages will run their built-in timing", project.cues)
    beats = Beats()
    anchors: dict[str, dict[str, float]] = {}
    rows: list[SectionBeats] = []
    unresolved = 0
    unknown_ids = unknown_cue_ids(project, specs)
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
            for k, cue_id, page in unknown_ids:
                if k == key:
                    rows[-1].note(cue_id, "UNKNOWN", f"not in {page}")
            continue
        words = read_words(project.audio_dir / entry.words_file)
        speech_end = words[-1].end if words else entry.duration_seconds
        row = SectionBeats(key=key, speech_end=speech_end, min_seconds=spec.min_seconds, resolved={})
        for cue in spec.cues:
            if not words and cue.on != "$start":
                row.note(
                    cue.step,
                    "UNRESOLVED",
                    f"no words ({'estimated manifest' if manifest.estimated else 'missing words file'})",
                )
                unresolved += 1
                continue
            t = resolve_cue(cue, words)
            if t is None:
                row.note(cue.step, "UNRESOLVED", f"phrase not found: {cue.on!r}")
                unresolved += 1
                continue
            if t > entry.duration_seconds:
                row.note(cue.step, None, f"{t}s is past the end of the audio ({entry.duration_seconds}s)")
            row.resolved[cue.step] = t
            anchor = anchor_time(cue, words)
            if anchor is not None:
                anchors.setdefault(key, {})[cue.step] = round(anchor, 2)
        if spec.min_seconds is not None and speech_end < spec.min_seconds:
            short = spec.min_seconds - speech_end
            row.note(None, None, f"speech {speech_end:.1f}s is {short:.1f}s shorter than the visuals need")
        for k, cue_id, page in unknown_ids:
            if k == key:
                row.note(cue_id, "UNKNOWN", f"not in {page}")
        if row.resolved:
            beats.sections[key] = row.resolved
        rows.append(row)
    beats.save(project.beats_path)
    write_anchors(project.beats_path.with_name("beats.anchors.json"), anchors)
    log.info(
        "wrote %s (%d sections with cues; %d unresolved, %d unknown)",
        project.beats_path,
        len(beats.sections),
        unresolved,
        len(unknown_ids),
    )
    result = BeatsResult(
        beats=beats,
        sections=rows,
        unresolved=unresolved,
        estimated=manifest.estimated,
        unknown=len(unknown_ids),
        beats_file=project.beats_path,
    )
    if result.unknown and not allow_unknown:
        raise UnknownCueError(result)
    return result
