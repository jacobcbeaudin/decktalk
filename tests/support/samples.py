"""One instance of any published model, filled from its field types alone.

The result round-trip and the terminal renderers both need one of every result without a
hand-written example of each, so the sampler lives here rather than in either test file.

Every string, number, path and member gets a value no other field of the sample holds. A string
carries its field's name and a number from one counter, a number is the next count, and a member is
picked by the count among the values no other member of the sample holds. Booleans alternate, true
first, so two flags side by side never agree. A member is unique only
while its enum has a value left over, and otherwise repeats one. A renderer that printed one field
where another belongs, such as a key's default where its value belongs or a fixed layer where the
layer in force belongs, therefore writes a different text and fails its snapshot.
"""

from __future__ import annotations

import itertools
import types
import typing
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from decktalk.findings import Code, Finding, Location

# A finding fills its own severity and page from its code, so the sampler is handed one ready made.
EXAMPLES: dict[object, BaseModel] = {
    Finding: Finding(code=Code.CUE_OFF, message="It lands 340 ms late.", location=Location(where="2.1:formula")),
}

WHEN = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
"""The one moment every datetime field holds, because no renderer prints two of them side by side."""

FRACTION = 0.25
"""What sets a float apart from the integer count it was made from, so neither reads as the other."""


class Filler:
    """The values of one sample, each drawn from one counter so no two fields of the sample agree."""

    def __init__(self, *, every: bool) -> None:
        self.every = every
        self.count = itertools.count(1)
        self.members: set[object] = set()
        self.flags = itertools.cycle((True, False))

    def model[M: BaseModel](self, model: type[M]) -> M:
        """One instance of a model with every field filled, and each optional one only when `every` is asked."""
        return model(
            **{
                name: self.value(held(field.annotation) if self.every else field.annotation, name)
                for name, field in model.model_fields.items()
            }
        )

    def value(self, annotation: object, name: str) -> object:
        """One value of the given type for the field called `name`, unlike any other value in the sample."""
        if hasattr(annotation, "__metadata__"):
            return self.value(typing.get_args(annotation)[0], name)
        if annotation in EXAMPLES:
            return EXAMPLES[annotation]
        if annotation is bool:
            return next(self.flags)
        if annotation is int:
            return next(self.count)
        if annotation is float:
            return next(self.count) + FRACTION
        if annotation is datetime:
            return WHEN
        if isinstance(annotation, type):
            return self.of_class(annotation, name)
        return self.of_generic(annotation, name)

    def member(self, annotation: type[Enum]) -> Enum:
        """A member picked by the count whose value no member already in the sample has, where one is left.

        Two enums can share a value, as a layer and a scope both say `project`, and a renderer that
        compares them would otherwise read the sample as the case where they agree.
        """
        members = list(annotation)
        start = next(self.count)
        turned = [members[(start + step) % len(members)] for step in range(len(members))]
        chosen = next((one for one in turned if one.value not in self.members), turned[0])
        self.members.add(chosen.value)
        return chosen

    def text(self, name: str) -> str:
        """A string that names its field and carries the next count, so two string fields never agree."""
        return f"{name}{next(self.count)}"

    def of_class(self, annotation: type, name: str) -> object:
        """One value of a class the package declares, which is a path, a member or a model of its own."""
        if issubclass(annotation, Path):
            return Path("build") / self.text(name)
        if issubclass(annotation, Enum):
            return self.member(annotation)
        if issubclass(annotation, BaseModel):
            return self.model(annotation)
        return self.text(name)

    def of_generic(self, annotation: object, name: str) -> object:
        """One value of an annotation that names other annotations, filled from the first one it names."""
        origin = typing.get_origin(annotation)
        arguments = typing.get_args(annotation)
        if origin is typing.Literal:
            return arguments[0]
        if origin is tuple:
            return (self.value(arguments[0], name),)
        if origin is dict:
            return {self.value(arguments[0], name): self.value(arguments[1], name)}
        if origin in (typing.Union, types.UnionType):
            return None if type(None) in arguments else self.value(arguments[0], name)
        return self.text(name)


def sample[M: BaseModel](model: type[M], *, every: bool = False) -> M:
    """One instance of a model with every field filled, built from the field types alone.

    An optional field is left out unless `every` is asked for, which fills it with a value of the
    type it holds, so a renderer's line for that field is written too.
    """
    return Filler(every=every).model(model)


def held(annotation: object) -> object:
    """The type an optional annotation holds when it holds something, and any other annotation as it is."""
    arguments = typing.get_args(annotation)
    if typing.get_origin(annotation) in (typing.Union, types.UnionType) and type(None) in arguments:
        return next(argument for argument in arguments if argument is not type(None))
    return annotation
