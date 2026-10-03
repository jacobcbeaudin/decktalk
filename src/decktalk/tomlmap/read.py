"""One mapping read into typed values, with the file, the table and the line named in every refusal."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints, overload

from decktalk.errors import InputError
from decktalk.findings import Location
from decktalk.locate import locate
from decktalk.tomlmap import Bounds, variable
from decktalk.tomlmap.suggest import unknown_key_warnings


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

    def note_unknown(self, known: Iterable[str], *, anywhere: Iterable[str] = ()) -> list[str]:
        """Every key this table does not read, as one sentence each.

        An unknown key is ignored rather than refused, so the sentence is a note a caller carries
        into what it returns. It is not written anywhere here, because a library that decided where
        a note went would decide it for every caller, and a note nobody can read is a note nobody
        acts on. `anywhere` is every dotted key a key of this table may have moved to.
        """
        return unknown_key_warnings(self.data, known, self.where, at=self.table, anywhere=anywhere)


def _is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    return origin in (Union, UnionType) and len(args) == 2 and args[1] is type(None)


def from_mapping[T](
    cls: type[T],
    *,
    base: Mapping[str, Any] | None = None,
    path: tuple[str, ...] = (),
    environ: Mapping[str, str],
) -> T:
    """Build a dataclass tree from its own defaults, a nested mapping, and the environment.

    Precedence, lowest to highest: the field's default, `base` (a nested mapping keyed by field
    name, such as a parsed TOML file with one table per nested dataclass), then the variable in
    `environ` that `variable` names for the field's dotted key. `environ` is
    required and never the process's own, because the machine is the one reader of the process.
    """
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
                path=(*path, f.name),
                environ=environ,
            )
            continue
        where = ".".join((*path, f.name))
        raw: Any = env.get(variable(where))
        from_env = raw is not None
        if raw is None and f.name in table:
            raw = table[f.name]
        if raw is None:
            continue
        bounds, hazard = f.metadata.get("bounds"), f.metadata.get("hazard")
        args[f.name] = read_value(annotation, raw, where=where, bounds=bounds, hazard=hazard, from_env=from_env)
    return cls(**args)


def read_value(
    annotation: Any, raw: Any, *, where: str, bounds: Bounds | None, hazard: str | None, from_env: bool
) -> Any:
    """One value read as its type and measured against its safe range, or a refusal.

    An environment variable carries a string and nothing else, so its value is converted. A mapping
    carries the type its author wrote, so a value of the wrong type is refused rather than converted,
    which is what keeps `[video] fps = "25"` and `[video] fps = 25.7` from becoming a
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


SWITCHED_OFF = frozenset(("", "0", "no", "false"))
"""The spellings of a switch variable that leave it off, so any other value turns it on."""


def _coerce(annotation: Any, raw: str) -> Any:
    """One environment variable's string, read as the field's own type.

    An environment variable carries a string and nothing else, so every value here is converted.
    A mapping's value goes through `_as_written` instead, which refuses a value of another type,
    and that is how one key answers the same way whether it was written in a file or exported.
    """
    if annotation is bool:
        return raw.lower() not in SWITCHED_OFF
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
