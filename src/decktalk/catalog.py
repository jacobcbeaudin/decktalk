"""The library's own contract, walked once so every rendering of it reads the same rows.

This module is internal. It is not in `__all__`, no command is named after it, and the only callers
are `decktalk schema` and the generators that write the committed schemas and the docs pages. It
exists so that the sentence a code, a key or a field publishes has one home in the model that
declares it, and so that two renderings of the same contract cannot disagree.

What it publishes is what the library owns, which is every result's schema, every finding code with
its sentence, every error code with its exit code, the event schema and every stage of the pipeline. The
command and flag table belongs to the command line and is joined onto this by `decktalk schema`,
which is the only place the two halves meet. The settings keys that move a finding are joined on
there too, because each key declares the codes it decides and the keys sit above this module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter

from decktalk.errors import ErrorCode
from decktalk.events import Line
from decktalk.findings import Code, Finding
from decktalk.pipeline import PIPELINE
from decktalk.results import RESULTS


def result_schemas() -> dict[str, dict[str, Any]]:
    """Every command's result schema, by name."""
    return {name: model.model_json_schema() for name, model in RESULTS.items()}


SCHEMAS: dict[str, Callable[[], dict[str, Any]]] = {
    **{name: RESULTS[name].model_json_schema for name in sorted(RESULTS)},
    "event": lambda: TypeAdapter(Line).json_schema(),
    "finding": Finding.model_json_schema,
}
"""Every JSON Schema the library owns by the name `decktalk schema NAME` prints it under, built when asked.

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
            "certainty": code.certainty.value,
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
