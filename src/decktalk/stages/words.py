"""Every spoken word with its span, which is how a cue phrase is written.

The clock is the section's own, which is the clock `?words=` passes to a page and the one
`cue-times.json` resolves against: zero is where the section starts, which is narration t=0 of its
recording and the first frame of its cut. A section's lead of silence counts, so its first word
starts after the lead.

The words come back from the voice with their punctuation stripped, and the take index records the
text each section was really narrated from, so the script's own spelling and case are put back here
rather than in the caller. A caller that wants to write a cue phrase reads what it will see in the
script, and a build made with placeholder narration says so on every row it reports.

This command writes nothing. `clip` reads one section through it, so the two can never disagree
about where a word sits.
"""

from __future__ import annotations

from collections.abc import Sequence

from decktalk.artifacts import Take, Takes
from decktalk.captions import display_words
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.machine import Run
from decktalk.pipeline import Artifact
from decktalk.results import SectionWords, WordsResult
from decktalk.stages import selects


def take_index(inputs: Inputs) -> Takes:
    """The take index, or a `NOT_BUILT` refusal naming the stage that writes it."""
    return Takes.require(inputs.workspace.takes_path, Artifact.TAKES)


def row_of(inputs: Inputs, take: Take) -> SectionWords:
    """One section's words, on its own clock, carrying the script's own spelling."""
    spoken = inputs.words(take.section, take.hash)
    shown = display_words(list(spoken), take.spoken) if take.spoken else list(spoken)
    return SectionWords(
        section=take.section,
        key=take.key,
        estimated=not take.voiced,
        words=tuple(shown),
    )


def section_words(inputs: Inputs, number: int) -> SectionWords | None:
    """One section's words, or None when the take index has no row for that section.

    `clip` cuts a span out of one section and lists the words wholly inside it, so it asks this
    rather than reading the take index itself, and the two agree about every start and every end.
    """
    takes = Takes.read(inputs.workspace.takes_path)
    if takes is None:
        return None
    take = takes.of(number)
    return None if take is None else row_of(inputs, take)


def words(inputs: Inputs, run: Run, *, only: Sequence[int] | None = None) -> WordsResult:
    """Every spoken section's words, in seconds after that section starts, in script order.

    A section this run was asked for that has no take is a refusal rather than an empty row, because
    a caller that read an empty answer for a finished one would write its cue phrase against nothing.
    """
    takes = take_index(inputs)
    chosen = selects(only)
    if only:
        spoken = {take.section for take in takes.sections}
        absent = sorted(number for number in only if number not in spoken)
        if absent:
            raise InputError(
                f"section(s) {absent} have no take in {inputs.workspace.takes_path.name}.",
                hint=f"The spoken sections are {sorted(spoken)}.",
                location=at(inputs.workspace.takes_path, inputs.root),
            )
    rows = tuple(row_of(inputs, take) for take in takes.sections if chosen(take.section))
    return run.result(WordsResult, sections=rows)


__all__ = ["row_of", "section_words", "take_index", "words"]
