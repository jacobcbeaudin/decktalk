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
    warnings: list[str] = field(default_factory=list)
    frame_gaps: list[tuple[float, int]] = field(default_factory=list)  # (seconds, ms) where the page stalled

    @property
    def worst_stall_ms(self) -> int:
        return max((ms for _, ms in self.frame_gaps), default=0)  # window.__decktalk.warnings read after the recording

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


# ---- captions and chapters -----------------------------------------------------------
#
# These are written by `decktalk assemble` next to the final mp4:
#
#     build/out/<name>.srt          captions from the word timestamps, absolute in the mp4
#     build/out/<name>.vtt          the same cues as WebVTT
#     build/out/<name>.chapters.txt an ffmetadata file with one [CHAPTER] per section

CAPTION_MAX_CHARS = 42  # The longest line a cue may carry, in characters.
CAPTION_MAX_LINES = 2
CAPTION_MAX_GAP = 1.5  # A pause longer than this between two words ends the cue.
CAPTION_MAX_SECONDS = 7.0  # No cue stays on screen longer than this.
CAPTION_TAIL = 0.2  # Seconds a cue lingers after its last word, unless the next cue begins first.

_SENTENCE_END = (".", "?", "!")
_CLAUSE_END = (",", ";", ":", "—", "-")


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _chars(words: list[Word]) -> int:
    return sum(len(w.word) for w in words) + max(len(words) - 1, 0)


def _caption_lines(words: list[Word], max_chars: int) -> list[list[Word]]:
    """Split one run of words into lines of at most max_chars, preferring punctuation breaks."""
    lines: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur and _chars([*cur, w]) > max_chars:
            # Back up to the last clause break inside the line when it leaves a reasonable line behind.
            cut = next(
                (
                    i + 1
                    for i in range(len(cur) - 2, -1, -1)
                    if cur[i].word.endswith(_CLAUSE_END) and _chars(cur[: i + 1]) >= max_chars // 2
                ),
                None,
            )
            if cut is None:
                lines.append(cur)
                cur = []
            else:
                lines.append(cur[:cut])
                cur = cur[cut:]
                # The carried tail plus the new word can still overflow, so flush it as its own line.
                if _chars([*cur, w]) > max_chars:
                    lines.append(cur)
                    cur = []
        cur.append(w)
        if w.word.endswith(_SENTENCE_END) and _chars(cur) >= max_chars // 2:
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return lines


def display_words(words: list[Word], text: str) -> list[Word]:
    """The same words carrying the script's own spelling, so captions keep punctuation and case.

    Word lists come back from the voice with punctuation stripped. The script text is
    walked in step with them, matching each word to the next token whose letters agree,
    and a token that matches supplies the display form. When the two cannot be aligned,
    the words are returned as they are.
    """
    tokens = text.split()
    out: list[Word] = []
    j = 0
    for w in words:
        key = _letters(w.word)
        k = j
        while k < len(tokens) and k < j + 3 and _letters(tokens[k]) != key:
            k += 1
        if k < len(tokens) and k < j + 3:
            out.append(Word(tokens[k], w.start, w.end))
            j = k + 1
        else:
            return list(words)
    return out


def _letters(token: str) -> str:
    return "".join(ch for ch in token.lower() if ch.isalnum())


def caption_cues(
    words: list[Word],
    *,
    max_chars: int = CAPTION_MAX_CHARS,
    max_lines: int = CAPTION_MAX_LINES,
    max_gap: float = CAPTION_MAX_GAP,
    max_seconds: float = CAPTION_MAX_SECONDS,
) -> list[CaptionCue]:
    """Cues for one section's words, so a cue never spans a section boundary.

    Words are grouped into lines of at most max_chars, broken at punctuation where the line
    would overflow, and lines are paired into cues of at most max_lines. A sentence end,
    a pause longer than max_gap, or a cue running past max_seconds also closes the cue.
    """
    if not words:
        return []
    runs: list[list[Word]] = [[]]
    for w in words:
        if runs[-1] and w.start - runs[-1][-1].end > max_gap:
            runs.append([])
        runs[-1].append(w)
    cues: list[CaptionCue] = []
    for run in runs:
        pending: list[list[Word]] = []
        for line in _caption_lines(run, max_chars):
            pending.append(line)
            closes = (
                len(pending) >= max_lines
                or line[-1].word.endswith(_SENTENCE_END)
                or line[-1].end - pending[0][0].start >= max_seconds
            )
            if closes:
                cues.append(_cue(pending))
                pending = []
        if pending:
            cues.append(_cue(pending))
    # A cue lingers briefly after its last word, but never into the next cue.
    out: list[CaptionCue] = []
    for i, cue in enumerate(cues):
        end = cue.end + CAPTION_TAIL
        if i + 1 < len(cues):
            end = min(end, cues[i + 1].start)
        out.append(CaptionCue(cue.start, round(max(end, cue.end), 3), cue.lines))
    return out


def _cue(lines: list[list[Word]]) -> CaptionCue:
    return CaptionCue(
        start=round(lines[0][0].start, 3),
        end=round(lines[-1][-1].end, 3),
        lines=tuple(" ".join(w.word for w in line) for line in lines),
    )


def _stamp(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_srt(path: Path, cues: list[CaptionCue]) -> None:
    blocks = [f"{i}\n{_stamp(c.start, ',')} --> {_stamp(c.end, ',')}\n{c.text}\n" for i, c in enumerate(cues, 1)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(blocks), encoding="utf-8")


def write_vtt(path: Path, cues: list[CaptionCue]) -> None:
    blocks = [f"{_stamp(c.start, '.')} --> {_stamp(c.end, '.')}\n{c.text}\n" for c in cues]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("WEBVTT\n\n" + "\n".join(blocks), encoding="utf-8")


@dataclass(frozen=True)
class Chapter:
    start: float
    end: float
    title: str


def ffmetadata_escape(value: str) -> str:
    """Escape the characters ffmetadata treats specially, so a title survives the round trip."""
    out = value.replace("\\", "\\\\")
    for ch in "=;#":
        out = out.replace(ch, "\\" + ch)
    return out.replace("\n", "\\\n")


def write_chapters(path: Path, chapters: list[Chapter]) -> None:
    """An ffmetadata file whose [CHAPTER] blocks ffmpeg muxes with -map_metadata."""
    lines = [";FFMETADATA1"]
    for ch in chapters:
        lines += [
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(ch.start * 1000))}",
            f"END={int(round(ch.end * 1000))}",
            f"title={ffmetadata_escape(ch.title)}",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
