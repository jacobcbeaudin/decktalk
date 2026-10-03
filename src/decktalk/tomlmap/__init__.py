"""One loader from a mapping to typed values, with located errors and "did you mean" hints.

Every file a project owns is a mapping once it is parsed: `decktalk.toml`, `cues.json` and
`media/markers.json`. `Table` reads one of those mappings with the file, the table and the line
named in every message, so a bad value fails at load rather than deep inside ffmpeg.
`from_mapping` builds a whole dataclass tree instead, reading the names, the types, the defaults
and the published record from the fields, which is how a tuning table is written once and read by
the loader, by the schema, by the reference page and by `config explain`.

`tune()` is the one way a key is declared and `Bounds` is the one way a range is stated. Both are
declarative: `Bounds` carries numbers, word sets and patterns rather than a function, so the same
range the loader enforces is the range the JSON Schema publishes, and a reader of either can never
meet a bound the other does not have.

    __init__.py   `tune()`, `Key`, `Bounds` and the registry a dataclass tree publishes
    read.py       `Table`, `from_mapping` and `read_value`, which read a mapping into typed values
    suggest.py    the warning for a key nobody reads, with the key it most likely meant
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, cast, get_type_hints

from decktalk.findings import Code
from decktalk.results import Nature, Scope, Source

ENV_PREFIX = "DECKTALK_"
"""What every variable DeckTalk reads starts with, which is the one place the prefix is spelled."""


def variable(dotted: str) -> str:
    """The environment variable that sets the key `dotted`, which is its id in upper case under the prefix."""
    return ENV_PREFIX + dotted.replace(".", "_").upper()


@dataclass(frozen=True)
class Bounds:
    """The range a value must sit inside, stated as data so the loader and the schema agree.

    The published range is the safe range. A bound is here because a value past it deletes a check,
    corrupts the evidence a later stage measures, or breaks a tool, never because the type would
    admit anything wider. The wider range the type admits is a record on the key and is not
    enforced, so a reader can see what was given up and why.
    """

    ge: float | None = None
    gt: float | None = None
    le: float | None = None
    enum: tuple[Any, ...] | None = None
    items: Bounds | None = None
    min_items: int | None = None
    pattern: str | None = None
    """A regular expression the whole of a string must match, for a word whose spellings no set can list."""

    def holds(self, value: Any) -> bool:
        """True when this value sits inside the range, arrays being judged element by element."""
        if isinstance(value, (tuple, list)):
            if self.min_items is not None and len(value) < self.min_items:
                return False
            return all(self.items is None or self.items.holds(item) for item in value)
        if self.enum is not None and value not in self.enum:
            return False
        # The whole string must match, so a value that is a colour followed by a newline and more is refused.
        if self.pattern is not None and not (isinstance(value, str) and re.fullmatch(self.pattern, value)):
            return False
        if self.ge is not None and value < self.ge:
            return False
        if self.gt is not None and value <= self.gt:
            return False
        return not (self.le is not None and value > self.le)

    @property
    def sentence(self) -> str:
        """The range in words, which is what a refusal prints after the key's name."""
        if self.enum is not None:
            return "must be one of " + ", ".join(str(v) for v in self.enum)
        parts: list[str] = []
        if self.pattern is not None:
            parts.append(f"must match the pattern {self.pattern}")
        if self.min_items is not None:
            parts.append(f"must hold at least {self.min_items}")
        if self.items is not None:
            parts.append("must hold values that each " + self.items.sentence)
        if self.ge is not None and self.le is not None:
            parts.append(f"must be between {_number(self.ge)} and {_number(self.le)}")
        else:
            if self.ge is not None:
                parts.append(f"must be at least {_number(self.ge)}")
            if self.gt is not None:
                parts.append(f"must be above {_number(self.gt)}")
            if self.le is not None:
                parts.append(f"must be at most {_number(self.le)}")
        return " and ".join(parts) if parts else "takes any value of its type"

    def json_schema(self) -> dict[str, Any]:
        """This range as JSON Schema keywords, which is the whole reason it is data and not a function."""
        out: dict[str, Any] = {}
        if self.enum is not None:
            out["enum"] = list(self.enum)
        if self.ge is not None:
            out["minimum"] = self.ge
        if self.gt is not None:
            out["exclusiveMinimum"] = self.gt
        if self.le is not None:
            out["maximum"] = self.le
        if self.pattern is not None:
            out["pattern"] = self.pattern
        if self.min_items is not None:
            out["minItems"] = self.min_items
        if self.items is not None:
            out["items"] = self.items.json_schema()
        return out


def _number(value: float) -> str:
    """One bound as the range prints it, which drops the decimal point a whole number does not need."""
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


A_PERCENT = Bounds(ge=0, le=100)
A_SHARE = Bounds(ge=0, le=1)
A_LUMA = Bounds(ge=0, le=255)


@dataclass(frozen=True)
class Key:
    """One settings key as every surface publishes it, which is the whole record an agent reads.

    The record answers the four questions an agent asks in order: what does this change, what may
    I write, what happens if I go to the edge, and which failure does it move. Nothing about a key
    is stated anywhere else, so the schema, the reference page, `config explain` and a finding that
    names a setting are four renderings of this one row.
    """

    id: str
    description: str
    default: Any
    annotation: Any
    bounds: Bounds | None = None
    typed: Bounds | None = None
    unit: str | None = None
    scope: Scope = Scope.PROJECT
    nature: Nature = Nature.TASTE
    source: Source = Source.CHOSEN
    evidence: str | None = None
    hazard: str | None = None
    requires: str | None = None
    see_also: tuple[str, ...] = ()
    decides: tuple[Code, ...] = ()

    @property
    def table(self) -> str:
        """The table this key sits in, which is every segment of the id but the last."""
        return self.id.rsplit(".", 1)[0] if "." in self.id else ""

    @property
    def name(self) -> str:
        """The key's own name inside its table, which is the last segment of the id."""
        return self.id.rsplit(".", 1)[-1]

    @property
    def environment(self) -> str:
        """The environment variable that sets this key, which is its id in upper case under the prefix."""
        return variable(self.id)

    @property
    def enum(self) -> tuple[Any, ...] | None:
        """The closed set of values this key takes, or null when its range is a span rather than a set."""
        return self.bounds.enum if self.bounds else None

    @property
    def range(self) -> str:
        """The safe range in words, which is the range the loader enforces and the schema publishes."""
        return self.bounds.sentence if self.bounds else "takes any value of its type"


PUBLISHED = (
    "id",
    "description",
    "default",
    "typed",
    "bounds",
    "enum",
    "unit",
    "decides",
    "scope",
    "nature",
    "source",
    "evidence",
    "hazard",
    "requires",
    "see_also",
    "environment",
)
"""Every field of a key that reaches a reader, named once so a test can hold the surfaces to it.

Each is here because an agent behaviour needs it, and a field nothing reads is a field that lies
about being part of the instruction set.
"""


def tune[T](
    default: T,
    doc: str,
    *,
    unit: str | None = None,
    scope: Scope = Scope.PROJECT,
    nature: Nature = Nature.TASTE,
    bounds: Bounds | None = None,
    typed: Bounds | None = None,
    decides: tuple[Code, ...] = (),
    see_also: tuple[str, ...] = (),
    hazard: str | None = None,
    requires: str | None = None,
    source: Source = Source.CHOSEN,
    evidence: str | None = None,
) -> T:
    """One tunable key, declared once, with everything any surface publishes about it.

    The record lives beside the default because every other spelling of a key is generated from it:
    the loader reads `bounds`, the schema reads all of it, the reference page renders it and a
    finding that names a setting quotes `decides` in reverse. A key declared here and nowhere else
    cannot drift from the value the code actually reads. The metadata is named after `Key`'s own
    fields, so `registry` builds each key from it whole.
    """
    return field(
        default=default,
        metadata={
            "description": doc,
            "unit": unit,
            "scope": scope,
            "nature": nature,
            "bounds": bounds,
            "typed": typed,
            "decides": decides,
            "see_also": see_also,
            "hazard": hazard,
            "requires": requires,
            "source": source,
            "evidence": evidence,
        },
    )


def registry(cls: type[Any], *, prefix: tuple[str, ...] = ()) -> tuple[Key, ...]:
    """Every key of a dataclass tree, in declaration order, with its dotted id.

    The walk is the same recursion `from_mapping` makes, so a nested table is published, loaded and
    named from one reading of the fields and a table added later needs no second edit anywhere.
    """
    out: list[Key] = []
    hints = get_type_hints(cls)
    for f in fields(cast("Any", cls)):
        annotation = hints.get(f.name, f.type)
        if is_dataclass(annotation):
            out += registry(cast("type[Any]", annotation), prefix=(*prefix, f.name))
            continue
        out.append(Key(id=".".join((*prefix, f.name)), default=f.default, annotation=annotation, **f.metadata))
    return tuple(out)
