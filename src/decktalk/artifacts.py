"""Typed build artifacts and their JSON files under build/.

These file shapes are part of the public contract: the page runtime and users' own
scripts read them. Field names match the JSON keys.

    build/narration/takes.json          Takes: one entry per narrated section
    build/narration/NN-slug.words.json  list[Word]
    build/narration/timeline.json       Timeline: absolute section and word times in narration.mp3
    build/cue-times.json                CueTimes: {"NN": "cue@seconds,..."} relative to the section start
    build/recordings/NN.json            RecordingLog: what the recorder did and where t=0 landed
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Self


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Write atomically: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=indent) + "\n", encoding="utf-8")
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
class Take:
    index: int
    chapter: str
    file: str
    words_file: str
    hash: str
    word_count: int
    estimated_seconds: float
    duration_seconds: float
    target_seconds: float | None = None
    speech_end_seconds: float | None = None
    tail_padded_seconds: float = 0.0
    spoken: str = ""  # The words the voice says, with the script's punctuation, which captions borrow.

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Takes:
    script: str
    model: str
    output_format: str
    estimated: bool = False
    estimate_basis: str = ""
    sections: dict[str, Take] = field(default_factory=dict)
    total_seconds: float = 0.0

    @classmethod
    def load(cls, path: Path) -> Self | None:
        if not path.exists():
            return None
        d = _read_json(path)
        rows = {k: Take.from_dict(v) for k, v in d.get("sections", {}).items()}
        return cls(
            script=str(d.get("script", "")),
            model=str(d.get("model", "")),
            output_format=str(d.get("output_format", "")),
            estimated=bool(d.get("estimated", False)),
            estimate_basis=str(d.get("estimate_basis", "")),
            sections=dict(sorted(rows.items())),
            total_seconds=float(d.get("total_seconds", 0.0)),
        )

    def save(self, path: Path) -> None:
        self.sections = dict(sorted(self.sections.items()))
        self.total_seconds = round(sum(s.duration_seconds for s in self.sections.values()), 3)
        _write_json(path, asdict(self))


@dataclass
class TimelineSection:
    title: str
    start: float
    end: float
    duration: float
    speech_end: float | None
    words: list[Word] = field(default_factory=list)
    lead_seconds: float = 0.0  # Silence joined in before the take, part of `duration`. The words already include it.

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            title=str(d["title"]),
            start=float(d["start"]),
            end=float(d["end"]),
            duration=float(d["duration"]),
            speech_end=None if d.get("speech_end") is None else float(d["speech_end"]),
            words=[Word.from_dict(w) for w in d.get("words", [])],
            lead_seconds=float(d.get("lead_seconds", 0.0)),
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
class CueTimes:
    """Resolved cue times per section, seconds relative to the section's start."""

    sections: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Self:
        if not path.exists():
            return cls()
        return cls({k: parse_cue_times(v) for k, v in _read_json(path).items()})

    def save(self, path: Path) -> None:
        _write_json(path, {k: self.query(k) for k in sorted(self.sections) if self.sections[k]})

    def query(self, key: str) -> str | None:
        """The ?cues= value for a section, or None when it has no resolved cues."""
        cues = self.sections.get(key)
        if not cues:
            return None
        return ",".join(f"{cue}@{t}" for cue, t in cues.items())

    def get(self, key: str, cue: str) -> float | None:
        return self.sections.get(key, {}).get(cue)


def parse_cue_times(value: str) -> dict[str, float]:
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
        d = _read_json(path)
        recording_log = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        recording_log.frame_gaps = [(at, int(ms)) for at, ms in recording_log.frame_gaps]
        return recording_log

    def save(self, path: Path) -> None:
        """Write standard JSON, with null for a gap time that is not a finite number."""
        d = asdict(self)
        d["frame_gaps"] = [[gap_time(at), ms] for at, ms in self.frame_gaps]
        _write_json(path, d)

    @property
    def trim_seconds(self) -> float:
        return self.t0_seconds if self.t0_seconds is not None else self.clock_start_seconds


# ---- captions and chapters -----------------------------------------------------------
#
# These are written by `decktalk assemble` next to the final mp4:
#
#     build/out/<name>.srt          captions from the word timestamps, absolute in the mp4
#     build/out/<name>.vtt          the same cues as WebVTT
#     build/out/<name>.chapters.txt an ffmetadata file with one [CHAPTER] per section

CAPTION_MAX_CHARS = 42  # The longest line a cue may carry, in characters. A cue has at most two lines.
CAPTION_MIN_SILENCE = 1.0  # A silence at least this long between two words always ends the cue.
CAPTION_TAIL = 0.2  # Seconds a cue lingers after its last word, unless the next cue begins first.
CAPTION_MAX_UNITS = 8  # The most sentences one cue is ever considered to hold.

# Short words a line should not end on and a split should not touch, so "billions of billions" stays whole.
_FUNCTION_WORDS = frozenset("a an the of to in on at for and or but by with as is it its my your".split())
_SENTENCE_END_RE = re.compile(r"[.?!][\"'”’)]*$")
_CLAUSE_END_RE = re.compile(r"[,;:—–-]$")


@dataclass(frozen=True)
class CaptionCue:
    start: float
    end: float
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _ends_sentence(token: str) -> bool:
    return bool(_SENTENCE_END_RE.search(token))


def _ends_clause(token: str) -> bool:
    return bool(_CLAUSE_END_RE.search(token))


def _is_function_word(token: str) -> bool:
    return re.sub(r"[^\w']", "", token.lower().replace("’", "'")) in _FUNCTION_WORDS


def _join(words: list[Word]) -> str:
    return " ".join(w.word for w in words)


def _wrap(words: list[Word], max_chars: int, *, several: bool = False) -> tuple[list[str], bool] | None:
    """One line if the words fit, else the best break into two lines, or None when no break fits.

    Returns the lines and whether the break falls after a sentence end. The break goes after a
    sentence end, else after a clause mark, while the shorter line is at least a third of the
    longer. A cue of `several` sentences takes a sentence-end break even when lopsided. Otherwise
    the lines are balanced, and a line that ends on a function word costs the most. No line is a
    single word unless that word is a whole sentence.
    """
    whole = _join(words)
    if len(whole) <= max_chars:
        return [whole], True
    best: tuple[tuple[int, int], list[str], bool] | None = None
    for i in range(1, len(words)):
        first, second = words[:i], words[i:]
        la, lb = len(_join(first)), len(_join(second))
        if la > max_chars or lb > max_chars:
            continue
        if any(len(part) == 1 and not _ends_sentence(part[0].word) for part in (first, second)):
            continue
        last, nxt = first[-1].word, second[0].word
        even = min(la, lb) >= max(la, lb) / 3
        if _ends_sentence(last) and (even or several):
            rank = 0
        elif _ends_clause(last) and even:
            rank = 1
        else:
            rank = 2
        score = abs(la - lb) + 16 * _is_function_word(last) + 4 * _is_function_word(nxt)
        if best is None or (rank, score) < best[0]:
            best = ((rank, score), [_join(first), _join(second)], _ends_sentence(last))
    return (best[1], best[2]) if best else None


def _split_sentence(words: list[Word], max_chars: int) -> list[list[Word]]:
    """Split a sentence too long for two lines into parts that each fit in two lines.

    The split goes at the clause mark nearest the middle when both parts keep a third of the
    characters, else at the word boundary nearest the middle with no function word on either
    side. Each part keeps at least three words. A sentence with no such boundary splits at the
    boundary nearest the middle, so no line runs past max_chars.
    """
    if len(words) < 2 or _wrap(words, max_chars) is not None:
        return [words]
    total = len(_join(words))
    ranked: list[tuple[int, float, int]] = []
    for i in range(3, len(words) - 2):
        cut = len(_join(words[:i]))
        token, nxt = words[i - 1].word, words[i].word
        if _ends_clause(token) and min(cut, total - cut) >= total / 3:
            ranked.append((0, abs(cut - total / 2), i))
        elif not _ends_clause(token) and not _is_function_word(token) and not _is_function_word(nxt):
            ranked.append((1, abs(cut - total / 2), i))
    if not ranked:  # no boundary follows the rules, so take the middle rather than overflow a line
        ranked = [(2, abs(len(_join(words[:i])) - total / 2), i) for i in range(1, len(words))]
    i = min(ranked)[2]
    return _split_sentence(words[:i], max_chars) + _split_sentence(words[i:], max_chars)


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
    min_silence: float = CAPTION_MIN_SILENCE,
) -> list[CaptionCue]:
    """Cues for one section's words, so a cue never spans a section boundary.

    Every word keeps its start time. A cue ends before any silence of min_silence or more.
    Between those breaks, whole sentences are grouped into cues at the lowest cost: each cue
    costs 1000, a cue of several sentences whose line break cannot fall at a sentence end
    costs 1200 more, a two-word cue 400 more, and a one-word cue 5000 more. A sentence too long
    for two lines is split first (see `_split_sentence`), and each part is a cue of its own.
    Each cue has one or two lines of at most max_chars (see `_wrap`).
    """
    if not words:
        return []
    runs: list[list[Word]] = [[]]
    for w in words:
        if runs[-1] and w.start - runs[-1][-1].end >= min_silence:
            runs.append([])
        runs[-1].append(w)

    groups: list[list[Word]] = []
    for run in runs:
        sentences: list[list[Word]] = [[]]
        for w in run:
            sentences[-1].append(w)
            if _ends_sentence(w.word):
                sentences.append([])
        units = [part for s in sentences if s for part in _split_sentence(s, max_chars)]
        groups += _group_units(units, max_chars)

    cues: list[CaptionCue] = []
    for i, group in enumerate(groups):
        wrapped = _wrap(group, max_chars, several=True)
        lines = wrapped[0] if wrapped else [_join(group)]
        end = group[-1].end + CAPTION_TAIL
        if i + 1 < len(groups):  # a cue lingers briefly after its last word, but never into the next cue
            end = min(end, groups[i + 1][0].start)
        cues.append(CaptionCue(round(group[0].start, 3), round(max(end, group[-1].end), 3), tuple(lines)))
    return cues


def _group_units(units: list[list[Word]], max_chars: int) -> list[list[Word]]:
    """Group consecutive units (whole sentences, or parts of a split one) into cues at the lowest cost."""

    def cost(chunk: list[list[Word]]) -> int | None:
        several = len(chunk) > 1
        if several and not all(_ends_sentence(u[-1].word) for u in chunk):
            return None  # only whole sentences share a cue
        flat = [w for u in chunk for w in u]
        wrapped = _wrap(flat, max_chars, several=several)
        if wrapped is None:
            return None if several else 6000  # a unit that cannot wrap still gets a cue of its own
        c = 1000 + (1200 if several and not wrapped[1] else 0)
        return c + (5000 if len(flat) == 1 else 400 if len(flat) == 2 else 0)

    n = len(units)
    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(max(0, j - CAPTION_MAX_UNITS), j):
            c = cost(units[i:j])
            if c is not None and best[i] + c < best[j]:
                best[j], back[j] = best[i] + c, i
    groups: list[list[Word]] = []
    j = n
    while j > 0:
        groups.append([w for u in units[back[j] : j] for w in u])
        j = back[j]
    return groups[::-1]


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
