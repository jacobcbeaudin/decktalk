"""`cues.json` parsed, and the phrase matching that resolves a cue against a section's words.

    {"sections": {"3": {"min_seconds": 25,
                        "cues": [{"cue": "3.2:expand", "on": "On a typical"},
                                 {"cue": "3.2:zero", "on": "Zero", "occurrence": 2},
                                 {"cue": "3.4:end", "on": "$end", "offset": 0.3}]}}}

`cue` is the wire id of a moment the page declares, which is its slide and the local name the slide
wrote. `on` is a word or a short phrase from that section's narration, matched on its first
occurrence, without case and with punctuation ignored, and `$start` and `$end` name the section's
own two ends. A time counts from the section start, so a section's lead moves every word cue later
and `$start` stays at zero. `occurrence`, `case_sensitive` and `offset` refine one match, and
`verify` set to false leaves the cue out of the measurement, for a reveal too small or too slow for
a frame difference to see.

The page owns what a moment looks like and the project file owns when it happens, which is why the
seconds are never written on the page and the phrase is never written in the markup.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from decktalk.errors import InputError
from decktalk.findings import Location
from decktalk.inputs.paths import at
from decktalk.results import Word
from decktalk.tomlmap import Table


@dataclass(frozen=True)
class Cue:
    """One row of `cues.json`: which spoken phrase a visual lands on."""

    cue: str
    on: str
    occurrence: int = 1
    case_sensitive: bool = False
    offset: float = 0.0
    verify: bool = True  # False leaves the cue out of a plain `decktalk verify`.
    occurrence_set: bool = False  # cues.json names the occurrence, so a repeated phrase is not ambiguous.


# Every key a cue row may hold, which is every field of `Cue` but `occurrence_set`, this module's own and
# never written by an author, and `_comment`, the one key a row may carry that DeckTalk reads nothing from.
CUE_KEYS = {f.name for f in fields(Cue) if f.name != "occurrence_set"} | {"_comment"}


@dataclass(frozen=True)
class CuedSection:
    """One section's cues and the length its visuals need."""

    number: int
    cues: tuple[Cue, ...]
    min_seconds: float | None = None


def load_cues(path: Path, root: Path, known: set[int]) -> tuple[CuedSection, ...]:
    """Parsed and validated `cues.json`, which is empty when the file does not exist.

    `known` is every section number in `decktalk.toml`, so a cue for a section that is not there
    fails at load rather than resolving against nothing.
    """
    if not path.exists():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(
            f"{path.name} is not valid JSON: {exc.msg}.",
            hint="Check the brackets and the commas on the line named here.",
            location=at(path, root, line=exc.lineno),
        ) from exc
    sections_raw = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections_raw, dict):
        raise InputError(
            f"{path.name} has no top-level 'sections' object.",
            hint='Wrap the sections in {"sections": {...}}.',
            location=at(path, root),
        )
    out: list[CuedSection] = []
    for num_raw, spec in sections_raw.items():
        try:
            number = int(num_raw)
        except ValueError as exc:
            raise InputError(
                f"{path.name} has the section key {num_raw!r}, which is not a number.",
                hint='Key each section by its number in decktalk.toml, such as "3".',
                location=at(path, root),
            ) from exc
        if number not in known:
            raise InputError(
                f"{path.name} names section {number}, which is not in decktalk.toml.",
                hint="Add a [[section]] with that number, or drop the cues written for it.",
                location=at(path, root),
            )
        if not isinstance(spec, dict):
            raise InputError(
                f"{path.name} gives section {number} a value that is not an object.",
                hint='Write the section as {"cues": [...]}.',
                location=at(path, root),
            )
        section = Table(spec, f"{path.name}: section {number}")
        cues = [
            parse_cue(raw, f"{path.name}: section {number}, cue #{i + 1}", at(path, root))
            for i, raw in enumerate(section.get_tables("cues"))
        ]
        out.append(CuedSection(number=number, cues=tuple(cues), min_seconds=section.get_num("min_seconds")))
    return tuple(sorted(out, key=lambda s: s.number))


def parse_cue(raw: dict[str, object], where: str, location: Location | None = None) -> Cue:
    """One cue row, refusing a key this file does not read so that a typo cannot move a cue in silence.

    An `on` that is there and empty is a row a fix scaffolded and nobody has written the phrase into
    yet, so it loads and `CUE_UNRESOLVED` judges it. A refusal here would mean the file a fix just
    wrote could not be read by the command run straight after it.
    """
    unknown = sorted(set(raw) - CUE_KEYS)
    if unknown:
        raise InputError(
            f"{where}: {unknown[0]!r} is not a key of a cue.",
            hint=f"The keys of a cue are {', '.join(sorted(CUE_KEYS))}.",
            location=location,
        )
    t = Table(raw, where)
    cue_id = t.get_str("cue", required=True)
    on = t.get_str("on", required=True)
    if not cue_id:
        raise InputError(f"{where}: 'cue' must not be empty", location=location)
    return Cue(
        cue=cue_id,
        on=on,
        occurrence=t.get_int("occurrence", 1),
        case_sensitive=t.get_bool("case_sensitive"),
        offset=t.get_num("offset", 0.0),
        verify=t.get_bool("verify", True),
        occurrence_set="occurrence" in raw,
    )


def norm(token: str, case_sensitive: bool = False) -> str:
    """One word with its punctuation dropped, as the matcher compares it."""
    token = re.sub(r"[^0-9A-Za-z']", "", token)
    return token if case_sensitive else token.lower()


def phrase_matches(words: Sequence[Word], phrase: str, case_sensitive: bool = False) -> list[int]:
    """Index of the first word of every occurrence of phrase, in order."""
    target = [t for t in (norm(t, case_sensitive) for t in phrase.split()) if t]
    if not target:
        return []
    normalized = [norm(w.word, case_sensitive) for w in words]
    return [i for i in range(len(normalized) - len(target) + 1) if normalized[i : i + len(target)] == target]


def find_phrase(words: Sequence[Word], phrase: str, occurrence: int = 1, case_sensitive: bool = False) -> int | None:
    """Index of the first word of the n-th occurrence of phrase, or None."""
    matches = phrase_matches(words, phrase, case_sensitive)
    return matches[occurrence - 1] if 1 <= occurrence <= len(matches) else None
