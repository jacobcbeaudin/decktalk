"""Reading and writing the JSON files DeckTalk owns, and the one walker each way between JSON and a dataclass.

Every file DeckTalk writes goes through `write_json`, which writes under a temporary name and
renames, so a reader never opens a half-written file. Every dataclass that becomes JSON goes
through `as_json`, which turns nested dataclasses into objects, a verdict and a finding row into
the objects every payload carries, paths into forward-slashed strings, other enums into their
values, and tuples into lists. A value that is none of those reaches `json.dumps` as it is, and is refused
there rather than written wrong. A number that is not finite is refused for the same reason,
because no strict JSON reader accepts `NaN` or `Infinity`.

`read_as` is the walk the other way. It builds a dataclass from JSON by the dataclass's own type
hints, reads an enum member from its value and a verdict from its object, and raises `ShapeError`,
naming the place in the document, on a missing key, a key the type does not declare, a value of
the wrong kind, or a code no enum holds. A reader of DeckTalk's JSON therefore holds enum members
and never compares a string against a code.
"""

from __future__ import annotations

import json
import types
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path, PurePath
from typing import Any, Union, get_args, get_origin, get_type_hints, overload

from .verdicts import Finding, Verdict


def read_json(path: Path) -> Any:
    """The parsed file. The caller turns it into a dataclass at once, which is where `Any` stops."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Write `data` as JSON atomically, so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=indent, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def dumps(data: Any) -> str:
    """`data` as the JSON text one envelope is printed as, refusing what no strict reader accepts.

    The envelope an agent parses and the files a build writes go through the same rule, so a value
    that could not be written to `build/` cannot be printed to stdout either.
    """
    return json.dumps(data, indent=2, allow_nan=False)


def as_json(value: Any) -> Any:
    """`value` as JSON-ready data: dataclasses become objects, paths strings, enums their values."""
    if isinstance(value, (Verdict, Finding)):
        return value.to_dict()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: as_json(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Enum):
        return as_json(value.value)
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(k): as_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_json(v) for v in value]
    return value


def relative(path: Path, root: Path) -> str:
    """The path relative to the project root with forward slashes, or the whole path when it lies outside."""
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


class ShapeError(ValueError):
    """JSON that is not the shape its type declares, with the place in the document where it went wrong."""


@overload
def read_as[T](kind: type[T], data: Any, where: str = "$") -> T: ...


@overload
def read_as(kind: Any, data: Any, where: str = "$") -> Any: ...


def read_as(kind: Any, data: Any, where: str = "$") -> Any:
    """`data` read as `kind`: a dataclass, an enum, a list, a tuple, a mapping, an optional, or a scalar.

    A dataclass takes every one of its fields from a key of the same name and refuses an object that
    is missing one or carries one it does not declare, so the type is the whole shape. A verdict and a
    finding row read themselves, because each checks its label and its certainty against its code,
    and they are the two types `as_json` lets write themselves. `where` names the place in the
    document, as `$.verify.cues[2]`. A class is read as a class, so the reader returns what it was
    asked for, and a union or an optional returns whichever member the data is.
    """
    origin = get_origin(kind)
    if kind is Any:
        return data
    if origin is Union or origin is types.UnionType:
        return _read_union(get_args(kind), data, where)
    if origin in (list, tuple):
        if not isinstance(data, list):
            raise ShapeError(f"{where} is {_kind_of(data)}, not an array")
        items = [read_as(get_args(kind)[0], item, f"{where}[{i}]") for i, item in enumerate(data)]
        return items if origin is list else tuple(items)
    if origin is dict:
        if not isinstance(data, dict):
            raise ShapeError(f"{where} is {_kind_of(data)}, not an object")
        value_kind = get_args(kind)[1]
        return {key: read_as(value_kind, value, f"{where}.{key}") for key, value in data.items()}
    if kind is Verdict or kind is Finding:
        try:
            return kind.from_dict(data)
        except ValueError as exc:
            raise ShapeError(f"{where}: {exc}") from None
    if isinstance(kind, type) and is_dataclass(kind):
        return _read_dataclass(kind, data, where)
    if isinstance(kind, type) and issubclass(kind, Enum):
        try:
            return kind(data)
        except ValueError:
            raise ShapeError(f"{where} is {data!r}, which is not a {kind.__name__}") from None
    return _read_scalar(kind, data, where)


def _kind_of(data: Any) -> str:
    return "null" if data is None else type(data).__name__


def _read_union(options: tuple[Any, ...], data: Any, where: str) -> Any:
    """The first option `data` reads as, so `X | None` is null or an X and `A | B` is whichever fits."""
    if data is None:
        if type(None) in options:
            return None
        raise ShapeError(f"{where} is null")
    refused: list[str] = []
    for option in options:
        if option is type(None):
            continue
        try:
            return read_as(option, data, where)
        except ShapeError as exc:
            refused.append(str(exc))
    raise ShapeError(refused[0] if len(refused) == 1 else f"{where} fits none of its shapes: {'; '.join(refused)}")


def _read_dataclass(kind: Any, data: Any, where: str) -> Any:
    if not isinstance(data, dict):
        raise ShapeError(f"{where} is {_kind_of(data)}, not a {kind.__name__} object")
    names = [f.name for f in fields(kind)]
    missing = [name for name in names if name not in data]
    if missing:
        raise ShapeError(f"{where} has no {', '.join(missing)}, which {kind.__name__} requires")
    unknown = [key for key in data if key not in names]
    if unknown:
        raise ShapeError(f"{where} carries {', '.join(map(str, unknown))}, which {kind.__name__} does not declare")
    hints = get_type_hints(kind)
    return kind(**{name: read_as(hints[name], data[name], f"{where}.{name}") for name in names})


def _read_scalar(kind: Any, data: Any, where: str) -> Any:
    """A boolean, an integer, a number or a string, where a boolean is never a number."""
    if kind is bool and isinstance(data, bool):
        return data
    if kind is int and isinstance(data, int) and not isinstance(data, bool):
        return data
    if kind is float and isinstance(data, (int, float)) and not isinstance(data, bool):
        return float(data)
    if kind is str and isinstance(data, str):
        return data
    if kind in (bool, int, float, str):
        raise ShapeError(f"{where} is {_kind_of(data)} {data!r}, not {kind.__name__}")
    raise TypeError(f"read_as cannot read the type {kind!r}")
