"""What the voice must never receive, and the scans `check` judges a script by.

The voice reads what it is given, so a placeholder nobody filled becomes "open brace chars close
brace characters" in a take that has already been bought, and a note in square brackets inside a
paragraph is worse than read out, because the parser turns it into a silent pause nobody asked for.
`narrate` refuses each of those with the line it sits on, before it plans anything, because a run
that has already paid cannot take the money back.

`narrate` judges nothing beyond that refusal. A digit or a symbol the voice may misread, and an open
placeholder a voiced run would read out, are findings rather than refusals, and they are raised by
`check`, which is the command that reports what a build would spend and show. This module is
therefore the one home of both rules, and `stages/check/script.py` reads them from here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from decktalk.errors import InputError
from decktalk.inputs.script import SECTION_RE, Segment

INLINE_DIRECTION_RE = re.compile(r"^(?:beat|pause\s+\d+(?:\.\d+)?)$", re.IGNORECASE)
"""The two directions a paragraph may hold, which the parser turns into a pause the author asked for.

A bracket that is a whole line is a stage direction. Inside a paragraph only a beat and a timed
pause are, and a placeholder is refused by its own rule under its own flag.
"""

PLACEHOLDER_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
"""What an unfilled placeholder such as `[NUMBER]` looks like, which has a rule of its own."""

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


def spoken_lines(markdown: str) -> Iterator[tuple[int, str]]:
    """(line number, line) for each line the voice reads, which is the body of every numbered section."""
    inside = False
    for number, line in enumerate(markdown.splitlines(), start=1):
        if SECTION_RE.match(line):
            inside = True
        elif line.startswith(("# ", "## ")) or line.strip() == "---":
            inside = False
        elif inside:
            yield number, line


def script_refusals(markdown: str) -> list[tuple[int, str]]:
    """(line, what) for everything in the spoken text the voice would read out or silently swallow."""
    out: list[tuple[int, str]] = []
    for number, line in spoken_lines(markdown):
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


def check_script(where: str, markdown: str) -> None:
    """Refuse a script whose spoken text holds something the voice would read out or swallow.

    `where` is the script's path as the reader sees it, which the caller has already made relative
    to the project root.
    """
    refusals = script_refusals(markdown)
    if not refusals:
        return
    rows = "\n  ".join(f"line {number}: {what}" for number, what in refusals)
    raise InputError(
        f"{where} has {len(refusals)} thing(s) the voice must not receive:\n  {rows}",
        hint=(
            "A stage direction goes on a line of its own. Inside a paragraph, write [beat] or "
            "[pause N] and nothing else."
        ),
    )


def symbol_tokens(segment: Segment) -> tuple[str, ...]:
    """Every word of one section that holds a digit or a symbol a voice may read as its name.

    The scan lives here beside the refusals because both read the same spoken text, and `check`
    raises the finding from it, so the rule has one home and the judgement has one raiser.
    """
    return tuple(sorted({token for token in segment.spoken.split() if SYMBOL_RE.search(token)}))


def shown(tokens: Iterable[str]) -> str:
    """The first few offending words as one phrase, which is what a finding's sentence carries."""
    listed = list(tokens)
    return ", ".join(listed[:SHOWN_TOKENS])


def ascending(segments: Iterable[Segment]) -> tuple[Segment, Segment] | None:
    """The first pair of headings whose numbers do not ascend, or None when the whole script does.

    The take index is the one order the narration is joined in, so a script that counts backwards
    would place its takes in an order no other reading of the project agrees with.
    """
    rows = list(segments)
    for first, second in zip(rows, rows[1:], strict=False):
        if second.index <= first.index:
            return first, second
    return None


__all__ = [
    "SYMBOL_RE",
    "ascending",
    "check_script",
    "script_refusals",
    "shown",
    "spoken_lines",
    "symbol_tokens",
]
