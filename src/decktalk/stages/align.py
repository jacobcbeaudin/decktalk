"""Stage 2: cue phrases become narration timestamps (cues.json + words -> build/cue-times.json).

cues.json:
    {"sections": {"3": {"min_seconds": 25,
                        "cues": [{"cue": "3.2", "on": "On a typical"},
                                 {"cue": "3.x", "on": "Zero", "occurrence": 2, "case_sensitive": true},
                                 {"cue": "15.2", "on": "$end", "offset": 0.3}]}}}

    cue     a cue id the page understands (decktalk-runtime.js)
    on      a word or short phrase from that section's narration: first occurrence,
            case-insensitive, punctuation ignored. "$start" = 0, "$end" = end of speech.
            Times count from the section start, so a section's lead_seconds moves every word
            cue later, and "$start" stays at 0.
    occurrence / case_sensitive / offset (seconds) refine the match. A phrase that occurs more
            than once in its section, on a cue that sets no occurrence, gets a warning that names
            every occurrence and its time.
    verify  false leaves the cue out of a plain `decktalk verify`, for a reveal too small
            or too slow for a frame difference to measure. The default is true.

Unresolved cues are reported and left out, and the recording still runs. A cue id that
appears nowhere in its page as a quoted literal is reported as unknown, because no slide
would ever reveal it, and align raises UnknownCueError unless allow_unknown_cues is set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import CueTime, CueTimes, Word
from ..errors import ConfigError, MissingInputError
from ..jsonio import relative
from ..model import PageSection, Project
from ..model.cues import Cue, SectionCues, find_phrase, page_mentions, phrase_matches
from ..verdicts import Finding, Findings, Verdict

log = logging.getLogger(__name__)


def unknown_cue_ids(project: Project, specs: list[SectionCues]) -> list[tuple[str, str, str]]:
    """(section key, cue id, page) for every cue id that its page never mentions.

    Clip sections have no page, and a page file that does not exist is reported by the
    recorder instead, so both are skipped.
    """
    pages: dict[str, str] = {}
    out: list[tuple[str, str, str]] = []
    for spec in specs:
        section = project.section(spec.number)
        if not isinstance(section, PageSection):
            continue
        if section.page not in pages:
            path = project.path(section.page)
            if not path.exists():
                continue
            pages[section.page] = path.read_text(encoding="utf-8")
        html = pages[section.page]
        out += [(section.key, cue.cue, section.page) for cue in spec.cues if not page_mentions(html, cue.cue)]
    return out


def ambiguity_note(cue: Cue, words: list[Word]) -> str | None:
    """The warning for a phrase that occurs more than once when the cue does not name its occurrence, else None."""
    if cue.occurrence_set or cue.on in ("$start", "$end"):
        return None
    matches = phrase_matches(words, cue.on, cue.case_sensitive)
    if len(matches) < 2:
        return None
    times = ", ".join(f"{words[i].start:.2f}s" for i in matches)
    return (
        f"{cue.on!r} occurs {len(matches)} times in this section, at {times}. The cue uses the first. "
        'Set "occurrence" to choose one.'
    )


def resolve_cue(cue: Cue, words: list[Word]) -> float | None:
    anchor = anchor_time(cue, words)
    return None if anchor is None else round(anchor + cue.offset, 2)


def anchor_time(cue: Cue, words: list[Word]) -> float | None:
    """The moment the cue is anchored to, before its offset: the matched word's start, or an edge."""
    if cue.on == "$start":
        return 0.0
    if cue.on == "$end":
        return words[-1].end if words else None
    idx = find_phrase(words, cue.on, cue.occurrence, cue.case_sensitive)
    return None if idx is None else words[idx].start


@dataclass
class SectionCueTimes:
    key: str
    speech_end: float
    min_seconds: float | None
    resolved: list[CueTime]
    skipped: str | None = None  # why nothing was resolved (no narration)
    rows: list[Finding] = field(default_factory=list)  # What this section's cues did, in order.

    @property
    def notes(self) -> list[str]:
        """The same rows as the sentences a table prints, so one row is never stored twice."""
        return [r.text for r in self.rows]

    def note(self, cue: str | None, verdict: Verdict | None, detail: str) -> None:
        """Record what one cue did, as the row a table and a payload both read."""
        self.rows.append(Finding(detail=detail, verdict=verdict or Verdict.NOTE, section=int(self.key), cue=cue))

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "speech_end": round(self.speech_end, 3),
            "min_seconds": self.min_seconds,
            "skipped": self.skipped,
            "cues": {r.cue: r.at for r in self.resolved},
            "notes": [r.to_dict() for r in self.rows],
        }


@dataclass
class AlignResult:
    """Every cue this run resolved, and every one it could not."""

    cue_times: CueTimes
    sections: list[SectionCueTimes]
    unresolved: int
    estimated: bool
    unknown: int = 0  # Cue ids that appear nowhere in the page that plays them.
    cue_times_file: Path | None = None
    allow_unknown_cues: bool = False  # The run was told to carry on past an unknown cue id.

    @property
    def short(self) -> int:
        """Sections whose speech ends before the min_seconds their visuals need."""
        return sum(
            1 for s in self.sections if not s.skipped and s.min_seconds is not None and s.speech_end < s.min_seconds
        )

    @property
    def findings(self) -> Findings:
        """Certain: an unresolved phrase and an unknown cue id. Uncertain: a section too short for its visuals."""
        unknown = 0 if self.allow_unknown_cues else self.unknown
        return Findings(certain=self.unresolved + unknown, uncertain=self.short)

    @property
    def problems(self) -> list[str]:
        return [f"section {s.key}: {n}" for s in self.sections for n in s.notes]

    @property
    def unknown_problems(self) -> list[str]:
        return [f"section {s.key}: {r.text}" for s in self.sections for r in s.rows if r.verdict == Verdict.UNKNOWN_CUE]

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The result as JSON-ready data, with the cue times file relative to the project root."""
        cue_times_file = relative(self.cue_times_file, root) if self.cue_times_file is not None else None
        return {
            "estimated": self.estimated,
            "cue_times_file": cue_times_file,
            "unresolved": self.unresolved,
            "unknown": self.unknown,
            "sections": [s.to_dict() for s in self.sections],
        }


def unknown_message(result: AlignResult) -> str:
    """Why the build stops on unknown cue ids, with the first one as the example fix."""
    first = next(r for s in result.sections for r in s.rows if r.verdict == Verdict.UNKNOWN_CUE)
    page = first.detail.removeprefix("not in ")
    return (
        f"{result.unknown} cue id(s) in cues.json appear nowhere in the page that plays them, so the page would "
        f'never reveal them. Add data-cue="{first.cue}" to the slide in {page}, fix the id in cues.json, or pass '
        "--allow-unknown-cues:\n  " + "\n  ".join(result.unknown_problems)
    )


class UnknownCueError(ConfigError):
    """Cue ids that no page mentions. The result is attached, so a caller can still print its table."""

    def __init__(self, result: AlignResult) -> None:
        super().__init__(unknown_message(result))
        self.result = result


def align(project: Project, *, allow_unknown_cues: bool = False) -> AlignResult:
    """Resolve every cue phrase, write cue-times.json, and report the cue ids that their page never mentions.

    cue-times.json is written either way. Unknown cue ids then raise UnknownCueError, which
    carries the result, unless allow_unknown_cues is set.
    """
    takes = project.takes()
    if takes is None:
        raise MissingInputError(
            f"{project.takes_path} not found. Run `decktalk narrate` (or `decktalk narrate --no-voice`) first."
        )
    specs = project.cue_specs()
    if not specs:
        log.info("no cues file at %s; pages will run their built-in timing", project.cues)
    unknown_ids = unknown_cue_ids(project, specs)
    take_words: dict[str, tuple[list[Word], float]] = {}
    for spec in specs:
        key = f"{spec.number:02d}"
        entry = takes.sections.get(key)
        if entry is not None:
            # Cue times count from the section start, which comes lead_seconds before the take.
            take_words[key] = (
                project.section_words(key, entry.words_file),
                entry.duration_seconds + project.lead_seconds(key),
            )
    cue_times, rows, unresolved = resolve_sections(
        specs, take_words, unknown_ids=unknown_ids, estimated=takes.estimated
    )
    cue_times.save(project.cue_times_path)
    log.info(
        "wrote %s (%d sections with cues; %d unresolved, %d unknown)",
        project.cue_times_path,
        len(cue_times.sections),
        unresolved,
        len(unknown_ids),
    )
    result = AlignResult(
        cue_times=cue_times,
        sections=rows,
        unresolved=unresolved,
        estimated=takes.estimated,
        unknown=len(unknown_ids),
        cue_times_file=project.cue_times_path,
        allow_unknown_cues=allow_unknown_cues,
    )
    if result.unknown and not allow_unknown_cues:
        raise UnknownCueError(result)
    return result


def resolve_sections(
    specs: list[SectionCues],
    take_words: dict[str, tuple[list[Word], float]],
    *,
    unknown_ids: list[tuple[str, str, str]],
    estimated: bool,
) -> tuple[CueTimes, list[SectionCueTimes], int]:
    """(cue_times, one row per section, unresolved count) for cues against each section's take.

    `take_words` maps a section key to its words and its length, both in seconds after the section
    starts. A section with no take is skipped. `estimated` names the take index kind in a no-words note.
    Nothing is written.
    """
    cue_times = CueTimes(estimated=estimated)
    rows: list[SectionCueTimes] = []
    unresolved = 0
    for spec in specs:
        key = f"{spec.number:02d}"
        take = take_words.get(key)
        if take is None:
            rows.append(
                SectionCueTimes(
                    key=key,
                    speech_end=0.0,
                    min_seconds=spec.min_seconds,
                    resolved=[],
                    skipped="no narration (clip section, or not rendered)",
                )
            )
            for k, cue_id, page in unknown_ids:
                if k == key:
                    rows[-1].note(cue_id, Verdict.UNKNOWN_CUE, f"not in {page}")
            continue
        words, length = take
        speech_end = words[-1].end if words else length
        row = SectionCueTimes(key=key, speech_end=speech_end, min_seconds=spec.min_seconds, resolved=[])
        for cue in spec.cues:
            if not words and cue.on != "$start":
                row.note(
                    cue.cue,
                    Verdict.UNRESOLVED,
                    f"no words ({'estimated takes' if estimated else 'missing words file'})",
                )
                unresolved += 1
                continue
            t = resolve_cue(cue, words)
            if t is None:
                row.note(cue.cue, Verdict.UNRESOLVED, f"phrase not found: {cue.on!r}")
                unresolved += 1
                continue
            if t > length:
                row.note(cue.cue, None, f"{t}s is past the end of the audio ({round(length, 3)}s)")
            anchor = anchor_time(cue, words)
            word_at = None if anchor is None else round(anchor, 2)
            row.resolved.append(CueTime(cue=cue.cue, on=cue.on, at=t, word_at=word_at))
            ambiguous = ambiguity_note(cue, words)
            if ambiguous:
                row.note(cue.cue, None, ambiguous)
        if spec.min_seconds is not None and speech_end < spec.min_seconds:
            short = spec.min_seconds - speech_end
            row.note(None, None, f"speech {speech_end:.1f}s is {short:.1f}s shorter than the visuals need")
        for k, cue_id, page in unknown_ids:
            if k == key:
                row.note(cue_id, Verdict.UNKNOWN_CUE, f"not in {page}")
        if row.resolved:
            cue_times.sections[key] = row.resolved
        rows.append(row)
    return cue_times, rows, unresolved
