"""The library's own contract, walked once so every rendering of it reads the same rows.

This module is internal. It is not in `__all__`, no command is named after it, and the only callers
are `decktalk schema` and the generators that write the committed schemas and the docs pages. It
exists so that the sentence a code, a key or a field publishes has one home in the model that
declares it, and so that two renderings of the same contract cannot disagree.

What it publishes is what the library owns, which is every result's schema, every finding code with
its sentence and the keys that decide it, every error code with its exit code, every event and every
stage of the pipeline. The command and flag table belongs to the command line and is joined onto
this by `decktalk schema`, which is the only place the two halves meet.
"""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from decktalk.errors import ErrorCode, ErrorInfo
from decktalk.events import Line
from decktalk.findings import Code, Finding
from decktalk.pipeline import PIPELINE
from decktalk.results import RESULTS


def result_schema(name: str) -> dict[str, Any]:
    """The JSON Schema of one command's result, by the name `decktalk schema NAME` prints it under."""
    return RESULTS[name].model_json_schema()


def result_schemas() -> dict[str, dict[str, Any]]:
    """Every command's result schema, by name."""
    return {name: model.model_json_schema() for name, model in RESULTS.items()}


def finding_schema() -> dict[str, Any]:
    """The JSON Schema of one finding, which carries the whole code enum inside it."""
    return Finding.model_json_schema()


def error_schema() -> dict[str, Any]:
    """The JSON Schema of the `error` a result carries, which carries the whole code enum inside it."""
    return ErrorInfo.model_json_schema()


def event_schema() -> dict[str, Any]:
    """The JSON Schema of one line of the event stream, discriminated by its `event`."""
    return TypeAdapter(Line).json_schema()


def finding_codes() -> list[dict[str, Any]]:
    """Every finding code as a row, in the order the enum declares them."""
    return [
        {
            "code": code.value,
            "sentence": code.sentence,
            "certainty": code.certainty.value,
            "raised_by": code.raised_by.value,
            "decides": list(code.decides),
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


def document() -> dict[str, Any]:
    """Everything the library publishes about itself, as one object a reader can hold whole."""
    return {
        "stages": stages(),
        "results": result_schemas(),
        "findings": finding_codes(),
        "errors": error_codes(),
        "event": event_schema(),
    }
