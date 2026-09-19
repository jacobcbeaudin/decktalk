"""`cues.json` parsed, and the phrase matching that resolves a cue against a section's words.

{"sections": {"3": {"min_seconds": 25,
                    "cues": [{"cue": "3.2", "on": "On a typical"},
                             {"cue": "3.x", "on": "Zero", "occurrence": 2, "case_sensitive": true},
                             {"cue": "15.2", "on": "$end", "offset": 0.3}]}}}

cue     a cue id the page understands (decktalk-runtime.js)
on      a word or short phrase from that section's narration: first occurrence,
        case-insensitive, punctuation ignored. "$start" = 0, "$end" = end of speech.
        Times count from the section start, so a section's lead_seconds moves every word
        cue later, and "$start" stays at 0.
occurrence / case_sensitive / offset (seconds) refine the match.
verify  false leaves the cue out of a plain `decktalk verify`, for a reveal too small
        or too slow for a frame difference to measure. The default is true.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields
from pathlib import Path

from ..artifacts import Word
from ..errors import ConfigError
from ..jsonio import read_json
from ..tomlmap import Table


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
class SectionCues:
    """One section's cues and the length its visuals need."""

    number: int
    cues: tuple[Cue, ...]
    min_seconds: float | None = None


def load_cues(path: Path, known: set[int]) -> list[SectionCues]:
    """Parsed and validated `cues.json`, which is [] when the file does not exist.

    `known` is every section number in `decktalk.toml`, so a cue for a section that is not there
    fails at load rather than resolving against nothing.
    """
    if not path.exists():
        return []
    try:
        data = read_json(path)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{path.name} is not valid JSON: {exc.msg}.",
            hint="Check the brackets and the commas on the line named here.",
            path=path,
            line=exc.lineno,
        ) from exc
    sections_raw = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections_raw, dict):
        raise ConfigError(
            f"{path.name} has no top-level 'sections' object.",
            hint='Wrap the sections in {"sections": {...}}.',
            path=path,
        )
    out: list[SectionCues] = []
    for num_raw, spec in sections_raw.items():
        try:
            number = int(num_raw)
        except ValueError as exc:
            raise ConfigError(
                f"{path.name} has the section key {num_raw!r}, which is not a number.",
                hint='Key each section by its number in decktalk.toml, such as "3".',
                path=path,
            ) from exc
        if number not in known:
            raise ConfigError(
                f"{path.name} names section {number}, which is not in decktalk.toml.",
                hint="Add a [[section]] with that number, or drop the cues written for it.",
                path=path,
            )
        if not isinstance(spec, dict):
            raise ConfigError(
                f"{path.name} gives section {number} a value that is not an object.",
                hint='Write the section as {"cues": [...]}.',
                path=path,
            )
        section = Table(spec, f"{path.name}: section {number}")
        cues = [
            parse_cue(raw, f"{path.name}: section {number}, cue #{i + 1}", path)
            for i, raw in enumerate(section.get_tables("cues"))
        ]
        out.append(SectionCues(number=number, cues=tuple(cues), min_seconds=section.get_num("min_seconds")))
    return sorted(out, key=lambda s: s.number)


def parse_cue(raw: dict[str, object], where: str, path: Path | None = None) -> Cue:
    """One cue row, refusing a key this file does not read so that a typo cannot move a cue in silence."""
    unknown = sorted(set(raw) - CUE_KEYS)
    if unknown:
        raise ConfigError(
            f"{where}: {unknown[0]!r} is not a key of a cue.",
            hint=f"The keys of a cue are {', '.join(sorted(CUE_KEYS))}.",
            path=path,
        )
    t = Table(raw, where)
    cue_id = t.get_str("cue", required=True)
    on = t.get_str("on", required=True)
    for key, value in (("cue", cue_id), ("on", on)):
        if not value:
            raise ConfigError(f"{where}: '{key}' must not be empty", path=path)
    return Cue(
        cue=cue_id,
        on=on,
        occurrence=t.get_int("occurrence", 1),
        case_sensitive=t.get_bool("case_sensitive"),
        offset=t.get_num("offset", 0.0),
        verify=t.get_bool("verify", True),
        occurrence_set="occurrence" in raw,
    )


def page_mentions(html: str, cue_id: str) -> bool:
    """Whether the page names the cue id as a quoted literal, as in data-cue="ID", a cues key, or a handler key."""
    return re.search(r"([\"'`])" + re.escape(cue_id) + r"\1", html) is not None


def norm(token: str, case_sensitive: bool = False) -> str:
    """One word with its punctuation dropped, as the matcher compares it."""
    token = re.sub(r"[^0-9A-Za-z']", "", token)
    return token if case_sensitive else token.lower()


def phrase_matches(words: list[Word], phrase: str, case_sensitive: bool = False) -> list[int]:
    """Index of the first word of every occurrence of phrase, in order."""
    target = [t for t in (norm(t, case_sensitive) for t in phrase.split()) if t]
    if not target:
        return []
    normalized = [norm(w.word, case_sensitive) for w in words]
    return [i for i in range(len(normalized) - len(target) + 1) if normalized[i : i + len(target)] == target]


def find_phrase(words: list[Word], phrase: str, occurrence: int = 1, case_sensitive: bool = False) -> int | None:
    """Index of the first word of the n-th occurrence of phrase, or None."""
    matches = phrase_matches(words, phrase, case_sensitive)
    return matches[occurrence - 1] if 1 <= occurrence <= len(matches) else None
