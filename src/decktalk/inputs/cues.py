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
from dataclasses import dataclass, fields, replace
from pathlib import Path

from decktalk.errors import InputError
from decktalk.findings import Location
from decktalk.inputs.paths import at
from decktalk.results import Word
from decktalk.tomlmap import Table, default_of

SECTION_START = "$start"
"""The phrase that anchors a cue or a marker to its section's own beginning rather than to a spoken word."""

SECTION_END = "$end"
"""The phrase that anchors a cue or a marker to the end of the last word its section speaks."""


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
    line: int | None = None  # The line of cues.json the row's phrase is written on, when it could be found.


READ_HERE = frozenset({"occurrence_set", "line"})
"""The fields of `Cue` this module works out for itself, which an author never writes in a row."""

# Every key a cue row may hold, which is every field of `Cue` but the ones this module works out, and
# `_comment`, the one key a row may carry that DeckTalk reads nothing from.
CUE_KEYS = {f.name for f in fields(Cue) if f.name not in READ_HERE} | {"_comment"}

PHRASE_KEY = re.compile(r'"on"\s*:\s*("(?:[^"\\]|\\.)*")')
"""Where a row writes its phrase in the file's own text, which is how the line of each row is found.

A quote inside a JSON string is escaped, so this spelling can only be the key of a row and never a
piece of some string's value.
"""


@dataclass(frozen=True)
class CuedSection:
    """One section's cues and the length its visuals need."""

    number: int
    cues: tuple[Cue, ...]
    min_seconds: float | None = None


def json_of(text: str, path: Path, root: Path) -> object:
    """The JSON a project file holds, refused with the line the parser stopped on when it is not valid JSON."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(
            f"{path.name} is not valid JSON: {exc.msg}.",
            hint="Check the brackets and the commas on the line named here.",
            location=at(path, root, line=exc.lineno),
        ) from exc


def load_cues(path: Path, root: Path, known: set[int]) -> tuple[CuedSection, ...]:
    """Parsed and validated `cues.json`, which is empty when the file does not exist.

    `known` is every section number in `decktalk.toml`, so a cue for a section that is not there
    fails at load rather than resolving against nothing.
    """
    if not path.exists():
        return ()
    text = path.read_text(encoding="utf-8")
    data = json_of(text, path, root)
    sections_raw = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections_raw, dict):
        raise InputError(
            f"{path.name} has no top-level 'sections' object.",
            hint='Wrap the sections in {"sections": {...}}.',
            location=at(path, root),
        )
    out: list[CuedSection] = []
    lines = iter(phrase_lines(text))
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
            _placed(parse_cue(raw, f"{path.name}: section {number}, cue #{i + 1}", at(path, root)), next(lines, None))
            for i, raw in enumerate(section.get_tables("cues"))
        ]
        out.append(CuedSection(number=number, cues=tuple(cues), min_seconds=section.get_num("min_seconds")))
    return tuple(sorted(out, key=lambda s: s.number))


def phrase_lines(text: str) -> list[tuple[str, int]]:
    """(the phrase, the line it is written on) for every row of a cue file, in the order the file writes them.

    JSON keeps the order of an object's members and of an array's items, so the rows the parser
    hands back come in exactly this order, and the n-th phrase here is the n-th row's.
    """
    found: list[tuple[str, int]] = []
    for match in PHRASE_KEY.finditer(text):
        try:
            phrase = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        found.append((phrase, text.count("\n", 0, match.start()) + 1))
    return found


def _placed(cue: Cue, written: tuple[str, int] | None) -> Cue:
    """The row with the line its phrase is written on, when the text agrees with what was parsed."""
    if written is None or written[0] != cue.on:
        return cue
    return replace(cue, line=written[1])


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
        occurrence=t.get_int("occurrence", default_of(Cue, "occurrence")),
        case_sensitive=t.get_bool("case_sensitive"),
        offset=t.get_num("offset", default_of(Cue, "offset")),
        verify=t.get_bool("verify", default_of(Cue, "verify")),
        occurrence_set="occurrence" in raw,
    )


UNMATCHED = re.compile(r"[^0-9A-Za-z']")
"""Every character the matcher ignores, which is everything but a letter, a digit and an apostrophe."""


def norm(token: str, case_sensitive: bool = False) -> str:
    """One word with its punctuation dropped, as the matcher compares it."""
    token = UNMATCHED.sub("", token)
    return token if case_sensitive else token.lower()


@dataclass(frozen=True)
class Spoken:
    """One section's words, with the form the matcher compares each of them in worked out once.

    A section is matched against once per cue, and a long section with many cues would otherwise
    normalise every one of its words again for each of them, so the two forms are made here when the
    words are read and every phrase is matched against these.
    """

    words: tuple[Word, ...]
    folded: tuple[str, ...]
    """Every word as a match without case compares it."""

    exact: tuple[str, ...]
    """Every word as a case-sensitive match compares it."""

    @classmethod
    def of(cls, words: Sequence[Word]) -> Spoken:
        """These words with both of their matched forms worked out once."""
        exact = tuple(norm(word.word, case_sensitive=True) for word in words)
        return cls(words=tuple(words), folded=tuple(one.lower() for one in exact), exact=exact)

    def matches(self, phrase: str, case_sensitive: bool = False) -> list[int]:
        """Index of the first word of every occurrence of phrase, in order."""
        target = [t for t in (norm(t, case_sensitive) for t in phrase.split()) if t]
        if not target:
            return []
        said, width = self.exact if case_sensitive else self.folded, len(target)
        return [i for i in range(len(said) - width + 1) if list(said[i : i + width]) == target]

    def find(self, phrase: str, occurrence: int = 1, case_sensitive: bool = False) -> int | None:
        """Index of the first word of the n-th occurrence of phrase, or None."""
        found = self.matches(phrase, case_sensitive)
        return found[occurrence - 1] if 1 <= occurrence <= len(found) else None
