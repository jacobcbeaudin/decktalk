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

import enum
import importlib
import inspect
import sys
import types
import typing
from pathlib import Path

from pydantic import BaseModel

import generated

ROOT = Path(__file__).resolve().parent.parent

TARGET = ROOT / "src" / "decktalk" / "__init__.py"
PACKAGE = "decktalk"

MODULES = (
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

VERSION_IMPORT = ("artifacts.stored", ["ENGINE_VERSION as __version__"])
"""Where `__version__` comes from, which is the one reading of the engine version every cache key carries."""


def seeds() -> dict[str, list[str]]:
    """Each module's own declared surface, which is what the closure starts from."""
    found: dict[str, list[str]] = {}
    for name in MODULES:
        module = importlib.import_module(f"{PACKAGE}.{name}")
        found[name] = sorted(getattr(module, "__all__", ()))
    return found


def annotations_of(obj: object) -> list[object]:
    """Every annotation one exported object carries, which is where the closure walks next.

    Every annotation is resolved rather than read as it was written, because the package writes
    `from __future__ import annotations` and a dataclass field then carries the source text of its
    type instead of the type. A string names no class, so a walk that read it would stop at the
    first dataclass and call everything beyond it private.
    """
    if isinstance(obj, type) and issubclass(obj, BaseModel):
        return [field.annotation for field in obj.model_fields.values()]
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        return []
    if isinstance(obj, type) or inspect.isfunction(obj):
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
    return {module: names for module, names in exported.items() if names}


def render(exported: dict[str, list[str]]) -> str:
    """The generated module as it is committed, with its imports sorted and wrapped by the project's ruff."""
    imports = [f"from .{module} import {', '.join(names)}" for module, names in [*exported.items(), VERSION_IMPORT]]
    every = sorted({name for names in exported.values() for name in names} | {"__version__"})
    listed = "".join(f'    "{name}",\n' for name in every)
    return generated.ruff(
        "\n".join(
            [DOCSTRING, "", "from __future__ import annotations", "", *imports, "", f"__all__ = [\n{listed}]", ""]
        ),
        TARGET,
    )


def documents() -> dict[Path, str]:
    """The package's __init__.py, which is the one file this generator owns."""
    return {TARGET: render(closure())}


if __name__ == "__main__":
    sys.exit(generated.run(documents))
