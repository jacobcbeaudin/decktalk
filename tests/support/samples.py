"""One instance of any published model, filled from its field types alone.

The result round-trip and the terminal renderers both need one of every result without a
hand-written example of each, so the sampler lives here rather than in either test file.
"""

from __future__ import annotations

import types
import typing
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from decktalk.findings import Code, Finding, Location

# A finding fills its own certainty and page from its code, so the sampler is handed one ready made.
EXAMPLES: dict[type[BaseModel], BaseModel] = {
    Finding: Finding(code=Code.CUE_OFF, message="It lands 340 ms late.", location=Location(where="2.1:formula")),
}


SCALARS: dict[object, object] = {
    bool: True,
    int: 1,
    float: 1.5,
    str: "one",
    datetime: datetime(2026, 9, 24, 3, 0, tzinfo=UTC),
}
"""One value per type that stands on its own, which is every field a sampler fills without recursing."""


def value(annotation: object) -> object:
    """One value of the given type, which is all a round-trip needs the field to hold."""
    if hasattr(annotation, "__metadata__"):
        return value(typing.get_args(annotation)[0])
    if annotation in EXAMPLES:
        return EXAMPLES[annotation]  # type: ignore[index]
    if annotation in SCALARS:
        return SCALARS[annotation]  # type: ignore[index]
    if isinstance(annotation, type):
        return of_class(annotation)
    return of_generic(annotation)


def of_class(annotation: type) -> object:
    """One value of a class the package declares, which is a path, a member or a model of its own."""
    if issubclass(annotation, Path):
        return Path("build/final/demo.mp4")
    if issubclass(annotation, Enum):
        return next(iter(annotation))
    if issubclass(annotation, BaseModel):
        return sample(annotation)
    return "one"


def of_generic(annotation: object) -> object:
    """One value of an annotation that names other annotations, filled from the first one it names."""
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if origin is typing.Literal:
        return arguments[0]
    if origin is tuple:
        return (value(arguments[0]),)
    if origin is dict:
        return {value(arguments[0]): value(arguments[1])}
    if origin in (typing.Union, types.UnionType):
        return None if type(None) in arguments else value(arguments[0])
    return "one"


def sample(model: type[BaseModel], *, every: bool = False) -> BaseModel:
    """One instance of a model with every field filled, built from the field types alone.

    An optional field is left out unless `every` is asked for, which fills it with a value of the
    type it holds, so a renderer's line for that field is written too.
    """
    return model(
        **{
            name: value(held(field.annotation) if every else field.annotation)
            for name, field in model.model_fields.items()
        }
    )


def held(annotation: object) -> object:
    """The type an optional annotation holds when it holds something, and any other annotation as it is."""
    arguments = typing.get_args(annotation)
    if typing.get_origin(annotation) in (typing.Union, types.UnionType) and type(None) in arguments:
        return next(argument for argument in arguments if argument is not type(None))
    return annotation
