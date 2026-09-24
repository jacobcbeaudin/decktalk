"""One walk over the parser, joined onto what the library publishes about itself.

Three renderings read this and no other source: `--help` through Click's own formatter, the
generated reference page, and `decktalk schema`. The command half is walked from the parser, so a
flag on a page is a flag the command takes, and the contract half is the library's own registry, so
a sentence a code or a key publishes has one home in the model that declares it.

Nothing outside `cli/` imports the application, which is why the join happens here and not in the
library's own registry.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import MISSING, Field, fields, is_dataclass
from enum import Enum
from typing import Any

from pydantic import JsonValue
from typer._click import Context, Parameter
from typer._click.core import Command
from typer.main import get_command

from decktalk import catalog as library
from decktalk import page
from decktalk import settings as knobs
from decktalk.cli.app import PROGRAM, app
from decktalk.errors import ErrorCode
from decktalk.results import RESULTS, Result
from decktalk.tomlmap import PUBLISHED, Key

PURPOSE_LIMIT = 120
"""How much of a command's own sentence a row carries, which is more than any of them is long."""

SETTINGS_KEYSPACE = "settings"
"""What the `--set` parameter points a reader at, which is the key space rather than a copy of it."""

EXITS: tuple[tuple[int, str], ...] = (
    (0, "The command ran and found nothing."),
    (1, "The command ran and found something at or above the threshold --fail-on set."),
    (2, "The command line was refused, which is USAGE or APPROVAL."),
    (3, "The command could not run, which is every other error code."),
    (130, "The caller stopped the run."),
)
"""Every code a run can exit with, which is a two-way branch for an agent and a table for a reader."""

NAMES: dict[type[Result], str] = {model: name for name, model in RESULTS.items()}
"""Each result model by the name `decktalk schema NAME` prints it under, read back off the registry."""


def walk() -> list[dict[str, Any]]:
    """Every command of the tree, in the order the help prints them, each with its own parameters."""
    root = get_command(app)
    context = Context(root, info_name=PROGRAM)
    return list(_commands(root, context, prefix=""))


def _commands(group: Command, context: Context, *, prefix: str) -> Iterator[dict[str, Any]]:
    """Walk one group, and the one group nested inside it, yielding a row per command."""
    for name in group.list_commands(context):  # ty: ignore[unresolved-attribute]
        command = group.get_command(context, name)  # ty: ignore[unresolved-attribute]
        if command is None or command.hidden:
            continue
        path = f"{prefix}{name}"
        if hasattr(command, "list_commands"):
            yield from _commands(command, Context(command, info_name=path, parent=context), prefix=f"{path} ")
            continue
        yield {
            "command": path,
            "group": getattr(command, "rich_help_panel", None),
            "purpose": command.get_short_help_str(PURPOSE_LIMIT),
            "result": _result(command),
            "params": [_param(param, context) for param in command.params if not _is_help(param)],
        }


def _result(command: Command) -> str | None:
    """The name of the result this command answers with, read off the function the parser calls."""
    model = getattr(command.callback, "result", None)
    return NAMES.get(model) if isinstance(model, type) else None


def _is_help(param: Parameter) -> bool:
    """True for the help option, which every command has and no schema needs a row for."""
    return "--help" in param.opts


def _param(param: Parameter, context: Context) -> dict[str, Any]:
    """One parameter as an agent reads it, which is what to write and what happens when it is not."""
    row: dict[str, Any] = {
        "opts": list(param.opts) + list(param.secondary_opts),
        "type": param.type.name,
        "metavar": param.make_metavar(context) if param.metavar else param.metavar,
        "default": _plain(param.default),
        "repeatable": bool(getattr(param, "multiple", False)),
        "envvar": param.envvar,
        "help": getattr(param, "help", None),
        "hidden": bool(getattr(param, "hidden", False)),
    }
    if "--set" in param.opts:
        row["keys"] = SETTINGS_KEYSPACE
    return row


def _plain(value: object) -> JsonValue:
    """A default as JSON carries it, which is its own value for a scalar and its name for an enum."""
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def globals_() -> list[dict[str, Any]]:
    """The flags every command carries, walked from the root callback so the list cannot drift."""
    root = get_command(app)
    context = Context(root, info_name=PROGRAM)
    return [_param(param, context) for param in root.params if not _is_help(param)]


def exits() -> list[dict[str, Any]]:
    """Every exit code with the sentence that says what it means."""
    return [{"exit": code, "sentence": sentence} for code, sentence in EXITS]


def document() -> dict[str, Any]:
    """The whole instruction set in one object, which is what bare `decktalk schema` prints."""
    published = library.document()
    return {
        "commands": walk(),
        "globals": globals_(),
        "exits": exits(),
        "errors": published["errors"],
        "findings": published["findings"],
        "stages": published["stages"],
    }


def settings_schema(*, machine: bool = False) -> dict[str, Any]:
    """Every knob with its type, default, safe range, unit, hazard and the findings it decides.

    It is rendered from the key records rather than read from a committed file, because the file is
    a repository artifact and a wheel carries the records. A test holds the two to the same key set.
    """
    wanted = [key for key in knobs.KEYS if not machine or key.scope.value == "machine"]
    return {
        "keys": [{name: _plain(_published(key, name)) for name in PUBLISHED} for key in wanted],
        "numbers": [
            {
                "id": number.id,
                "formula": number.formula,
                "reads": list(number.reads),
                "unit": number.unit,
                "kind": number.kind,
                "sentence": number.sentence,
                "decides": [code.value for code in number.decides],
            }
            for number in knobs.NUMBERS
        ],
    }


def _published(key: Key, name: str) -> object:
    """One published field of one key, with a range written as the sentence a reader meets."""
    value = getattr(key, name)
    if name in ("bounds", "typed"):
        return value.sentence if value is not None else None
    if name == "decides":
        return [code.value for code in value]
    return value


def page_schema() -> dict[str, Any]:
    """Every attribute an author or an agent writes in a slide, with its values, its range and its code."""
    return {
        "attributes": [spec.model_dump(mode="json") for spec in page.ATTRS.values()],
        "entrances": {name: effect.model_dump(mode="json") for name, effect in page.ENTRANCES.items()},
        "exits": {name: effect.model_dump(mode="json") for name, effect in page.EXITS.items()},
        "words": {name: effect.model_dump(mode="json") for name, effect in page.WORD_STYLES.items()},
        "counts": {name: effect.model_dump(mode="json") for name, effect in page.COUNTS.items()},
        "attention": {name: effect.model_dump(mode="json") for name, effect in page.ATTENTION.items()},
        "slides": {name: effect.model_dump(mode="json") for name, effect in page.SLIDE_ENTRANCES.items()},
        "measurable_span_seconds": page.MEASURABLE_SPAN_SECONDS,
        "capture_fps": page.CAPTURE_FPS,
    }


def project_schema() -> dict[str, Any]:
    """The shape of `cues.json`, walked from the row the loader parses it into."""
    # The cue row is an input rather than a result, so its schema is walked from the declaration
    # the loader reads it with, which is the one place its keys and their defaults are written.
    from decktalk.inputs.cues import Cue  # noqa: PLC0415

    if not is_dataclass(Cue):  # pragma: no cover  (the row is a dataclass and the walk needs one)
        raise TypeError("the cue row is no longer a dataclass, so its schema cannot be walked")
    rows = [
        {"key": field.name, "type": _type_name(field.type), "default": _default(field)}
        for field in fields(Cue)
        if field.name != "occurrence_set"
    ]
    return {
        "file": "cues.json",
        "sections": {"min_seconds": {"type": "number", "default": None}, "cues": rows},
    }


def _default(field: Field[object]) -> JsonValue:
    """What a row holds when the author writes nothing, or null when the key is required."""
    return None if field.default is MISSING else _plain(field.default)


def _type_name(annotation: object) -> str:
    """One declared type as JSON names it, which is what a reader writes in the file."""
    spelled = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", str(annotation))
    return {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}.get(spelled, spelled)


def named(name: str, *, machine: bool = False) -> dict[str, Any]:
    """The one contract document `decktalk schema NAME` prints, whichever name was asked for."""
    if name in RESULTS:
        return library.result_schema(name)
    if name == "finding":
        return library.finding_schema()
    if name == "error":
        return library.error_schema()
    if name == "event":
        return library.event_schema()
    if name == "settings":
        return settings_schema(machine=machine)
    if name == "page":
        return page_schema()
    if name == "project":
        return project_schema()
    raise KeyError(name)


def names() -> tuple[str, ...]:
    """Every name `decktalk schema NAME` answers to, which is what a refusal lists back."""
    return (*sorted(RESULTS), "error", "event", "finding", "page", "project", "settings")


def codes() -> tuple[str, ...]:
    """Every error code, which the reference page prints with its exit code beside it."""
    return tuple(code.value for code in ErrorCode)


__all__ = ["document", "globals_", "named", "names", "page_schema", "project_schema", "settings_schema", "walk"]
