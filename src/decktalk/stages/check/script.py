"""What the script would sound like, judged before a single second of it is bought.

`narrate` refuses a script the voice must not receive, because a run that has already paid cannot
take the money back. `check` is the command a person runs before that, so it meets the same rules
and reports every one of them at once, with the line each sits on, rather than stopping on the
first. The rules themselves live in `stages/narrate/script_rules.py` and are read from there, so
the scan has one home and the judgement has one raiser.

Two codes cover it. `TAKE_PLACEHOLDER` is certain and names everything a voiced run would read out
or silently swallow, which is the one condition `narrate` refuses on. `TAKE_SPOKEN_SYMBOL` is
uncertain and names a word holding a digit or a symbol, because "41" may be exactly what the author
wants the voice to try.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from decktalk.findings import Code, Finding, Location
from decktalk.inputs.script import SECTION_RE, Segment
from decktalk.stages import judge
from decktalk.stages.narrate.script_rules import BRACKET_RE, PLACEHOLDER_RE, script_refusals, shown, symbol_tokens


def spoken_sections(markdown: str) -> dict[int, int]:
    """Which section each spoken line belongs to, by line number, for the lines the voice reads.

    A finding about one line is more use with the section it sits in, because that is the unit an
    author edits and the unit every other row of a check is grouped by.
    """
    out: dict[int, int] = {}
    number: int | None = None
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        match = SECTION_RE.match(line)
        if match:
            number = int(match.group("num"))
        elif line.startswith(("# ", "## ")) or line.strip() == "---":
            number = None
        elif number is not None:
            out[line_number] = number
    return out


def placeholder_rows(markdown: str) -> list[tuple[int, str]]:
    """(line, name) for every open placeholder in the spoken text, such as `[NUMBER]`.

    A placeholder nobody filled becomes its own name read aloud in a take that has already been
    bought, so it is named one at a time rather than counted.
    """
    inside = spoken_sections(markdown)
    out: list[tuple[int, str]] = []
    for number, line in enumerate(markdown.splitlines(), start=1):
        if number not in inside:
            continue
        out += [
            (number, match.group(1).strip())
            for match in BRACKET_RE.finditer(line)
            if not match.group(2) and PLACEHOLDER_RE.match(match.group(1).strip())
        ]
    return out


def placeholder_findings(markdown: str, *, script: Path) -> list[Finding]:
    """One certain judgement per thing in the spoken text that a voiced run must not receive.

    The open placeholders and the refusals are one condition under one code, because they are the
    one rule `narrate` refuses on: the script still holds something the voice would read out or turn
    into a pause nobody asked for.
    """
    inside = spoken_sections(markdown)
    where = script.as_posix()
    rows = [(line, f"[{name}] is still open, so a voiced run would read {name!r} out") for line, name in
            placeholder_rows(markdown)]  # fmt: skip
    rows += [(line, f"line {line} holds {what}") for line, what in script_refusals(markdown)]
    return [
        judge(
            Code.TAKE_PLACEHOLDER,
            f"{where} line {line} holds something a voiced run would read out or swallow, which is that {what}.",
            Location(where=where, file=script, line=line, section=inside.get(line)),
        )
        for line, what in sorted(rows)
    ]


def symbol_findings(segments: Iterable[Segment], *, script: Path) -> list[Finding]:
    """One uncertain judgement per section whose spoken words hold a digit or a symbol.

    A reader turns each of those into a word of its own and a voice may say something else, so the
    finding names the first few of them and leaves the decision with the author.
    """
    where = script.as_posix()
    found: list[Finding] = []
    for segment in segments:
        tokens = symbol_tokens(segment)
        if not tokens:
            continue
        found.append(
            judge(
                Code.TAKE_SPOKEN_SYMBOL,
                f"section {segment.index} speaks {len(tokens)} word(s) holding a digit or a symbol, which are "
                f"{shown(tokens)}. Write them the way the voice should say them.",
                Location(where=where, file=script, section=segment.index),
            )
        )
    return found


def script_findings(markdown: str, segments: Sequence[Segment], *, script: Path) -> list[Finding]:
    """Everything a check judges about the script, which is what it would refuse and what it may misread."""
    return [*placeholder_findings(markdown, script=script), *symbol_findings(segments, script=script)]


__all__ = [
    "placeholder_findings",
    "placeholder_rows",
    "script_findings",
    "spoken_sections",
    "symbol_findings",
]
