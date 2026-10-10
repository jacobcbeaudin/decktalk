"""What the script would sound like, judged before a single second of it is bought.

`narrate` refuses a script the voice must not receive, because a run that has already paid cannot
take the money back. `check` is the command a person runs before that, so it meets the same rules
and reports every one of them at once, with the line each sits on, rather than stopping on the
first. The rules themselves live in `stages/narrate/script_rules.py` and are read from there, so
the scan has one home and the judgement has one raiser.

The voices come first. `SCRIPT_VOICE_UNKNOWN` and `SCRIPT_VOICE_UNMARKED` are errors and name each
voice direction `narrate` would refuse, `SCRIPT_VOICE_BARE` is a warning and names a note that is only
a voice's name, and `VOICE_UNUSED` is a warning and names a voice the project declares that no turn
is read by.

Three codes cover the rest. `SCRIPT_UNFINISHED` is an error and names everything a voiced run would read out
or silently swallow: what `narrate` refuses, and unfinished text such as `[NUMBER]`, which `narrate`
sends as it stands. `SCRIPT_SPOKEN_SYMBOL` is a
warning and names a word holding a digit or a symbol, because "41" may be exactly what the author
wants the voice to try. `SCRIPT_PAUSE_DROPPED` is an error and names a section whose timed pause the
voice's model does not render, which `narrate` refuses to build the voice for.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from decktalk.findings import Code, Finding, Location, judge
from decktalk.inputs.script import ScriptSection, parse_script
from decktalk.locate import locate
from decktalk.settings import PROJECT_FILE
from decktalk.stages.narrate.plan import DROPPED_PAUSE_HINT, Dropped
from decktalk.stages.narrate.script_rules import (
    BRACKET_RE,
    PLACEHOLDER_RE,
    Misread,
    VoiceLine,
    script_refusals,
    shown,
    spoken_lines,
    symbol_tokens,
    voice_lines,
)
from decktalk.tomlmap.suggest import did_you_mean


def placeholder_rows(markdown: str) -> list[tuple[int, str]]:
    """(line, name) for every unfilled blank in the spoken text, such as `[NUMBER]`.

    An unfilled blank becomes its own name read aloud in a take that has already been
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

    The unfinished text and what `narrate` refuses are one condition under one code, because both are
    something the voice would read out or turn into a pause nobody asked for. `narrate` refuses only
    the second, so the first is met here.
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


def pause_findings(dropped: Iterable[Dropped], *, script: Path) -> list[Finding]:
    """One error per section and voice whose timed pauses that voice's model would drop.

    `narrate` refuses to build the voice for such a script, so the finding names each section here,
    with the voice that reads the turn, before anything is bought, with the two ways out.
    """
    where = script.as_posix()
    named = {(one.section.number, one.voice.name): one for one in dropped}
    return [
        judge(
            Code.SCRIPT_PAUSE_DROPPED,
            f"section {one.section.number} asks for a timed pause in a turn the voice {one.voice.name} reads, and "
            f"{one.voice.provider!r} renders none on model {one.voice.model!r}, so a voiced run would drop it. "
            f"{DROPPED_PAUSE_HINT}",
            Location(where=where, file=script, line=one.turn.line, section=one.section.number),
        )
        for one in named.values()
    ]


def voice_findings(markdown: str, names: Sequence[str], *, script: Path) -> list[Finding]:
    """One finding per line of the script that fails the voice rule, judged against the voices the project declares.

    Every error here is a voice direction `narrate` refuses before it plans anything, so a check
    reports them all at once. A note that is only a voice's name is a warning, because it plays as a
    beat and most likely meant to hand the turns after it to that voice.
    """
    where = script.as_posix()
    return [
        judge(
            one.code,
            _voice_sentence(one, where, names),
            Location(where=where, file=script, line=one.line, section=one.section),
        )  # fmt: skip
        for one in voice_lines(markdown, parse_script(markdown), names)
    ]


def _voice_sentence(one: VoiceLine, where: str, names: Sequence[str]) -> str:
    """What check says about one line that fails the voice rule."""
    listed = ", ".join(names)
    if one.misread is Misread.NOT_A_DIRECTION:
        return (
            f"{where} line {one.line} holds [{one.inner}], which is not a voice direction. Write [voice NAME] on a "
            f"line of its own, where NAME is one of {listed}."
        )
    if one.misread is Misread.UNKNOWN:
        return (
            f"{where} line {one.line} holds [voice {one.inner}], and no voice is named {one.inner}. The voices are "
            f"{listed}.{did_you_mean(one.inner, names)}"
        )
    if one.misread is Misread.UNMARKED:
        return (
            f"{where} line {one.line} starts a turn of section {one.section} that no [voice NAME] line names, and "
            f"section {one.section} names a voice, so every turn of it names its own. Put [voice NAME] on its own "
            "line before it."
        )
    return (
        f"{where} line {one.line} holds [{one.inner}], a note that plays as a beat, and {one.inner} is a voice. "
        f"Write [voice {one.inner}] on a line of its own to hand the turns after it to {one.inner}."
    )


def unused_voices(
    sections: Iterable[ScriptSection], names: Sequence[str], default: str, *, script: Path, project: Path, text: str
) -> list[Finding]:
    """One warning per voice the project declares that no turn of the script is read by.

    A turn no direction names is read by the default voice. A voice nobody reads is never asked for a
    take, so the warning says nothing about its id, and the location is its table in `project`, whose
    `text` gives the line.
    """
    read = {turn.voice or default for section in sections for turn in section.turns}
    where = script.as_posix()
    return [
        judge(
            Code.VOICE_UNUSED,
            f"{PROJECT_FILE} declares the voice {name} in [voices.{name}], and no turn of {where} is read by it.",
            Location(where=f"[voices.{name}]", file=project, line=locate(text, f"voices.{name}")),
        )
        for name in names
        if name not in read
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
    "unused_voices",
    "voice_findings",
]
