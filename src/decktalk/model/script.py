"""`script.md` parsed into the sections the voice reads.

The script is markdown with "## N. Title" headings and an optional "— 0:40 to 1:10" budget after
the title. Bracketed directions such as [Deck. The curve draws.] or [beat] are not spoken and
become a short pause, a [pause N] direction becomes a pause of N seconds, markdown formatting is
stripped, and an ALL-CAPS placeholder like [NUMBER] refuses a real run.

The exact text a section sends is the take's cache key, so nothing here may change without
re-voicing every project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigError, MissingInputError
from ..settings import NarrationConfig

DIRECTION_MARK = "\x00DIR\x00"
# "## 3. The demo — 1:40 to 3:40"  (the dash and time range are optional)
SECTION_RE = re.compile(
    r"^##\s+(?P<num>\d+)\.\s+(?P<title>.+?)(?:\s+[—–-]+\s+(?P<start>\d+:\d{2})\s+to\s+(?P<end>\d+:\d{2}))?\s*$"
)
DIRECTION_RE = re.compile(r"\[(?![A-Z][A-Z0-9_]*\])[^\]]*\]")
# "[pause 3]" or "[pause 2.5]": a timed pause, in seconds, in place of the default direction pause.
PAUSE_RE = re.compile(r"\[\s*pause\s+(?P<seconds>\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"\[([A-Z][A-Z0-9_]*)\]")
BREAK_RE = re.compile(r'<break time="([0-9.]+)s"\s*/>')
PUNCT = "\"'“”‘’.,;:!?()[]—–-…"


def break_tag(seconds: float) -> str:
    return f'<break time="{seconds:g}s" />'


@dataclass
class Segment:
    """One "## N. Title" section of the script, cleaned for speech."""

    index: int
    title: str
    slug: str
    text: str  # prose with direction pauses as <break/> tags
    start: str | None = None
    end: str | None = None
    first_spoken: bool = False

    @property
    def key(self) -> str:
        return f"{self.index:02d}"

    @property
    def spoken(self) -> str:
        """The words the voice says, without break tags or the dashes that mark a beat."""
        text = re.sub(r"\s*<break[^>]*/>\s*", " ", self.text)
        text = re.sub(r"\s+—(?=\s|$)", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @property
    def word_count(self) -> int:
        return len(self.spoken.split())

    @property
    def placeholders(self) -> list[str]:
        return sorted(set(PLACEHOLDER_RE.findall(self.text)))

    @property
    def filename(self) -> str:
        return f"{self.key}-{self.slug}.mp3"

    @property
    def words_filename(self) -> str:
        return f"{self.key}-{self.slug}.words.json"

    @property
    def target_seconds(self) -> float | None:
        if self.start and self.end:
            return _mmss(self.end) - _mmss(self.start)
        return None

    def tts_text(self, cfg: NarrationConfig) -> str:
        """The text sent to the voice. Silence before the first section and after every last
        word is added to the audio afterwards rather than requested with break tags."""
        return self.text

    def estimated_seconds(self, cfg: NarrationConfig) -> float:
        return round(self.word_count / cfg.words_per_minute * 60, 1)

    def silent_seconds(self, cfg: NarrationConfig) -> float:
        breaks = sum(float(t) for t in BREAK_RE.findall(self.text))
        beat_seconds = self.text.count(" —") * cfg.silent_beat_seconds
        lead = cfg.opening_silence_seconds if self.first_spoken else 0.0
        return round(
            self.word_count / cfg.silent_words_per_minute * 60 + breaks + beat_seconds + lead + cfg.min_tail_seconds, 3
        )


def _mmss(value: str) -> int:
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "section"


def strip_markdown(text: str) -> str:
    """Prose ready for speech. Every direction becomes a break tag appended to the paragraph before it."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links, before directions
    # A timed pause carries its own length through the direction marker, and every other
    # direction carries the default.
    text = PAUSE_RE.sub(lambda m: f"\n\n{DIRECTION_MARK}{float(m.group('seconds')):g}\n\n", text)
    text = DIRECTION_RE.sub(f"\n\n{DIRECTION_MARK}beat\n\n", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    paragraphs = [p for p in paragraphs if p]
    out: list[str] = []
    pending: float | None = None  # the pause owed to the paragraph before the next one
    for p in paragraphs:
        if p.startswith(DIRECTION_MARK):
            # A direction before any prose has nothing to pause after, and back-to-back
            # directions keep the longest pause rather than stacking. A plain direction
            # is a beat, which the voice reads as a dash, and only a timed pause becomes
            # a break tag, because break tags unsettle the voice when they are frequent.
            value = p.removeprefix(DIRECTION_MARK)
            seconds = 0.0 if value == "beat" else float(value)
            if out:
                pending = seconds if pending is None else max(pending, seconds)
            continue
        if pending is not None:
            out[-1] = f"{out[-1]} {break_tag(pending)}" if pending > 0 else f"{out[-1]} —"
            pending = None
        out.append(p)
    return "\n\n".join(out)


def parse_script(markdown: str) -> list[Segment]:
    """Every "## N. Title" section of the text, in the order it appears."""
    segments: list[Segment] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        segments.append(
            Segment(
                index=int(current["num"]),
                title=current["title"].strip(),
                slug=slugify(current["title"]),
                text=strip_markdown("\n".join(body)),
                start=current["start"],
                end=current["end"],
            )
        )

    for line in markdown.splitlines():
        match = SECTION_RE.match(line)
        if match:
            flush()
            current = match.groupdict()
            body = []
            continue
        if line.startswith("## ") or line.startswith("# ") or line.strip() == "---":
            flush()
            current = None
            body = []
            continue
        if current is not None:
            body.append(line)
    flush()
    return segments


def read_script(path: Path, *, declared: set[int], clips: set[int]) -> tuple[list[Segment], list[Segment]]:
    """(every section in the script, the spoken ones in order with first_spoken set).

    `declared` is every section number in `decktalk.toml`, and `clips` are the ones that play a
    clip instead of a page, which the voice never reads.
    """
    if not path.exists():
        raise MissingInputError(f"script not found: {path}")
    all_segments = parse_script(path.read_text(encoding="utf-8"))
    if not all_segments:
        raise ConfigError(f"no '## N. Title' sections found in {path}")
    undeclared = [s.index for s in all_segments if s.index not in declared]
    if undeclared:
        raise ConfigError(f"script sections {undeclared} have no [[section]] in decktalk.toml")
    spoken = [s for s in all_segments if s.index not in clips]
    for i, seg in enumerate(spoken):
        seg.first_spoken = i == 0
    return all_segments, spoken
