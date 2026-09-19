"""The rules a script must obey before any of it is paid for.

The voice reads what it is given, so a placeholder nobody filled becomes "open brace chars close
brace characters" in a take that has already been bought. A note in square brackets inside a
paragraph is worse than read out, because the parser turns it into a silent pause nobody asked
for. Each of those is refused with the line it sits on, and `narrate` checks the whole script
before it plans anything. Digits and symbols are an uncertain finding instead of a refusal,
because "41" may be exactly what the author wants the voice to try.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from ...errors import ConfigError
from ...model.script import SECTION_RE, Segment
from ...verdicts import Finding, Verdict

# A bracket that is a whole line is a stage direction. Inside a paragraph only a beat and a timed
# pause are, and a placeholder is refused by its own rule under its own flag.
INLINE_DIRECTION_RE = re.compile(r"^(?:beat|pause\s+\d+(?:\.\d+)?)$", re.IGNORECASE)
PLACEHOLDER_RE, BRACKET_RE = re.compile(r"^[A-Z][A-Z0-9_]*$"), re.compile(r"\[([^\]\n]*)\](\()?")
BRACE_RE, COMMENT_RE = re.compile(r"[{}]"), re.compile(r"<!--")
# A reader turns each of these into a word of its own, and a voice may say something else.
SYMBOL_RE = re.compile(r"[0-9$€£%&@#*/+=<>~^|]")


def spoken_lines(markdown: str) -> Iterator[tuple[int, str]]:
    """(line number, line) for each line the voice reads: the body of every numbered section."""
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
    """Raise when the spoken text holds something the voice would read out or turn into a silent pause.

    `where` is the script's path as the reader sees it, which the caller has already made relative
    to the project root.
    """
    refusals = script_refusals(markdown)
    if not refusals:
        return
    rows = "\n  ".join(f"line {number}: {what}" for number, what in refusals)
    raise ConfigError(
        f"{where} has {len(refusals)} thing(s) the voice must not receive:\n  {rows}\n"
        "A stage direction goes on a line of its own. Inside a paragraph, write [beat] or [pause N] "
        "and nothing else."
    )


def symbol_findings(segments: list[Segment], where: str) -> list[Finding]:
    """One uncertain row per section whose spoken words hold a digit or a symbol a voice may misread."""
    rows: list[Finding] = []
    for segment in segments:
        found = sorted({token for token in segment.spoken.split() if SYMBOL_RE.search(token)})
        if found:
            detail = f"{', '.join(found[:5])} hold digits or symbols. Write them the way the voice should say them."
            rows.append(Finding(detail, Verdict.SPOKEN_SYMBOL, section=segment.index, where=where))
    return rows
