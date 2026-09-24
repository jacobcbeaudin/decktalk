"""Where a key sits in a TOML file, found by reading the text rather than by parsing it.

A refusal that names a file and not a line makes a reader search for the value DeckTalk refused,
and no TOML reader in the language carries a line number out of a parsed document. The scan below
is the whole of what a located refusal needs: it walks the lines once, remembers the table header
it is inside, and answers with the line a dotted key was written on. It is deliberately not a
parser, so a file it cannot make sense of costs a null rather than a second error on top of the
first.
"""

from __future__ import annotations

import re
import tomllib

TABLE = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*(?:#.*)?$")
"""A table header, whose one group is the dotted path inside the brackets, arrays of tables too."""

ASSIGNMENT = re.compile(r"^\s*((?:[A-Za-z0-9_\-]+|\"[^\"]*\")(?:\s*\.\s*(?:[A-Za-z0-9_\-]+|\"[^\"]*\"))*)\s*=")
"""One assignment, whose group is the key as written, which may itself be dotted."""


def _path(written: str) -> tuple[str, ...]:
    """A dotted path as written, with the quotes and the spacing around each dot removed."""
    return tuple(part.strip().strip('"') for part in written.split("."))


def locate(text: str, key: str) -> int | None:
    """The one-based line `key` is written on in this text, or null when the text does not set it.

    The key is dotted from the top of the file, such as `verify.cue_offset_max_ms`, which is the
    same string a diagnostic prints and `config set` takes. A key written inside its table and a
    key written dotted at the top of the file are the same key and both are found.
    """
    wanted = _path(key)
    here: tuple[str, ...] = ()
    for number, line in enumerate(text.splitlines(), start=1):
        header = TABLE.match(line)
        if header:
            here = _path(header.group(1))
            continue
        assignment = ASSIGNMENT.match(line)
        if assignment and here + _path(assignment.group(1)) == wanted:
            return number
    return None


def locate_table(text: str, table: str) -> int | None:
    """The one-based line the header of `table` is written on, or null when the text has no such header."""
    wanted = _path(table)
    for number, line in enumerate(text.splitlines(), start=1):
        header = TABLE.match(line)
        if header and _path(header.group(1)) == wanted:
            return number
    return None


def refused_line(error: tomllib.TOMLDecodeError) -> int | None:
    """The line a TOML parser refused, read from the attribute it carries or from its own sentence.

    The attribute is there on the Python versions that publish it, and the sentence is the fallback
    on the ones that do not, so a malformed file opens at the right line on every supported runtime.
    """
    numbered = getattr(error, "lineno", None)
    if isinstance(numbered, int):
        return numbered
    named = re.search(r"at line (\d+)", str(error))
    return int(named.group(1)) if named else None


__all__ = ["locate", "locate_table", "refused_line"]
