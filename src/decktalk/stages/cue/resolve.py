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
from difflib import SequenceMatcher
from pathlib import Path
from typing import NamedTuple

from decktalk.files import json_text
from decktalk.findings import Applicability, Code, Edit, EditFix, Finding, Location, judge
from decktalk.inputs.cues import SECTION_END, SECTION_START, Cue, CuedSection, Spoken, norm
from decktalk.page import SECOND_DIGITS
from decktalk.pipeline import Stage
from decktalk.results import CueTime, SectionCues, Word, section_key
from decktalk.stages import SECTION_START_SECONDS

REPEATS_MIN = 2
"""How many times a phrase has to occur before a cue that names no occurrence is ambiguous."""

NEAREST_MIN_RATIO = 0.6
"""Calibration: how alike a spoken phrase and a cue's phrase must read before one is offered for the other.

It is the ratio `difflib` reports over the two phrases as the matcher compares them. An edit that
changed a word or two of a sentence leaves the old phrase well above it, and a phrase the section
never came near stays below it, so the fix is offered for the common case and not for a guess.
"""

WIDTH_SLACK = 1
"""How many words longer or shorter than the cue's phrase a spoken phrase may be and still be offered."""


def occurrences(cue: Cue, spoken: Spoken) -> list[int]:
    """Index of the word every occurrence of the cue's phrase starts on, which is none for either end.

    This is the one scan of a section for a cue. Its anchor and whether it is ambiguous are both read
    off it, so a section is matched against once per cue however much is asked of the match.
    """
    if cue.phrase in (SECTION_START, SECTION_END):
        return []
    return spoken.matches(cue.phrase, cue.case_sensitive)


def anchor_time(cue: Cue, spoken: Spoken, found: Sequence[int]) -> float | None:
    """The moment this cue is anchored to, before its own nudge, or None when its phrase is not spoken.

    `found` is the cue's `occurrences` in `spoken`.
    """
    words = spoken.words
    if cue.phrase == SECTION_START:
        return SECTION_START_SECONDS
    if cue.phrase == SECTION_END:
        return words[-1].end if words else None
    return words[found[cue.occurrence - 1]].start if 1 <= cue.occurrence <= len(found) else None


def resolve_cue(cue: Cue, spoken: Spoken, found: Sequence[int]) -> float | None:
    """The second this cue fires, which is its anchor plus its own nudge, never before the section starts.

    A nudge that would pull a cue in front of its own section has nowhere to go, so it lands on the
    section's start, which is the case `CUE_NO_ONSET` names.
    """
    anchor = anchor_time(cue, spoken, found)
    if anchor is None:
        return None
    return max(SECTION_START_SECONDS, round(anchor + cue.offset_seconds, SECOND_DIGITS))


def ambiguity(cue: Cue, spoken: Spoken, found: Sequence[int]) -> str | None:
    """The sentence for a phrase that occurs more than once on a cue that names no occurrence, or None.

    It is a sentence and not a judgement because the cue does resolve: it takes the first occurrence,
    which is what the author most often meant, and the frozen code list holds no code for a choice
    that was made for them.
    """
    if cue.occurrence_set or len(found) < REPEATS_MIN:
        return None
    times = ", ".join(f"{spoken.words[start].start:.2f}s" for start in found)
    return (
        f"{cue.phrase!r} occurs {len(found)} times in section {cue.id.split(':', 1)[0]}, at {times}, and the cue "
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


def nearest_phrase(cue: Cue, spoken: Spoken) -> str | None:
    """The phrase this section speaks that reads most like the cue's own, or None when none comes close.

    A phrase that stopped resolving was most often edited rather than deleted, so the words that
    replaced it sit in the section and read almost the same. The phrase offered is written the way
    the matcher reads it back, and it is offered only when the cue would resolve on it.
    """
    wanted = [token for token in (norm(one) for one in cue.phrase.split()) if token]
    if not wanted or not spoken.words:
        return None
    target, best, found = " ".join(wanted), NEAREST_MIN_RATIO, None
    widths = range(max(1, len(wanted) - WIDTH_SLACK), len(wanted) + WIDTH_SLACK + 1)
    for width in widths:
        for start in range(len(spoken.folded) - width + 1):
            ratio = SequenceMatcher(None, target, " ".join(spoken.folded[start : start + width])).ratio()
            if ratio > best:
                best, found = ratio, " ".join(spoken.exact[start : start + width])
    if found is None or spoken.find(found, cue.occurrence, cue.case_sensitive) is None:
        return None
    return found


def phrase_fix(cue: Cue, phrase: str, cues_file: Path | None, lines: Sequence[str]) -> EditFix | None:
    """The edit that writes `phrase` into the cue's row, or None when the row's line cannot be found.

    It is unsafe rather than safe, because the phrase decides which word the reveal lands on, and a
    phrase read off the section by likeness is a reading of what the author meant rather than a fact.
    """
    if cues_file is None or cue.line is None or not 1 <= cue.line <= len(lines):
        return None
    written = lines[cue.line - 1]
    old, new = f'"phrase": {json_text(cue.phrase)}', f'"phrase": {json_text(phrase)}'
    if written.count(old) != 1:
        return None
    return EditFix(
        title=(
            f"Change the phrase of {cue.id} to {phrase!r} in {cues_file.as_posix()}, which is the nearest "
            "phrase its section speaks."
        ),
        applicability=Applicability.UNSAFE,
        edits=(Edit(file=cues_file, line=cue.line, old=written, new=written.replace(old, new)),),
    )


class Resolved(NamedTuple):
    """Every section's cues resolved, what judges them, and the choices they made for their author."""

    sections: tuple[SectionCues, ...]
    found: list[Finding]
    notes: dict[int, list[str]]
    """For each section, the sentence for every cue that took one of several occurrences of its phrase."""


def resolve_sections(
    cued: Sequence[CuedSection],
    words_by_section: Mapping[int, Sequence[Word]],
    *,
    clips: Container[int],
    estimated: Container[int],
    cues_file: Path | None = None,
    cues_text: str = "",
    stage: Stage | None = None,
) -> Resolved:
    """Every section's cues resolved against the words it speaks, everything that judges, and every choice made.

    `words_by_section` holds the words of each section that has any, in seconds after that section
    starts. A section that is not in it has no take, so its cues resolve to nothing: that is a
    `CUE_UNRESOLVED` for a page section, whose recorder would otherwise play it blind, and the plain
    skip it should be for a section in `clips`, which the voice never reads. `estimated` names the
    sections whose words come from a placeholder take rather than from a voice. `cues_text` is the
    cue file as it is written, which is what a fix that rewrites one row's phrase is worked out on.
    """
    lines = cues_text.splitlines()
    sections: list[SectionCues] = []
    found: list[Finding] = []
    notes: dict[int, list[str]] = {}
    for block in sorted(cued, key=lambda one: one.number):
        words = words_by_section.get(block.number)
        spoken = None if words is None else Spoken.of(words)
        rows: list[CueTime] = []
        for cue in block.cues:
            place = Location(where=cue.id, file=cues_file, line=cue.line, section=block.number, cue=cue.id)
            matched = [] if spoken is None else occurrences(cue, spoken)
            seconds = None if spoken is None else resolve_cue(cue, spoken, matched)
            rows.append(CueTime(id=cue.id, phrase=cue.phrase, seconds=seconds, nudge_seconds=cue.offset_seconds))
            said = None if spoken is None else ambiguity(cue, spoken, matched)
            if said is not None:
                notes.setdefault(block.number, []).append(said)
            if seconds is None:
                found += _unresolved(cue, block, spoken, place, clips=clips, stage=stage, lines=lines)
                continue
            if seconds == SECTION_START_SECONDS:
                found.append(
                    judge(
                        Code.CUE_NO_ONSET,
                        f"the cue {cue.id} resolves to {seconds:.2f}s, which is where section {block.number} "
                        f"itself starts rather than where a word does, so nothing measures its onset "
                        f"against {cue.phrase!r}.",
                        place,
                        stage=stage,
                    )
                )
        sections.append(
            SectionCues(
                section=block.number,
                key=section_key(block.number),
                estimated=block.number in estimated,
                cues=tuple(rows),
            )
        )
    return Resolved(tuple(sections), found, notes)


def _unresolved(
    cue: Cue,
    block: CuedSection,
    spoken: Spoken | None,
    place: Location,
    *,
    clips: Container[int],
    stage: Stage | None,
    lines: Sequence[str],
) -> list[Finding]:
    """The judgement for one cue that resolved to nothing, or none when the section is never spoken.

    When the section speaks a phrase that reads almost like the cue's, the judgement names it and
    carries the edit that writes it into the cue's own row, because an edited sentence is the most
    common reason a phrase stops resolving.
    """
    if block.number in clips:
        return []
    fix = None
    if not cue.phrase:
        message = (
            f"the cue {cue.id} has no phrase yet, so write the words it lands on into its 'phrase' and "
            "it will have a second."
        )
    elif spoken is None:
        message = (
            f"the cue {cue.id} waits for {cue.phrase!r} and section {block.number} has no take, so there are no "
            "words to place it against."
        )
    else:
        message = (
            f"the cue {cue.id} waits for {cue.phrase!r} and section {block.number} speaks {len(spoken.words)} words, "
            "none of which is that phrase."
        )
        nearest = nearest_phrase(cue, spoken)
        if nearest is not None:
            message += f" The nearest phrase it speaks is {nearest!r}."
            fix = phrase_fix(cue, nearest, place.file, lines)
    return [judge(Code.CUE_UNRESOLVED, message, place, stage=stage, fix=fix)]


__all__ = [
    "NEAREST_MIN_RATIO",
    "REPEATS_MIN",
    "Resolved",
    "ambiguity",
    "anchor_time",
    "nearest_phrase",
    "occurrences",
    "phrase_fix",
    "resolve_cue",
    "resolve_sections",
    "short_section",
]
