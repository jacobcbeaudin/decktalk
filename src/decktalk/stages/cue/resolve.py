"""The arithmetic that turns one cue phrase into one second on its section's own clock.

Every function here takes words and gives numbers. It opens no file, reads no project and knows
nothing about a run, so what a cue would resolve to can be read and tested without a take on disk.
That is what lets `check` resolve the same rows against the words a voiced run would have, and
`cue` resolve them against the words a voiced run already produced, through one implementation.

A second counts from the start of its own section, so a section's lead moves every word cue later
and a cue anchored to the section's own start stays at zero.
"""

from __future__ import annotations

from collections.abc import Container, Mapping, Sequence
from pathlib import Path

from decktalk.findings import Code, Finding, Location
from decktalk.inputs.cues import Cue, CuedSection, find_phrase, phrase_matches
from decktalk.pipeline import Stage
from decktalk.results import CueTime, SectionCues, Word
from decktalk.stages import SECOND_DIGITS, judge

SECTION_START = "$start"
"""The phrase that anchors a cue to its section's own beginning rather than to a spoken word."""

SECTION_END = "$end"
"""The phrase that anchors a cue to the end of the last word its section speaks."""

SECTION_START_SECONDS = 0.0
"""Where a section's own clock begins, which is what a cue with no word behind it resolves to."""

REPEATS_MIN = 2
"""How many times a phrase has to occur before a cue that names no occurrence is ambiguous."""


def anchor_time(cue: Cue, words: Sequence[Word]) -> float | None:
    """The moment this cue is anchored to, before its own nudge, or None when its phrase is not spoken."""
    if cue.on == SECTION_START:
        return SECTION_START_SECONDS
    if cue.on == SECTION_END:
        return words[-1].end if words else None
    found = find_phrase(words, cue.on, cue.occurrence, cue.case_sensitive)
    return None if found is None else words[found].start


def resolve_cue(cue: Cue, words: Sequence[Word]) -> float | None:
    """The second this cue fires, which is its anchor plus its own nudge, never before the section starts.

    A nudge that would pull a cue in front of its own section has nowhere to go, so it lands on the
    section's start, which is the case `CUE_NO_ONSET` names.
    """
    anchor = anchor_time(cue, words)
    if anchor is None:
        return None
    return max(SECTION_START_SECONDS, round(anchor + cue.offset, SECOND_DIGITS))


def ambiguity(cue: Cue, words: Sequence[Word]) -> str | None:
    """The sentence for a phrase that occurs more than once on a cue that names no occurrence, or None.

    It is a sentence and not a judgement because the cue does resolve: it takes the first occurrence,
    which is what the author most often meant, and the frozen code list holds no code for a choice
    that was made for them.
    """
    if cue.occurrence_set or cue.on in (SECTION_START, SECTION_END):
        return None
    matches = phrase_matches(words, cue.on, cue.case_sensitive)
    if len(matches) < REPEATS_MIN:
        return None
    times = ", ".join(f"{words[found].start:.2f}s" for found in matches)
    return (
        f"{cue.on!r} occurs {len(matches)} times in section {cue.cue.split(':', 1)[0]}, at {times}, and the cue "
        'takes the first. Set "occurrence" on the cue to choose another.'
    )


def short_section(block: CuedSection, words: Sequence[Word]) -> str | None:
    """The sentence for a section whose speech ends before its visuals need it to, or None.

    `min_seconds` is the length the author says the pictures of this section need. Falling short of
    it is worth saying and is not a judgement, because the frozen code list holds no code for it.
    """
    if block.min_seconds is None or not words:
        return None
    speech = words[-1].end
    if speech >= block.min_seconds:
        return None
    return (
        f"section {block.number} speaks for {speech:.1f}s, which is {block.min_seconds - speech:.1f}s short of the "
        f"{block.min_seconds:.1f}s its visuals ask for."
    )


def resolve_sections(
    cued: Sequence[CuedSection],
    words_by_section: Mapping[int, Sequence[Word]],
    *,
    clips: Container[int],
    estimated: Container[int],
    cues_file: Path | None = None,
    stage: Stage | None = None,
) -> tuple[tuple[SectionCues, ...], list[Finding]]:
    """Every section's cues resolved against the words it speaks, and everything that judges.

    `words_by_section` holds the words of each section that has any, in seconds after that section
    starts. A section that is not in it has no take, so its cues resolve to nothing: that is a
    `CUE_UNRESOLVED` for a page section, whose recorder would otherwise play it blind, and the plain
    skip it should be for a section in `clips`, which the voice never reads. `estimated` names the
    sections whose words come from a placeholder take rather than from a voice.
    """
    sections: list[SectionCues] = []
    found: list[Finding] = []
    for block in sorted(cued, key=lambda one: one.number):
        words = words_by_section.get(block.number)
        rows: list[CueTime] = []
        for cue in block.cues:
            place = Location(where=cue.cue, file=cues_file, section=block.number, cue=cue.cue)
            seconds = None if words is None else resolve_cue(cue, words)
            rows.append(CueTime(cue=cue.cue, phrase=cue.on, seconds=seconds, offset=cue.offset))
            if seconds is None:
                found += _unresolved(cue, block, words, place, clips=clips, stage=stage)
                continue
            if seconds == SECTION_START_SECONDS:
                found.append(
                    judge(
                        Code.CUE_NO_ONSET,
                        f"the cue {cue.cue} resolves to {seconds:.2f}s, which is where section {block.number} "
                        f"itself starts rather than where a word does, so nothing measures its onset "
                        f"against {cue.on!r}.",
                        place,
                        stage=stage,
                    )
                )
        sections.append(
            SectionCues(
                section=block.number,
                key=f"{block.number:02d}",
                estimated=block.number in estimated,
                cues=tuple(rows),
            )
        )
    return tuple(sections), found


def _unresolved(
    cue: Cue,
    block: CuedSection,
    words: Sequence[Word] | None,
    place: Location,
    *,
    clips: Container[int],
    stage: Stage | None,
) -> list[Finding]:
    """The judgement for one cue that resolved to nothing, or none when the section is never spoken."""
    if block.number in clips:
        return []
    if words is None:
        message = (
            f"the cue {cue.cue} waits for {cue.on!r} and section {block.number} has no take, so there are no "
            "words to place it against."
        )
    else:
        message = (
            f"the cue {cue.cue} waits for {cue.on!r} and section {block.number} speaks {len(words)} words, "
            "none of which is that phrase."
        )
    return [judge(Code.CUE_UNRESOLVED, message, place, stage=stage)]


__all__ = [
    "REPEATS_MIN",
    "SECTION_END",
    "SECTION_START",
    "SECTION_START_SECONDS",
    "ambiguity",
    "anchor_time",
    "resolve_cue",
    "resolve_sections",
    "short_section",
]
