"""One walk over the parser, joined onto what the library publishes about itself.

Three renderings read this and no other source: `--help` through Click's own formatter, the
generated reference page, and `decktalk schema`. The command half is walked from the parser, so a
flag on a page is a flag the command takes, and the contract half is read off the library's own
registries, so a sentence a code or a key publishes has one home in the model that declares it.
That is every result's schema, every finding code, every error code with its exit, the event
schema and every stage of the pipeline.

Nothing outside `cli/` imports the application, which is why the join happens here. The generators
that write the committed schemas and the docs pages read the same rows from here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from pydantic import TypeAdapter
from typer import Context
from typer.main import get_command

from decktalk import page, settings
from decktalk.cli.app import PROGRAM, Command, Parameter, app
from decktalk.errors import ErrorCode, Exit
from decktalk.events import Line
from decktalk.findings import Code, Finding
from decktalk.pipeline import PIPELINE
from decktalk.results import RESULTS, Result, Scope
from decktalk.settings.layers import json_value
from decktalk.settings.numbers import NUMBERS
from decktalk.tomlmap import PUBLISHED, Key

PURPOSE_LIMIT = 120
"""How much of a command's own sentence a row carries, which is more than any of them is long."""

SETTINGS_KEYSPACE = "settings"
"""What the `--set` parameter points a reader at, which is the key space rather than a copy of it."""

NAMES: dict[type[Result], str] = {model: name for name, model in RESULTS.items()}
"""Each result model by the name `decktalk schema NAME` prints it under, read back off the registry."""

SCHEMAS: dict[str, Callable[[], dict[str, Any]]] = {
    **{name: RESULTS[name].model_json_schema for name in sorted(RESULTS)},
    "event": lambda: TypeAdapter(Line).json_schema(),
    "finding": Finding.model_json_schema,
}
"""Every JSON Schema the library's models own, by the name `decktalk schema NAME` prints it under.

A result's schema is its model's, and `error` is the result a refused command answers with, which
carries the error object and its whole code enum. The finding schema carries the whole code enum
too, and the event schema is one line of the stream discriminated by its `event`.
"""


def finding_codes() -> list[dict[str, Any]]:
    """Every finding code as a row, in the order the enum declares them."""
    return [
        {
            "code": code.value,
            "sentence": code.sentence,
            "severity": code.severity.value,
            "raised_by": code.raised_by.value,
            "docs": code.url,
        }
        for code in Code
    ]


def error_codes() -> list[dict[str, Any]]:
    """Every error code as a row, with the exit code its refusal takes."""
    return [
        {"code": code.value, "sentence": code.sentence, "exit": code.exit_code, "docs": code.url} for code in ErrorCode
    ]


def stages() -> list[dict[str, Any]]:
    """Every stage as a row, with what it reads, what it writes and why it runs where it does."""
    return [
        {
            "stage": spec.stage.value,
            "reads": [artifact.value for artifact in spec.reads],
            "writes": [artifact.value for artifact in spec.writes],
            "why": spec.why,
        }
        for spec in PIPELINE
    ]


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
        "default": json_value(param.default),
        "repeatable": bool(getattr(param, "multiple", False)) or param.nargs == -1,
        "required": param.required,
        "envvar": param.envvar,
        "help": getattr(param, "help", None),
        "hidden": bool(getattr(param, "hidden", False)),
    }
    if "--set" in param.opts:
        row["keys"] = SETTINGS_KEYSPACE
    return row


def globals_() -> list[dict[str, Any]]:
    """The flags every command carries, walked from the root callback so the list cannot drift."""
    root = get_command(app)
    context = Context(root, info_name=PROGRAM)
    return [_param(param, context) for param in root.params if not _is_help(param)]


def exits() -> list[dict[str, Any]]:
    """Every exit code with the sentence that says what it means."""
    return [{"exit": code.value, "sentence": code.sentence} for code in Exit]


def deciding(code: Code) -> tuple[str, ...]:
    """The settings keys whose value moves one code's verdict, read in reverse off each key's own list.

    A key names the codes it decides beside its range, and that is the one declaration of the
    relation, so the finding page, `decktalk schema` and `config explain` cannot give an agent two
    different answers about which setting to read.
    """
    return tuple(key.id for key in settings.KEYS if code in key.decides)


def findings() -> list[dict[str, Any]]:
    """Every finding code as the library publishes it, with the keys that decide it joined on."""
    return [{**row, "decides": list(deciding(Code(row["code"])))} for row in finding_codes()]


def document() -> dict[str, Any]:
    """The whole instruction set in one object, which is what bare `decktalk schema` prints."""
    return {
        "commands": walk(),
        "globals": globals_(),
        "exits": exits(),
        "errors": error_codes(),
        "findings": findings(),
        "stages": stages(),
    }


def settings_schema(*, scope: Scope | None = None) -> dict[str, Any]:
    """Every setting with its type, default, safe range, unit, hazard and the findings it decides.

    It is rendered from the key records rather than read from a committed file, because the file is
    a repository artifact and a wheel carries the records. A test holds the two to the same key set.
    A scope keeps the keys of one file alone, which for the machine is what its file may hold.
    """
    wanted = [key for key in settings.KEYS if scope is None or key.scope is scope]
    return {
        "keys": [{name: json_value(_published(key, name)) for name in PUBLISHED} for key in wanted],
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
            for number in NUMBERS
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


def cues_schema() -> dict[str, Any]:
    """The shape of `cues.json`, read off the row the loader parses it into."""
    # The cue row is an input rather than a result, so its schema comes from the declaration the
    # loader reads it with, which is the one place its keys and their defaults are written.
    from decktalk.inputs.cues import READ_HERE, Cue  # noqa: PLC0415

    declared = TypeAdapter(Cue).json_schema()["properties"]
    rows = [
        {"key": name, "type": row["type"], "default": row.get("default")}
        for name, row in declared.items()
        if name not in READ_HERE
    ]
    return {
        "file": "cues.json",
        "sections": {"min_seconds": {"type": "number", "default": None}, "cues": rows},
    }


CONTRACTS: dict[str, Callable[..., dict[str, Any]]] = {
    **SCHEMAS,
    "cues": cues_schema,
    "page": page_schema,
    "settings": settings_schema,
}
"""Every document `decktalk schema NAME` prints, by name, in the order a refusal lists them back."""


def named(name: str, *, scope: Scope | None = None) -> dict[str, Any]:
    """The one contract document `decktalk schema NAME` prints, whichever name was asked for."""
    if name == "settings":
        return settings_schema(scope=scope)
    return CONTRACTS[name]()


def names() -> tuple[str, ...]:
    """Every name `decktalk schema NAME` answers to, which is what a refusal lists back."""
    return tuple(CONTRACTS)


__all__ = [
    "CONTRACTS",
    "SCHEMAS",
    "deciding",
    "document",
    "error_codes",
    "finding_codes",
    "findings",
    "globals_",
    "named",
    "names",
    "page_schema",
    "cues_schema",
    "settings_schema",
    "stages",
    "walk",
]
