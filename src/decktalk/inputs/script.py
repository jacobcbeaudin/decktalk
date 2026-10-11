"""`script.md` parsed into the sections the voice reads.

The script is markdown with "## N. Title" headings, whose whole text after the number is the title.
Bracketed directions such as [Deck. The curve draws.] or [beat] are not spoken and
become a beat, a [pause N] direction or a `<break time="Ns" />` tag becomes a pause of N seconds,
markdown formatting is stripped, and ALL-CAPS unfinished text like [NUMBER] stays in the text, which
`check` reports as `SCRIPT_UNFINISHED`.

A section is one or more turns, each a stretch one voice reads, and a turn is pieces of text with
the pause after each, and never markup. A line holding only `[voice NAME]` hands the turns after it
to that voice until the next such line or the next section, and every whole line whose bracket opens
with the word voice is claimed by that rule, so it is never a note. The pause written where the
voice changes is the gap between two turns, which is placement and in neither turn's text. What a
take is named by is `speech.canonical_text` over a turn's pieces, and a section with no voice
direction is one turn of exactly the pieces it always had, so nothing here may change what they are
without re-voicing every project.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from decktalk.errors import InputError
from decktalk.inputs.paths import at
from decktalk.page import SECOND_DIGITS
from decktalk.results import Word, section_key
from decktalk.settings import NarrationConfig
from decktalk.speech import BEAT, PUNCT, Piece

SECONDS_PER_MINUTE = 60
"""Truth: a minute is sixty seconds, which a words-per-minute rate and an `m:ss` budget are both written in."""

DIRECTION_MARK = "\x00DIR\x00"
# "## 3. The demo": a number, a full stop and a space, then the title.
SECTION_RE = re.compile(r"^##\s+(?P<num>\d+)\.\s+(?P<title>.+?)\s*$")
DIRECTION_RE = re.compile(r"\[(?![A-Z][A-Z0-9_]*\])[^\]]*\]")
# "[pause 3]" or "[pause 2.5]": a timed pause, in seconds, in place of the default direction pause.
PAUSE_RE = re.compile(r"\[\s*pause\s+(?P<seconds>\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
# '<break time="1.5s" />' or '<break time="1500ms"/>' written by hand, which is a timed pause like [pause N].
BREAK_RE = re.compile(r"""<break\s+time\s*=\s*["'](?P<amount>\d+(?:\.\d+)?)(?P<unit>s|ms)["']\s*/?>""", re.IGNORECASE)
TAG_RE = re.compile(r"<break\b[^>]*>", re.IGNORECASE)
"""Any break tag, which is refused when `BREAK_RE` cannot read a length from it."""
BEAT_DASH_RE = re.compile(r"\s+—(?=\s|$)")
"""A dash standing alone, which the voice pauses on rather than says."""

WHOLE_BRACKET_RE = re.compile(r"\[[^\]\n]*\]")
"""One bracketed span and nothing else, which is what a line must be to be a direction of its own."""

VOICE_CLAIM_RE = re.compile(r"^\[\s*voice\b", re.IGNORECASE)
"""A whole line whose bracket opens with the word voice, which is a voice direction or is refused, and never a note."""

VOICE_DIRECTION_RE = re.compile(r"^\[\s*(?i:voice)\s+(?P<name>[a-z][a-z0-9]*)\s*\]$")
"""A voice direction: the word voice, in any case, and a voice's name, alone on a line."""

VOICE_HINT = (
    "A voice direction is a line holding only [voice NAME], where NAME is a [voices.NAME] table of decktalk.toml. "
    "A section that names a voice names one before every turn, its first included."
)
"""What every refusal of a voice direction ends on, which is the rule in two sentences."""

WORD_GAP_SECONDS = 0.02
"""Calibration: the gap an estimated word leaves before the next one, so two clicks are never one sound."""

SHORTEST_PLACEHOLDER_SECONDS = 0.1
"""Calibration: the least a placeholder take may run, so a section of one short word still has room for a click."""


def spoken_words(spoken: str) -> tuple[str, ...]:
    """The words of spoken text, each with its punctuation stripped, and no token that was only punctuation.

    It is the one tokenizer: the words DeckTalk estimates and the words an aligner is asked to time are
    both read through it, so the two never disagree about what a take says.
    """
    return tuple(word for token in spoken.split() if (word := token.strip(PUNCT)))


def estimated_words(spoken: str, seconds: float) -> list[Word]:
    """The words of spoken text spread evenly over `seconds`, so every cue resolves to a plausible time.

    Each word leaves a gap before the next, unless its share of the time is shorter than the gap,
    when it ends where it starts.
    """
    tokens = spoken_words(spoken)
    if not tokens:
        return []
    per = max(SHORTEST_PLACEHOLDER_SECONDS, seconds) / len(tokens)
    words: list[Word] = []
    for place, token in enumerate(tokens):
        start = round(place * per, SECOND_DIGITS)
        end = round((place + 1) * per - WORD_GAP_SECONDS, SECOND_DIGITS)
        words.append(Word(word=token, start=start, end=max(start, end)))
    return words


class Reading:
    """What a stretch of script says and how long it runs, read from its pieces, for a section or one of its turns."""

    pieces: tuple[Piece, ...]

    @property
    def spoken(self) -> str:
        """The words the voice says, without the pauses or the dashes that mark a beat."""
        text = " ".join(piece.text for piece in self.pieces)
        return re.sub(r"\s+", " ", BEAT_DASH_RE.sub(" ", text)).strip()

    @property
    def word_count(self) -> int:
        return len(self.spoken.split())

    def estimated_words(self, seconds: float) -> list[Word]:
        """The spoken words spread evenly over `seconds`, so every cue resolves to a plausible time."""
        return estimated_words(self.spoken, seconds)

    def estimated_seconds(self, cfg: NarrationConfig) -> float:
        return round(self.word_count / cfg.words_per_minute * SECONDS_PER_MINUTE, 1)

    def placeholder_seconds(self, cfg: NarrationConfig) -> float:
        """How long a placeholder take of these pieces runs, which is speech and pauses and no silence around them.

        A beat, and a dash the author wrote after a word, each run `placeholder_beat_seconds`. The lead
        before the first word, the gap between two turns and the tail after the last belong to the
        section rather than to a take, so they are placed when the takes are joined and are not counted here.
        """
        timed = sum(piece.pause for piece in self.pieces if piece.pause is not None and piece.timed)
        beats = sum(piece.pause == BEAT for piece in self.pieces) + sum(piece.text.count(" —") for piece in self.pieces)
        return round(
            self.word_count / cfg.placeholder_words_per_minute * SECONDS_PER_MINUTE
            + timed
            + beats * cfg.placeholder_beat_seconds,
            3,
        )


@dataclass(frozen=True)
class Turn(Reading):
    """One stretch of a section that one voice reads, from one voice direction to the next."""

    number: int
    """Counted from one within its section."""
    voice: str | None
    """The name its direction gave, or None when no direction names one, which the default voice reads."""
    pieces: tuple[Piece, ...]
    """The paragraphs this voice reads, each with the pause after it."""
    gap: float | None
    """The timed pause written where the voice changes after this turn, or None when none is written."""
    line: int
    """The script.md line its first prose sits on."""


@dataclass
class ScriptSection(Reading):
    """One "## N. Title" section of the script, cleaned for speech, as the turns its voices read."""

    number: int
    title: str
    slug: str  # The heading as a file-name-safe word, which the tables print. A take is named by its digest.
    turns: tuple[Turn, ...]  # the stretches each voice reads, in script order, at least one

    @property
    def key(self) -> str:
        return section_key(self.number)

    @property
    def pieces(self) -> tuple[Piece, ...]:
        """Every turn's paragraphs in order, which for a section with no voice direction is its one turn's."""
        return tuple(piece for turn in self.turns for piece in turn.pieces)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "section"


def _read(text: str) -> tuple[float | None, tuple[Piece, ...], float | None]:
    """The pause written before the first prose, the paragraphs, and the pause written after the last.

    Neither pause is asked of the voice. A pause before any prose has nothing to pause after, and one
    after the last words is the section's tail or the gap before another voice, which the join places.
    """
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
    head: float | None = None
    for p in (p for p in paragraphs if p):
        if p.startswith(DIRECTION_MARK):
            # Back-to-back directions keep the longest pause rather than stacking. A plain direction
            # is a beat, and only a timed pause asks for measured silence, because the voice is
            # unsettled by measured silences when they are frequent.
            value = p.removeprefix(DIRECTION_MARK)
            seconds = BEAT if value == "beat" else float(value)
            if pieces:
                last = pieces[-1]
                pieces[-1] = replace(last, pause=seconds if last.pause is None else max(last.pause, seconds))
            else:
                head = seconds if head is None else max(head, seconds)
            continue
        pieces.append(Piece(p))
    tail: float | None = None
    if pieces and pieces[-1].pause is not None:
        tail = pieces[-1].pause
        pieces[-1] = replace(pieces[-1], pause=None)
    return head, tuple(pieces), tail


def claimed(line: str) -> bool:
    """Whether one line of a script is a whole bracket opening with the word voice, which the voice rule claims."""
    stripped = line.strip()
    return WHOLE_BRACKET_RE.fullmatch(stripped) is not None and VOICE_CLAIM_RE.match(stripped) is not None


def directed(line: str) -> str | None:
    """The voice a line names when it is a voice direction, or None when it is not one."""
    found = VOICE_DIRECTION_RE.fullmatch(line.strip())
    return found["name"] if found else None


def _turns(body: list[tuple[int, str]], heading: int) -> tuple[Turn, ...]:
    """A section's body, as numbered lines, read into the turns each voice reads.

    A claimed line is removed. A voice direction naming a voice other than the one in force ends the
    stretch before it. A stretch with no prose is no turn, and its pauses join the boundary it sits
    on, whose gap is the longest timed pause written between the two turns' prose. A section with no
    prose at all is one empty turn, so every section has a first turn.
    """
    stretches: list[tuple[str | None, list[tuple[int, str]]]] = []
    voice: str | None = None
    current: list[tuple[int, str]] = []
    for number, line in body:
        if claimed(line):
            name = directed(line)
            if name is not None and name != voice:
                stretches.append((voice, current))
                voice, current = name, []
            continue
        current.append((number, line))
    stretches.append((voice, current))
    turns: list[Turn] = []
    between: list[float | None] = []
    for voice, lines in stretches:
        head, pieces, tail = _read("\n".join(line for _number, line in lines))
        if not pieces:
            between.append(head)
            continue
        if turns:
            timed = [pause for pause in (*between, head) if pause is not None and pause > BEAT]
            turns[-1] = replace(turns[-1], gap=max(timed) if timed else None)
        first = next(number for number, line in lines if _read(line)[1])
        turns.append(Turn(number=len(turns) + 1, voice=voice, pieces=pieces, gap=None, line=first))
        between = [tail]
    return tuple(turns) or (Turn(number=1, voice=voice, pieces=(), gap=None, line=heading),)


def _tag_seconds(match: re.Match[str]) -> float:
    """The length of a break tag written by hand, in seconds."""
    amount = float(match.group("amount"))
    return amount / 1000 if match.group("unit").lower() == "ms" else amount


def parse_script(markdown: str) -> list[ScriptSection]:
    """Every "## N. Title" section of the text, in the order it appears, each read into its turns."""
    sections: list[ScriptSection] = []
    current: dict[str, Any] | None = None
    heading = 0
    body: list[tuple[int, str]] = []

    def flush() -> None:
        if current is None:
            return
        sections.append(
            ScriptSection(
                number=int(current["num"]),
                title=current["title"].strip(),
                slug=slugify(current["title"]),
                turns=_turns(body, heading),
            )
        )

    for number, line in enumerate(markdown.splitlines(), start=1):
        match = SECTION_RE.match(line)
        if match:
            flush()
            current, heading = match.groupdict(), number
            body = []
            continue
        if line.startswith("## ") or line.startswith("# ") or line.strip() == "---":
            flush()
            current = None
            body = []
            continue
        if current is not None:
            body.append((number, line))
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
