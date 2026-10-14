"""Every spoken word with its span, which is how a cue phrase is written.

The clock is the section's own, which is the clock `?words=` passes to a page and the one
`cue-times.json` resolves against: zero is where the section starts, which is the start of its
recording's section clock and the first frame of its cut. A section's lead of silence counts, so its first word
starts after the lead.

The words come back from the voice with their punctuation stripped, and the take index records the
text each section was really narrated from, so the script's own spelling and case are put back here
rather than in the caller. A caller that wants to write a cue phrase reads what it will see in the
script, and a build made with placeholder narration says so on every row it reports.

This command writes only its run's events file. `clip` lists a span's words through `row_of`, so the two
carry the same spelling.
"""

from __future__ import annotations

from collections.abc import Sequence

from decktalk.artifacts import Takes
from decktalk.captions import display_words
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.machine.run import Run
from decktalk.results import SectionTurn, SectionWords, Word, WordsResult
from decktalk.stages import selects


def row_of(inputs: Inputs, takes: Takes, number: int) -> SectionWords:
    """One section's words on its own clock, each turn's carrying the script's own spelling and named by its voice."""
    rows = takes.turns_of(number)
    shown: list[Word] = []
    turns: list[SectionTurn] = []
    for turn in inputs.turn_words(number):
        said = list(turn.words.words)
        spelled = display_words(said, turn.spoken) if turn.spoken else said
        shown += spelled
        turns.append(SectionTurn(turn=turn.turn, voice=turn.voice, estimated=turn.estimated, word_count=len(spelled)))
    return SectionWords(
        section=number,
        key=rows[0].key,
        estimated=inputs.words(number).estimated,
        words=tuple(shown),
        turns=tuple(turns),
    )


def words(inputs: Inputs, run: Run, *, only: Sequence[int] | None = None) -> WordsResult:
    """Every spoken section's words, in seconds after that section starts, in script order.

    A section this run was asked for that has no take is a refusal rather than an empty row, because
    a caller that read an empty answer for a finished one would write its cue phrase against nothing.
    """
    takes = inputs.takes(required=True)
    chosen = selects(only)
    if only:
        spoken = set(takes.numbers)
        absent = sorted(number for number in only if number not in spoken)
        if absent:
            raise InputError(
                f"section(s) {absent} have no take in {inputs.workspace.takes_path.name}.",
                hint=f"The spoken sections are {sorted(spoken)}.",
                location=at(inputs.workspace.takes_path, inputs.root),
            )
    rows = tuple(row_of(inputs, takes, number) for number in takes.numbers if chosen(number))
    return run.result(WordsResult, sections=rows)


__all__ = ["row_of", "words"]
