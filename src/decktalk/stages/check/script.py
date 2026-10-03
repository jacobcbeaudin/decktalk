"""What the script would sound like, judged before a single second of it is bought.

`narrate` refuses a script the voice must not receive, because a run that has already paid cannot
take the money back. `check` is the command a person runs before that, so it meets the same rules
and reports every one of them at once, with the line each sits on, rather than stopping on the
first. The rules themselves live in `stages/narrate/script_rules.py` and are read from there, so
the scan has one home and the judgement has one raiser.

Three codes cover it. `SCRIPT_UNFINISHED` is an error and names everything a voiced run would read out
or silently swallow, which is the one condition `narrate` refuses on. `SCRIPT_SPOKEN_SYMBOL` is a
warning and names a word holding a digit or a symbol, because "41" may be exactly what the author
wants the voice to try. `SCRIPT_PAUSE_DROPPED` is an error and names a section whose timed pause the
voice's model does not render, which `narrate` refuses to build the voice for.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from decktalk.findings import Code, Finding, Location, judge
from decktalk.inputs.script import ScriptSection
from decktalk.stages.narrate.plan import DROPPED_PAUSE_HINT
from decktalk.stages.narrate.script_rules import (
    BRACKET_RE,
    PLACEHOLDER_RE,
    script_refusals,
    shown,
    spoken_lines,
    symbol_tokens,
)


def placeholder_rows(markdown: str) -> list[tuple[int, str]]:
    """(line, name) for every open placeholder in the spoken text, such as `[NUMBER]`.

    A placeholder nobody filled becomes its own name read aloud in a take that has already been
    bought, so it is named one at a time rather than counted.
    """
    out: list[tuple[int, str]] = []
    for number, _section, line in spoken_lines(markdown):
        out += [
            (number, match.group(1).strip())
            for match in BRACKET_RE.finditer(line)
            if not match.group(2) and PLACEHOLDER_RE.match(match.group(1).strip())
        ]
    return out


def placeholder_findings(markdown: str, *, script: Path) -> list[Finding]:
    """One error per thing in the spoken text that a voiced run must not receive.

    The open placeholders and the refusals are one condition under one code, because they are the
    one rule `narrate` refuses on: the script still holds something the voice would read out or turn
    into a pause nobody asked for.
    """
    inside = {number: section for number, section, _line in spoken_lines(markdown)}
    where = script.as_posix()
    rows = [(line, f"[{name}] is still open, so a voiced run would read {name!r} out") for line, name in
            placeholder_rows(markdown)]  # fmt: skip
    rows += [(line, f"line {line} holds {what}") for line, what in script_refusals(markdown)]
    return [
        judge(
            Code.SCRIPT_UNFINISHED,
            f"{where} line {line} holds something a voiced run would read out or swallow, which is that {what}.",
            Location(where=where, file=script, line=line, section=inside.get(line)),
        )
        for line, what in sorted(rows)
    ]


def symbol_findings(sections: Iterable[ScriptSection], *, script: Path) -> list[Finding]:
    """One warning per section whose spoken words hold a digit or a symbol.

    A reader turns each of those into a word of its own and a voice may say something else, so the
    finding names the first few of them and leaves the decision with the author.
    """
    where = script.as_posix()
    found: list[Finding] = []
    for section in sections:
        tokens = symbol_tokens(section)
        if not tokens:
            continue
        found.append(
            judge(
                Code.SCRIPT_SPOKEN_SYMBOL,
                f"section {section.number} speaks {len(tokens)} word(s) holding a digit or a symbol, which are "
                f"{shown(tokens)}. Write them the way the voice should say them.",
                Location(where=where, file=script, section=section.number),
            )
        )
    return found


def pause_findings(dropped: Iterable[ScriptSection], *, provider: str, model: str, script: Path) -> list[Finding]:
    """One error per section whose timed pauses the voice's model would drop.

    `narrate` refuses to build the voice for such a script, so the finding names each section here,
    before anything is bought, with the two ways out.
    """
    where = script.as_posix()
    return [
        judge(
            Code.SCRIPT_PAUSE_DROPPED,
            f"section {section.number} asks for a timed pause, and [voice] provider {provider!r} renders none on "
            f"model {model!r}, so a voiced run would drop it. {DROPPED_PAUSE_HINT}",
            Location(where=where, file=script, section=section.number),
        )
        for section in dropped
    ]


def script_findings(markdown: str, sections: Sequence[ScriptSection], *, script: Path) -> list[Finding]:
    """Everything a check judges about the script, which is what it would refuse and what it may misread."""
    return [*placeholder_findings(markdown, script=script), *symbol_findings(sections, script=script)]


__all__ = [
    "pause_findings",
    "placeholder_findings",
    "placeholder_rows",
    "script_findings",
    "symbol_findings",
]
