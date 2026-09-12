"""Build a dataclass tree from defaults, a nested mapping (parsed TOML), and environment variables."""

from __future__ import annotations

import os
from dataclasses import fields, is_dataclass
from types import UnionType
from typing import Any, Union, cast, get_args, get_origin, get_type_hints


def from_env[T](
    cls: type[T],
    overrides: dict[str, str] | None = None,
    prefixes: list[str] | None = None,
    base: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> T:
    """Instantiate a dataclass recursively.

    Precedence, lowest to highest: the dataclass default, `base` (a nested mapping keyed
    by field name, for example a parsed TOML file with one table per nested dataclass),
    then the environment variable named PREFIX_FIELD (upper case, nested names joined
    with underscores). `overrides` maps a computed env name to an alias. `environ`
    replaces os.environ, for tests.
    """
    overrides = overrides or {}
    prefixes = prefixes or []
    base = base or {}
    env = os.environ if environ is None else environ

    def is_optional(t: Any) -> bool:
        origin = get_origin(t)
        args = get_args(t)
        return origin in (Union, UnionType) and len(args) == 2 and args[1] is type(None)

    hints = get_type_hints(cls)
    args: dict[str, Any] = {}
    for f in fields(cast(Any, cls)):
        ftype = hints.get(f.name, f.type)
        if is_dataclass(ftype):
            sub = base.get(f.name)
            args[f.name] = from_env(
                cast(type[Any], ftype),
                overrides,
                [*prefixes, f.name],
                sub if isinstance(sub, dict) else None,
                environ,
            )
            continue
        env_name = "_".join([*prefixes, f.name]).upper()
        key = overrides.get(env_name, env_name)
        raw: Any = env.get(key)
        if raw is None and f.name in base:
            raw = base[f.name]
        if raw is None:
            continue
        if ftype is bool:
            args[f.name] = raw if isinstance(raw, bool) else str(raw).lower() not in ("false", "0", "no", "")
        elif is_optional(ftype):
            inner = get_args(ftype)[0]
            args[f.name] = raw if isinstance(raw, inner) else inner(raw)
        elif get_origin(ftype) is tuple:
            items = raw.split(",") if isinstance(raw, str) else list(raw)
            inner = get_args(ftype)[0] if get_args(ftype) else str
            args[f.name] = tuple(inner(x) for x in items)
        elif isinstance(ftype, type):
            args[f.name] = raw if isinstance(raw, ftype) else ftype(raw)
        else:
            args[f.name] = raw
    return cls(**args)
