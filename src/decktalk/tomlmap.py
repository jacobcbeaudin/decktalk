"""One loader from a mapping to typed values, with located errors and "did you mean" hints.

Every file a project owns is a mapping once it is parsed: `decktalk.toml`, `cues.json` and
`media/markers.json`. `Table` reads one of those mappings with the file and the table named in
every message, so a bad value fails at load rather than deep inside ffmpeg. `from_mapping`
builds a whole dataclass tree instead, reading the names, the types and the defaults from the
fields, which is how the tuning tables are written once and read everywhere.
"""

from __future__ import annotations

import difflib
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints, overload

from .errors import ConfigError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Check:
    """One rule a value must satisfy, with the sentence a failure prints after the key's name."""

    ok: Callable[[Any], bool]
    must: str


ABOVE_ZERO = Check(lambda v: v > 0, "must be above zero")
NOT_NEGATIVE = Check(lambda v: v >= 0, "must not be negative")
A_PERCENT = Check(lambda v: 0 <= v <= 100, "must be a percentage between 0 and 100")
A_SHARE = Check(lambda v: 0 <= v <= 1, "must be between 0 and 1")
A_LUMA = Check(lambda v: 0 <= v <= 255, "must be a luma between 0 and 255")


def unknown_key_message(key: str, known: Iterable[str], where: str) -> str:
    """The warning for one key that DeckTalk does not read, with the closest known key when one is near."""
    close = difflib.get_close_matches(key, sorted(known), n=1)
    hint = f" (did you mean '{close[0]}'?)" if close else ""
    return f"{where}: ignoring unknown key '{key}'{hint}."


def unknown_key_warnings(table: Mapping[str, Any], known: Iterable[str], where: str) -> list[str]:
    """One warning per key in `table` that is not in `known`. An unknown key is ignored, not an error."""
    names = set(known)
    return [unknown_key_message(key, names, where) for key in sorted(set(table) - names)]


class Table:
    """Typed access to one mapping, with error messages that name the file and the table."""

    def __init__(self, data: Mapping[str, Any], where: str, path: Path | None = None) -> None:
        self.data = data
        self.where = where
        # The file this table was read from, so a refusal fills the error's own path slot. A caller
        # that has no file, such as a table built from an environment, leaves it unset.
        self.path = path

    def _get(self, key: str, kind: type | tuple[type, ...], default: Any, required: bool) -> Any:
        if key not in self.data:
            if required:
                raise ConfigError(
                    f"{self.where}: '{key}' is required and is not there.",
                    hint=f"Add '{key}' to this table.",
                    path=self.path,
                )
            return default
        value = self.data[key]
        if isinstance(value, bool) and kind in (int, float, (int, float)):
            raise ConfigError(f"{self.where}: '{key}' must be a number, got a boolean", path=self.path)
        if not isinstance(value, kind):
            names = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
            raise ConfigError(f"{self.where}: '{key}' must be {names}, got {type(value).__name__}", path=self.path)
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
            raise ConfigError(
                f"{self.where}: '{key}' names a path outside the project.",
                hint=f"Write '{key}' as a path inside the project directory.",
                path=self.path,
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
                raise ConfigError(f"{self.where}: [[{key}]] #{i + 1} must be a table", path=self.path)
        return cast("list[dict[str, Any]]", items)

    def unknown(self, known: Iterable[str]) -> list[str]:
        return sorted(set(self.data) - set(known))

    def warn_unknown(self, known: Iterable[str]) -> None:
        """Log a warning for every key this table does not read. DeckTalk ignores such a key."""
        for message in unknown_key_warnings(self.data, known, self.where):
            log.warning(message)


def _is_optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    args = get_args(annotation)
    return origin in (Union, UnionType) and len(args) == 2 and args[1] is type(None)


def from_mapping[T](
    cls: type[T],
    *,
    base: Mapping[str, Any] | None = None,
    prefixes: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> T:
    """Build a dataclass tree from its own defaults, a nested mapping, and the environment.

    Precedence, lowest to highest: the field's default, `base` (a nested mapping keyed by field
    name, such as a parsed TOML file with one table per nested dataclass), then the environment
    variable named PREFIX_FIELD, upper case, with nested names joined by `_`. `environ`
    replaces os.environ, for tests.
    """
    names = prefixes or []
    table = base or {}
    env = os.environ if environ is None else environ
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
        where = f"[{names[-1]}] {f.name}" if names[1:] else f.name
        args[f.name] = _checked(annotation, raw, where=where, field=f, from_env=from_env)
    return cls(**args)


def _checked(annotation: Any, raw: Any, *, where: str, field: Any, from_env: bool) -> Any:
    """One value read as its field's type and measured against the field's own rule, or a located error.

    An environment variable carries a string and nothing else, so its value is converted. A mapping
    carries the type its author wrote, so a value of the wrong type is refused rather than converted,
    which is what keeps `[video] fps = "25"` and `[video] fps = 25.7` from becoming a number nobody
    typed.
    """
    try:
        value = _coerce(annotation, raw) if from_env else _as_written(annotation, raw)
    except (TypeError, ValueError) as exc:
        wanted = getattr(annotation, "__name__", str(annotation))
        raise ConfigError(f"{where}: expected {wanted}, got {raw!r} ({exc})") from exc
    check = field.metadata.get("check")
    if check is not None and not check.ok(value):
        raise ConfigError(f"{where}: {check.must}, got {value!r}")
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
    if isinstance(annotation, type) and not isinstance(raw, annotation):
        raise TypeError(f"a {type(raw).__name__} is not {'an' if annotation is int else 'a'} {annotation.__name__}")
    return raw


def env_names(cls: type[Any], prefix: str) -> set[str]:
    """Every environment variable `from_mapping` reads for this dataclass tree, so a typo can be named."""
    out: set[str] = set()
    hints = get_type_hints(cls)
    for f in fields(cast("Any", cls)):
        annotation = hints.get(f.name, f.type)
        if is_dataclass(annotation):
            out |= env_names(cast("type[Any]", annotation), f"{prefix}_{f.name}")
        else:
            out.add(f"{prefix}_{f.name}".upper())
    return out
