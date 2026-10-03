"""`script.md` parsed into the sections the voice reads.

The script is markdown with "## N. Title" headings and an optional "— 0:40 to 1:10" budget after
the title. Bracketed directions such as [Deck. The curve draws.] or [beat] are not spoken and
become a beat, a [pause N] direction or a `<break time="Ns" />` tag becomes a pause of N seconds,
markdown formatting is stripped, and an ALL-CAPS placeholder like [NUMBER] refuses a real run.

A section is pieces of text with the pause after each, and never markup. What a take is named by
is `speech.canonical_text` over those pieces, so nothing here may change what they are without
re-voicing every project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from decktalk.errors import InputError
from decktalk.inputs.paths import at
from decktalk.results import section_key
from decktalk.settings import NarrationConfig
from decktalk.speech import BEAT, Piece

DIRECTION_MARK = "\x00DIR\x00"
# "## 3. The demo — 1:40 to 3:40"  (the dash and time range are optional)
SECTION_RE = re.compile(
    r"^##\s+(?P<num>\d+)\.\s+(?P<title>.+?)(?:\s+[—–-]+\s+(?P<start>\d+:\d{2})\s+to\s+(?P<end>\d+:\d{2}))?\s*$"
)
DIRECTION_RE = re.compile(r"\[(?![A-Z][A-Z0-9_]*\])[^\]]*\]")
# "[pause 3]" or "[pause 2.5]": a timed pause, in seconds, in place of the default direction pause.
PAUSE_RE = re.compile(r"\[\s*pause\s+(?P<seconds>\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
# '<break time="1.5s" />' or '<break time="1500ms"/>' written by hand, which is a timed pause like [pause N].
BREAK_RE = re.compile(r"""<break\s+time\s*=\s*["'](?P<amount>\d+(?:\.\d+)?)(?P<unit>s|ms)["']\s*/?>""", re.IGNORECASE)
TAG_RE = re.compile(r"<break\b[^>]*>", re.IGNORECASE)
"""Any break tag, which is refused when `BREAK_RE` cannot read a length from it."""
BEAT_DASH_RE = re.compile(r"\s+—(?=\s|$)")
"""A dash standing alone, which the voice pauses on rather than says."""


@dataclass
class ScriptSection:
    """One "## N. Title" section of the script, cleaned for speech."""

    number: int
    title: str
    slug: str  # The heading as a file-name-safe word, which the tables print. A take is named by its digest.
    pieces: tuple[Piece, ...]  # the paragraphs the voice reads, each with the pause after it
    start: str | None = None
    end: str | None = None

    @property
    def key(self) -> str:
        return section_key(self.number)

    @property
    def spoken(self) -> str:
        """The words the voice says, without the pauses or the dashes that mark a beat."""
        text = " ".join(piece.text for piece in self.pieces)
        return re.sub(r"\s+", " ", BEAT_DASH_RE.sub(" ", text)).strip()

    @property
    def word_count(self) -> int:
        return len(self.spoken.split())

    @property
    def target_seconds(self) -> float | None:
        if self.start and self.end:
            return _mmss(self.end) - _mmss(self.start)
        return None

    def estimated_seconds(self, cfg: NarrationConfig) -> float:
        return round(self.word_count / cfg.words_per_minute * 60, 1)

    def placeholder_seconds(self, cfg: NarrationConfig) -> float:
        """How long a placeholder take of this section runs, which is speech and pauses and no silence around them.

        A beat, and a dash the author wrote after a word, each run `placeholder_beat_seconds`. The lead
        before the first word and the tail after the last belong to the section rather than to the
        take, so they are placed when the takes are joined and are not counted here.
        """
        timed = sum(piece.pause for piece in self.pieces if piece.pause is not None and piece.timed)
        beats = sum(piece.pause == BEAT for piece in self.pieces) + sum(piece.text.count(" —") for piece in self.pieces)
        return round(
            self.word_count / cfg.placeholder_words_per_minute * 60 + timed + beats * cfg.placeholder_beat_seconds, 3
        )


def _mmss(value: str) -> int:
    minutes, seconds = value.split(":")
    return int(minutes) * 60 + int(seconds)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "section"


def strip_markdown(text: str) -> tuple[Piece, ...]:
    """Prose ready for speech, as paragraphs. Every direction becomes the pause after the paragraph before it."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links, before directions
    # A timed pause carries its own length through the direction marker, and every other
    # direction carries the default. A break tag written by hand is a timed pause like any other.
    text = BREAK_RE.sub(lambda m: f"\n\n{DIRECTION_MARK}{_tag_seconds(m):g}\n\n", text)
    unread = TAG_RE.search(text)
    if unread:
        raise InputError(
            f"the script holds {unread.group(0)!r}, a break tag with no length DeckTalk can read.",
            hint='Write [pause N] for a pause of N seconds, or a tag like <break time="1.5s" />.',
        )
    text = PAUSE_RE.sub(lambda m: f"\n\n{DIRECTION_MARK}{float(m.group('seconds')):g}\n\n", text)
    text = DIRECTION_RE.sub(f"\n\n{DIRECTION_MARK}beat\n\n", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.M)
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n", text)]
    pieces: list[Piece] = []
    for p in (p for p in paragraphs if p):
        if p.startswith(DIRECTION_MARK):
            # A direction before any prose has nothing to pause after, and back-to-back
            # directions keep the longest pause rather than stacking. A plain direction is a
            # beat, and only a timed pause asks for measured silence, because the voice is
            # unsettled by measured silences when they are frequent.
            value = p.removeprefix(DIRECTION_MARK)
            seconds = BEAT if value == "beat" else float(value)
            if pieces:
                last = pieces[-1]
                pieces[-1] = replace(last, pause=seconds if last.pause is None else max(last.pause, seconds))
            continue
        pieces.append(Piece(p))
    if pieces and pieces[-1].pause is not None:
        # A pause after the last words is the section's tail, which the join places, so it is not asked for.
        pieces[-1] = replace(pieces[-1], pause=None)
    return tuple(pieces)


def _tag_seconds(match: re.Match[str]) -> float:
    """The length of a break tag written by hand, in seconds."""
    amount = float(match.group("amount"))
    return amount / 1000 if match.group("unit").lower() == "ms" else amount


def parse_script(markdown: str) -> list[ScriptSection]:
    """Every "## N. Title" section of the text, in the order it appears."""
    sections: list[ScriptSection] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        sections.append(
            ScriptSection(
                number=int(current["num"]),
                title=current["title"].strip(),
                slug=slugify(current["title"]),
                pieces=strip_markdown("\n".join(body)),
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
    return sections


def read_script(path: Path, root: Path, *, declared: set[int]) -> list[ScriptSection]:
    """Every section in the script, in order, where `declared` is every section number in `decktalk.toml`."""
    if not path.exists():
        raise InputError(
            f"{path.name} is not there.",
            hint="Write the script, or point [project] script at the file you meant.",
            location=at(path, root),
        )
    try:
        all_sections = parse_script(path.read_text(encoding="utf-8"))
    except InputError as refused:
        raise InputError(str(refused), hint=refused.hint, location=at(path, root)) from refused
    if not all_sections:
        raise InputError(
            f"{path.name} holds no '## N. Title' section.",
            hint="Open each spoken section with a heading such as '## 1. Open'.",
            location=at(path, root),
        )
    undeclared = [s.number for s in all_sections if s.number not in declared]
    if undeclared:
        raise InputError(
            f"script sections {undeclared} have no [[section]] in decktalk.toml.",
            hint="Add a [[section]] for each, or drop the heading from the script.",
            location=at(path, root),
        )
    return all_sections
