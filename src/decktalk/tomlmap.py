"""One loader from a mapping to typed values, with located errors and "did you mean" hints.

Every file a project owns is a mapping once it is parsed: `decktalk.toml`, `cues.json` and
`media/markers.json`. `Table` reads one of those mappings with the file, the table and the line
named in every message, so a bad value fails at load rather than deep inside ffmpeg.
`from_mapping` builds a whole dataclass tree instead, reading the names, the types, the defaults
and the published record from the fields, which is how a tuning table is written once and read by
the loader, by the schema, by the reference page and by `config explain`.

`tune()` is the one way a key is declared and `Bounds` is the one way a range is stated. Both are
declarative: `Bounds` carries numbers, word sets and patterns rather than a function, so the same range the
loader enforces is the range the JSON Schema publishes, and a reader of either can never meet a
bound the other does not have.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints, overload

from .errors import InputError
from .findings import Code, Location
from .locate import locate
from .results import Scope


class Nature(Enum):
    """The four-way test every number takes, which decides whether it can be a key at all.

    A number is a key when a project could hold another value for a reason a sentence can state.
    Taste and apparatus are the two answers that make one, and truth and derived are the two that
    make a published number instead, so an agent that cannot find a knob learns the number is
    deliberately not one rather than proposing a setting that cannot exist. Calibration is the
    fifth answer and belongs to a published number alone: it is a fact measured once from a tool
    DeckTalk drives, so it is neither a standard nor arithmetic and no project may state it.
    """

    TASTE = "taste"
    APPARATUS = "apparatus"
    TRUTH = "truth"
    DERIVED = "derived"
    CALIBRATION = "calibration"


class Source(Enum):
    """Where the value in force is expected to come from, which decides who may write it.

    A stated key is one DeckTalk cannot know and the operator must supply, such as a price, which is
    why it names the evidence the operator reads it from.
    """

    CHOSEN = "chosen"
    STATED = "stated"


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
    names a knob are four renderings of this one row.
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
        return "DECKTALK_" + self.id.replace(".", "_").upper()

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
    finding that names a knob quotes `decides` in reverse. A key declared here and nowhere else
    cannot drift from the value the code actually reads.
    """
    return field(
        default=default,
        metadata={
            "doc": doc,
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
        out.append(
            Key(
                id=".".join((*prefix, f.name)),
                description=str(f.metadata.get("doc", "")),
                default=f.default,
                annotation=annotation,
                bounds=f.metadata.get("bounds"),
                typed=f.metadata.get("typed"),
                unit=f.metadata.get("unit"),
                scope=f.metadata.get("scope", Scope.PROJECT),
                nature=f.metadata.get("nature", Nature.TASTE),
                source=f.metadata.get("source", Source.CHOSEN),
                evidence=f.metadata.get("evidence"),
                hazard=f.metadata.get("hazard"),
                requires=f.metadata.get("requires"),
                see_also=tuple(f.metadata.get("see_also", ())),
                decides=tuple(f.metadata.get("decides", ())),
            )
        )
    return tuple(out)


def unknown_key_message(key: str, known: Iterable[str], where: str) -> str:
    """The warning for one key that DeckTalk does not read, with the closest known key when one is near."""
    close = difflib.get_close_matches(key, sorted(known), n=1)
    hint = f" (did you mean '{close[0]}'?)" if close else ""
    return f"{where}: ignoring unknown key '{key}'{hint}."


def unknown_key_warnings(table: Mapping[str, Any], known: Iterable[str], where: str) -> list[str]:
    """One warning per key in `table` that is not in `known`. An unknown key is ignored, not an error."""
    names = set(known)
    return [unknown_key_message(key, names, where) for key in sorted(set(table) - names)]


NAMED_PART_MIN = 3
"""How long the last part of a mistyped key must be before a key whose own last part holds it is offered.

A shorter part, such as `db` or `a`, sits inside too many names to say which one was meant.
"""


def did_you_mean(key: str, known: Iterable[str]) -> str:
    """The key one nobody knows most likely meant, as a clause a refusal appends, or nothing when none is near.

    The last part of a key names the thing it sets, and a person who remembers that thing and not
    its table writes it under the wrong one, as `record.fps` for `video.output_fps`. So a key whose
    last part holds the typed one is offered first, the closest spelling among them when there are
    several, and the closest spelling of the whole key is offered only when no such key exists.
    """
    names = sorted(known)
    part = key.rsplit(".", 1)[-1]
    holding = [name for name in names if len(part) >= NAMED_PART_MIN and part in name.rsplit(".", 1)[-1]]
    close = difflib.get_close_matches(key, holding, n=1, cutoff=0) if holding else []
    close = close or difflib.get_close_matches(key, names, n=1)
    return f" Did you mean '{close[0]}'?" if close else ""


class Table:
    """Typed access to one mapping, with error messages that name the file, the table and the line."""

    def __init__(
        self,
        data: Mapping[str, Any],
        where: str,
        path: Path | None = None,
        *,
        text: str = "",
        table: str = "",
    ) -> None:
        self.data = data
        self.where = where
        # The file this table was read from, so a refusal fills the error's own path slot. A caller
        # that has no file, such as a table built from an environment, leaves it unset.
        self.path = path
        # The file's own text, so a refusal carries the line the key sits on. A caller that has the
        # mapping and not the text still gets the file, which is what the scan degrades to.
        self.text = text
        # The dotted name of this table inside the file, which a key's own name is not enough to
        # find, because the same key name sits in several tables.
        self.table = table

    def _refuse(self, message: str, key: str, hint: str | None = None) -> InputError:
        dotted = f"{self.table}.{key}" if self.table else key
        line = locate(self.text, dotted) if self.text else None
        location = Location(where=f"{self.where} {key}", file=self.path, line=line) if self.path else None
        return InputError(message, hint=hint, location=location)

    def _get(self, key: str, kind: type | tuple[type, ...], default: Any, required: bool) -> Any:
        if key not in self.data:
            if required:
                raise self._refuse(
                    f"{self.where}: '{key}' is required and is not there.",
                    key,
                    hint=f"Add '{key}' to this table.",
                )
            return default
        value = self.data[key]
        if isinstance(value, bool) and kind in (int, float, (int, float)):
            raise self._refuse(f"{self.where}: '{key}' must be a number, got a boolean", key)
        if not isinstance(value, kind):
            names = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
            raise self._refuse(f"{self.where}: '{key}' must be {names}, got {type(value).__name__}", key)
        return value

    # A key with a default, and a required key, always have a value. Only an optional key with no
    # default can be None, so a caller never has to narrow a type the mapping already guarantees.
    @overload
    def get_str(self, key: str, default: None = None, *, required: Literal[True]) -> str: ...
    @overload
    def get_str(self, key: str, default: str, *, required: bool = False) -> str: ...
    @overload
    def get_str(self, key: str, default: None = None, *, required: bool = False) -> str | None: ...

    def get_str(self, key: str, default: str | None = None, *, required: bool = False) -> str | None:
        return cast("str | None", self._get(key, str, default, required))

    @overload
    def get_num(self, key: str, default: None = None, *, required: Literal[True]) -> float: ...
    @overload
    def get_num(self, key: str, default: float, *, required: bool = False) -> float: ...
    @overload
    def get_num(self, key: str, default: None = None, *, required: bool = False) -> float | None: ...

    def get_num(self, key: str, default: float | None = None, *, required: bool = False) -> float | None:
        value = self._get(key, (int, float), default, required)
        return None if value is None else float(value)

    @overload
    def get_int(self, key: str, default: None = None, *, required: Literal[True]) -> int: ...
    @overload
    def get_int(self, key: str, default: int, *, required: bool = False) -> int: ...
    @overload
    def get_int(self, key: str, default: None = None, *, required: bool = False) -> int | None: ...

    def get_int(self, key: str, default: int | None = None, *, required: bool = False) -> int | None:
        return cast("int | None", self._get(key, int, default, required))

    @overload
    def get_path(self, key: str, default: str, *, required: bool = False) -> str: ...
    @overload
    def get_path(self, key: str, default: None = None, *, required: Literal[True]) -> str: ...
    @overload
    def get_path(self, key: str, default: None = None, *, required: bool = False) -> str | None: ...

    def get_path(self, key: str, default: str | None = None, *, required: bool = False) -> str | None:
        """One key whose value is a path inside the project, refused when it names somewhere else.

        Every path DeckTalk reports is relative to the project root, so a key that points outside it
        would put a path from another part of the machine into a payload a reader relays, and would
        read or write a file the project does not own. A path key is read through this method rather
        than through `get_str`, so a key added later cannot miss the rule by being spelled the other
        way. The refusal names the key alone, because the value is what must not be repeated.
        """
        value = self._get(key, str, default, required)
        if value in (None, ""):
            return cast("str | None", value)
        text = cast("str", value)
        # Both flavours are tested, because the value is resolved later by the platform's own Path
        # and a drive-absolute value is absolute on Windows while a POSIX reader would let it pass.
        posix, windows = PurePosixPath(text.replace("\\", "/")), PureWindowsPath(text)
        if posix.is_absolute() or windows.is_absolute() or ".." in posix.parts:
            raise self._refuse(
                f"{self.where}: '{key}' names a path outside the project.",
                key,
                hint=f"Write '{key}' as a path inside the project directory.",
            )
        return text

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self._get(key, bool, default, False))

    def get_table(self, key: str) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", self._get(key, dict, None, False))

    def get_tables(self, key: str) -> list[dict[str, Any]]:
        items = cast("list[Any]", self._get(key, list, [], False))
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                raise self._refuse(f"{self.where}: [[{key}]] #{i + 1} must be a table", key)
        return cast("list[dict[str, Any]]", items)

    def unknown(self, known: Iterable[str]) -> list[str]:
        return sorted(set(self.data) - set(known))

    def note_unknown(self, known: Iterable[str]) -> list[str]:
        """Every key this table does not read, as one sentence each.

        An unknown key is ignored rather than refused, so the sentence is a note a caller carries
        into what it returns. It is not written anywhere here, because a library that decided where
        a note went would decide it for every caller, and a note nobody can read is a note nobody
        acts on.
        """
        return unknown_key_warnings(self.data, known, self.where)


def _is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    return origin in (Union, UnionType) and len(args) == 2 and args[1] is type(None)


def from_mapping[T](
    cls: type[T],
    *,
    base: Mapping[str, Any] | None = None,
    prefixes: list[str] | None = None,
    environ: Mapping[str, str],
) -> T:
    """Build a dataclass tree from its own defaults, a nested mapping, and the environment.

    Precedence, lowest to highest: the field's default, `base` (a nested mapping keyed by field
    name, such as a parsed TOML file with one table per nested dataclass), then the variable in
    `environ` named PREFIX_FIELD, upper case, with nested names joined by `_`. `environ` is
    required and never the process's own, because the machine is the one reader of the process.
    """
    names = prefixes or []
    table = base or {}
    env = environ
    hints = get_type_hints(cls)
    args: dict[str, Any] = {}
    for f in fields(cast("Any", cls)):
        annotation = hints.get(f.name, f.type)
        if is_dataclass(annotation):
            nested = table.get(f.name)
            args[f.name] = from_mapping(
                cast("type[Any]", annotation),
                base=nested if isinstance(nested, dict) else None,
                prefixes=[*names, f.name],
                environ=environ,
            )
            continue
        raw: Any = env.get("_".join([*names, f.name]).upper())
        from_env = raw is not None
        if raw is None and f.name in table:
            raw = table[f.name]
        if raw is None:
            continue
        where = ".".join([*names[1:], f.name])
        bounds, hazard = f.metadata.get("bounds"), f.metadata.get("hazard")
        args[f.name] = read_value(annotation, raw, where=where, bounds=bounds, hazard=hazard, from_env=from_env)
    return cls(**args)


def read_value(
    annotation: Any, raw: Any, *, where: str, bounds: Bounds | None, hazard: str | None, from_env: bool
) -> Any:
    """One value read as its type and measured against its safe range, or a refusal.

    An environment variable carries a string and nothing else, so its value is converted. A mapping
    carries the type its author wrote, so a value of the wrong type is refused rather than converted,
    which is what keeps `[video] output_fps = "25"` and `[video] output_fps = 25.7` from becoming a
    number nobody typed. `config set` and `--set` hand over the string a command line carried, so
    both read it the way an environment variable is read, and the loader and the writer meet one rule.
    """
    try:
        value = _coerce(annotation, raw) if from_env else _as_written(annotation, raw)
    except (TypeError, ValueError) as exc:
        wanted = getattr(annotation, "__name__", str(annotation))
        raise InputError(f"{where}: expected {wanted}, got {raw!r} ({exc}).") from exc
    if bounds is not None and not bounds.holds(value):
        raise InputError(f"{where}: {bounds.sentence}, got {value!r}.", hint=hazard)
    return value


def _coerce(annotation: Any, raw: str) -> Any:
    """One environment variable's string, read as the field's own type.

    An environment variable carries a string and nothing else, so every value here is converted.
    A mapping's value goes through `_as_written` instead, which refuses a value of another type,
    and that is how one key answers the same way whether it was written in a file or exported.
    """
    if annotation is bool:
        return raw.lower() not in ("false", "0", "no", "")
    if _is_optional(annotation):
        return _coerce(get_args(annotation)[0], raw)
    if get_origin(annotation) is tuple:
        inner = get_args(annotation)[0] if get_args(annotation) else str
        return tuple(_coerce(inner, x.strip()) for x in raw.split(","))
    if annotation is int:
        return int(raw)
    if annotation is float:
        return float(raw)
    if isinstance(annotation, type):
        return annotation(raw)
    return raw


def _as_written(annotation: Any, raw: Any) -> Any:
    """One value read from a mapping, which has to be the type its author wrote."""
    if _is_optional(annotation):
        return _as_written(get_args(annotation)[0], raw)
    if get_origin(annotation) is tuple:
        if isinstance(raw, str) or not isinstance(raw, (list, tuple)):
            raise TypeError(f"a {type(raw).__name__} is not an array")
        inner = get_args(annotation)[0] if get_args(annotation) else str
        return tuple(_as_written(inner, x) for x in raw)
    if annotation is bool:
        if not isinstance(raw, bool):
            raise TypeError(f"a {type(raw).__name__} is not a boolean")
        return raw
    if annotation in (int, float) and isinstance(raw, bool):
        raise TypeError("a boolean is not a number")
    if annotation is float and isinstance(raw, int):
        return float(raw)
    # JSON Schema counts a float with no fraction as an integer, so a file an editor validated
    # against the published schema reads the same here rather than being refused as a float.
    if annotation is int and isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(annotation, type) and not isinstance(raw, annotation):
        raise TypeError(f"a {type(raw).__name__} is not {'an' if annotation is int else 'a'} {annotation.__name__}")
    return raw


__all__ = [
    "A_LUMA",
    "A_PERCENT",
    "A_SHARE",
    "PUBLISHED",
    "Bounds",
    "Key",
    "Nature",
    "Source",
    "Table",
    "did_you_mean",
    "from_mapping",
    "read_value",
    "registry",
    "tune",
    "unknown_key_message",
    "unknown_key_warnings",
]
