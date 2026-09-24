# /// script
# requires-python = ">=3.12"
# ///
"""Generate src/decktalk/__init__.py, whose `__all__` is the reachable closure of the public surface.

    uv run scripts/build_api.py --write    # write the package's __init__.py
    uv run scripts/build_api.py --check    # exit 1 if the committed file would change

A name is exported when a module in `MODULES` lists it, or when it is reachable from the annotation
of something already exported. A result field annotated with a row type an agent can see in the JSON
Schema and cannot import would make the Python and the JSON surfaces disagree about one word, and
leaving that row out of `__all__` does not make it private, it makes it undiscoverable.

`MODULES` is the one list a new public module joins. A module that carries no `__all__` exports
nothing, which is how the stages and the command line stay private.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import importlib
import inspect
import sys
import types
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel  # noqa: E402  (after sys.path, so a checkout needs no install)

TARGET = ROOT / "src" / "decktalk" / "__init__.py"
PACKAGE = "decktalk"
STALE = "stale: {path}. Run `uv run scripts/{script} --write` to bring it up to date."
LINE_LENGTH = 120
"""The line ruff wraps at, which decides whether a module's imports fit on one line."""

MODULES = (
    "artifacts",
    "errors",
    "events",
    "explain",
    "findings",
    "inputs",
    "machine",
    "pipeline",
    "project",
    "results",
)
"""Every module whose `__all__` seeds the closure, in the order the imports are written."""

DOCSTRING = '''"""DeckTalk: narrated presentation videos, cut to the word.

A project is a directory. `decktalk.open(path)` returns a project, six verbs move it forward, and a
handful of calls report on it, cut a piece out of it or serve it. Every call returns a frozen result
whose findings are diagnostics with a code, a location, a certainty and often a fix a caller can
apply. Every call opens a run and writes to one event stream, which any renderer subscribes to.
Nothing in this package prints, nothing reads the environment except `Machine.from_environment`, and
a path a result carries is always relative to the project root.

`__all__` is the whole supported Python API. It is generated, and it is the closure of every type
reachable from an exported annotation, so a type a result can hand you is a type you can import.
Everything not in it may move without notice.
"""'''

VERSION_BLOCK = '''try:
    __version__ = version("decktalk")
except PackageNotFoundError:  # running from a checkout without an install
    __version__ = "0+unknown"'''


def seeds() -> dict[str, list[str]]:
    """Each module's own declared surface, which is what the closure starts from."""
    found: dict[str, list[str]] = {}
    for name in MODULES:
        module = importlib.import_module(f"{PACKAGE}.{name}")
        found[name] = sorted(getattr(module, "__all__", ()))
    return found


def annotations_of(obj: object) -> list[object]:
    """Every annotation one exported object carries, which is where the closure walks next."""
    if isinstance(obj, type) and issubclass(obj, BaseModel):
        return [field.annotation for field in obj.model_fields.values()]
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        return []
    if dataclasses.is_dataclass(obj) and isinstance(obj, type):
        return [field.type for field in dataclasses.fields(obj)]
    if isinstance(obj, type):
        return list(typing.get_type_hints(obj).values())
    if inspect.isfunction(obj):
        return list(typing.get_type_hints(obj).values())
    return [obj]


def named(annotation: object) -> list[type]:
    """Every class this annotation names, opening out unions, containers and annotated aliases."""
    if isinstance(annotation, type):
        return [annotation]
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is not None:
        return [found for argument in typing.get_args(annotation) for found in named(argument)]
    return []


def closure() -> dict[str, list[str]]:
    """Every exported name by the module it is imported from, seeds first and then what they reach."""
    exported: dict[str, list[str]] = {name: list(names) for name, names in seeds().items()}
    pending = [getattr(importlib.import_module(f"{PACKAGE}.{m}"), n) for m, names in exported.items() for n in names]
    seen = {id(obj) for obj in pending}
    while pending:
        for annotation in annotations_of(pending.pop()):
            for found in named(annotation):
                if id(found) in seen or not getattr(found, "__module__", "").startswith(f"{PACKAGE}."):
                    continue
                seen.add(id(found))
                pending.append(found)
                module = found.__module__.removeprefix(f"{PACKAGE}.")
                if found.__name__ not in exported.setdefault(module, []):
                    exported[module].append(found.__name__)
    return {module: sorted(names, key=order) for module, names in sorted(exported.items()) if names}


def order(name: str) -> tuple[int, str]:
    """How an import list is sorted, which is constants, then classes, then the rest, each ignoring case.

    This is the order the formatter's own import sorter wants, so the generated file is already
    formatted and `ruff check` has nothing to say about it.
    """
    rank = 0 if name.isupper() else 1 if name[:1].isupper() else 2
    return rank, name.lower()


def render(exported: dict[str, list[str]]) -> str:
    """The generated module as it is committed, which one `ruff format` run would leave alone."""
    lines = [
        DOCSTRING,
        "",
        "from __future__ import annotations",
        "",
        "from importlib.metadata import PackageNotFoundError, version",
        "",
    ]
    for module, names in exported.items():
        statement = f"from .{module} import {', '.join(names)}"
        if len(statement) <= LINE_LENGTH:
            lines.append(statement)
        else:
            lines.append(f"from .{module} import (")
            lines += [f"    {name}," for name in names]
            lines.append(")")
    every = sorted({name for names in exported.values() for name in names} | {"__version__"})
    lines += ["", VERSION_BLOCK, "", "__all__ = ["]
    lines += [f'    "{name}",' for name in every]
    lines.append("]")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="write the package's __init__.py")
    action.add_argument("--check", action="store_true", help="exit 1 if the committed file would change")
    args = parser.parse_args()

    text = render(closure())
    if args.check:
        if not TARGET.exists() or TARGET.read_text("utf-8") != text:
            print(STALE.format(path=TARGET.relative_to(ROOT), script=Path(__file__).name))
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
