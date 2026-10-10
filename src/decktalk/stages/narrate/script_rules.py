"""What the voice must never receive, and the scans `check` judges a script by.

The voice reads what it is given, so a brace becomes "open brace chars close brace
characters" in a take that has already been bought, and a note in square brackets inside a
paragraph is worse than read out, because the parser turns it into a silent pause nobody asked for.
`narrate` refuses each of those with the line it sits on, before it plans anything, because a run
that has already paid cannot take the money back.

A voice direction narrate cannot read is refused the same way and first: a line that opens with the
word voice and is no direction, a direction naming a voice the project does not declare, and the
first turn of a section that names a voice when no direction names that turn. Any of them would buy
a take in a voice nobody chose.

`narrate` judges nothing beyond that refusal. A digit or a symbol the voice may misread, and an unfilled
blank a voiced run would read out, are findings rather than refusals, and they are raised by
`check`, which is the command that reports what a build would cost and show. This module is
therefore the one home of both rules, and `stages/check/script.py` reads them from here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from decktalk.errors import InputError
from decktalk.findings import Code
from decktalk.inputs.script import (
    SECTION_RE,
    VOICE_HINT,
    WHOLE_BRACKET_RE,
    ScriptSection,
    claimed,
    directed,
    parse_script,
)
from decktalk.results import counted
from decktalk.tomlmap.suggest import did_you_mean

INLINE_DIRECTION_RE = re.compile(r"^(?:beat|pause\s+\d+(?:\.\d+)?)$", re.IGNORECASE)
"""The two directions a paragraph may hold, which the parser turns into a pause the author asked for.

A bracket that is a whole line is a direction. Inside a paragraph only a beat and a timed
pause are, and an unfilled blank such as `[NUMBER]` is left to its own rule, which `check`
reports.
"""

PLACEHOLDER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
"""What an unfilled blank such as `[NUMBER]` looks like, which has a rule of its own."""

BRACKET_RE = re.compile(r"\[([^\]\n]*)\](\()?")
"""One bracketed span, with the opening parenthesis that would make it a markdown link."""

BRACE_RE = re.compile(r"[{}]")
"""A brace the voice reads out as a word, which no script may send."""

COMMENT_RE = re.compile(r"<!--")
"""An HTML comment, which the voice reads out because the parser leaves it in the prose."""

SYMBOL_RE = re.compile(r"[0-9$€£%&@#*/+=<>~^|]")
"""A character a reader turns into a word of its own, where a voice may say something else."""

SHOWN_TOKENS = 5
"""Calibration: five words are enough to recognise the line, where the whole list would be a wall of text."""


def spoken_lines(markdown: str) -> Iterator[tuple[int, int, str]]:
    """(line number, section number, line) for each line the voice reads, which is the body of every numbered section.

    A finding about one line is more use with the section it sits in, because that is the unit an
    author edits and the unit every other row of a check is grouped by.
    """
    section: int | None = None
    for number, line in enumerate(markdown.splitlines(), start=1):
        match = SECTION_RE.match(line)
        if match:
            section = int(match.group("num"))
        elif line.startswith(("# ", "## ")) or line.strip() == "---":
            section = None
        elif section is not None:
            yield number, section, line


def script_refusals(markdown: str) -> list[tuple[int, str]]:
    """(line, what) for everything in the spoken text the voice would read out or silently swallow."""
    out: list[tuple[int, str]] = []
    for number, _section, line in spoken_lines(markdown):
        if COMMENT_RE.search(line):
            out.append((number, "an HTML comment, which the voice reads out"))
        if BRACE_RE.search(line):
            out.append((number, "a brace, which the voice reads out"))
        stripped = line.strip()
        for match in BRACKET_RE.finditer(line):
            inner, link = match.group(1).strip(), match.group(2)
            if link or match.group(0) == stripped or INLINE_DIRECTION_RE.match(inner) or PLACEHOLDER_RE.match(inner):
                continue
            out.append((number, f"[{inner}] inside a paragraph, which becomes a pause nobody asked for"))
    return out


class Misread(StrEnum):
    """How one line of a script fails the voice rule, which narrate and check each word their own way."""

    NOT_A_DIRECTION = "not-a-direction"
    UNKNOWN = "unknown"
    UNMARKED = "unmarked"
    BARE = "bare"


MISREAD_CODES = {
    Misread.NOT_A_DIRECTION: Code.SCRIPT_VOICE_UNKNOWN,
    Misread.UNKNOWN: Code.SCRIPT_VOICE_UNKNOWN,
    Misread.UNMARKED: Code.SCRIPT_VOICE_UNMARKED,
    Misread.BARE: Code.SCRIPT_VOICE_BARE,
}
"""The code each way of failing the voice rule is judged under."""


@dataclass(frozen=True)
class VoiceLine:
    """One line of a script the voice rule judges: where it sits, how it fails, and the bracket it holds."""

    line: int
    section: int
    misread: Misread
    inner: str
    """The text inside the bracket, or the voice a direction names, or nothing for an unmarked turn."""

    @property
    def code(self) -> Code:
        return MISREAD_CODES[self.misread]


def voice_lines(markdown: str, sections: Sequence[ScriptSection], names: Sequence[str]) -> list[VoiceLine]:
    """Every line of the spoken text that fails the voice rule, in line order, judged against the declared names.

    A claimed line that is no direction, a direction naming no declared voice, and the first turn of
    a section that holds a direction when no direction names that turn are what narrate refuses. A
    note that is exactly a declared voice's name is a slip check warns about, because it plays as a
    beat rather than handing the turns to that voice.
    """
    found: list[VoiceLine] = []
    directing: set[int] = set()
    for number, section, line in spoken_lines(markdown):
        stripped = line.strip()
        if claimed(line):
            name = directed(line)
            if name is None:
                found.append(VoiceLine(number, section, Misread.NOT_A_DIRECTION, stripped[1:-1].strip()))
                continue
            directing.add(section)
            if name not in names:
                found.append(VoiceLine(number, section, Misread.UNKNOWN, name))
        elif WHOLE_BRACKET_RE.fullmatch(stripped) and stripped[1:-1].strip() in names:
            found.append(VoiceLine(number, section, Misread.BARE, stripped[1:-1].strip()))
    for section in sections:
        first = section.turns[0]
        if section.number in directing and first.voice is None and first.pieces:
            found.append(VoiceLine(first.line, section.number, Misread.UNMARKED, ""))
    return sorted(found, key=lambda one: one.line)


def voice_refusals(markdown: str, names: Sequence[str]) -> list[tuple[int, Code, str]]:
    """(line, code, what) for every voice direction narrate cannot read, judged against the declared names."""
    listed = ", ".join(names)
    rows: list[tuple[int, Code, str]] = []
    for one in voice_lines(markdown, parse_script(markdown), names):
        if one.misread is Misread.NOT_A_DIRECTION:
            rows.append((one.line, one.code, f"[{one.inner}] is not a voice direction, which is written [voice NAME]"))
        elif one.misread is Misread.UNKNOWN:
            near = did_you_mean(one.inner, names)
            rows.append((one.line, one.code, f"[voice {one.inner}] names no voice, and the voices are {listed}.{near}"))
        elif one.misread is Misread.UNMARKED:
            what = f"section {one.section} names a voice, and the turn that starts here names none"
            rows.append((one.line, one.code, what))
    return rows


def check_script(where: str, markdown: str, names: Sequence[str]) -> None:
    """Refuse a script whose voice directions narrate cannot read, or whose spoken text holds something the voice
    would read out or swallow.

    `where` is the script's path as the reader sees it, which the caller has already made relative
    to the project root, and `names` is every voice the project declares, the default first.
    """
    voices = voice_refusals(markdown, names)
    if voices:
        rows = "\n  ".join(f"line {number}: {what}" for number, _code, what in voices)
        raise InputError(
            f"{where} has {counted(len(voices), 'voice direction')} narrate cannot read:\n  {rows}",
            hint=VOICE_HINT,
        )
    refusals = script_refusals(markdown)
    if not refusals:
        return
    rows = "\n  ".join(f"line {number}: {what}" for number, what in refusals)
    raise InputError(
        f"{where} has {counted(len(refusals), 'thing')} the voice must not receive:\n  {rows}",
        hint=("A direction goes on a line of its own. Inside a paragraph, write [beat] or [pause N] and nothing else."),
    )


def symbol_tokens(section: ScriptSection) -> tuple[str, ...]:
    """Every word of one section that holds a digit or a symbol a voice may read as its name.

    The scan lives here beside the refusals because both read the same spoken text, and `check`
    raises the finding from it, so the rule has one home and the judgement has one raiser.
    """
    return tuple(sorted({token for token in section.spoken.split() if SYMBOL_RE.search(token)}))


def shown(tokens: Iterable[str]) -> str:
    """The first few offending words as one phrase, which is what a finding's sentence carries."""
    listed = list(tokens)
    return ", ".join(listed[:SHOWN_TOKENS])


def ascending(sections: Iterable[ScriptSection]) -> tuple[ScriptSection, ScriptSection] | None:
    """The first pair of headings whose numbers do not ascend, or None when the whole script does.

    The take index is the one order the narration is joined in, so a script that counts backwards
    would place its takes in an order no other reading of the project agrees with.
    """
    return next(((first, second) for first, second in pairwise(sections) if second.number <= first.number), None)


__all__ = [
    "SYMBOL_RE",
    "Misread",
    "VoiceLine",
    "ascending",
    "check_script",
    "script_refusals",
    "shown",
    "spoken_lines",
    "symbol_tokens",
    "voice_lines",
    "voice_refusals",
]
