"""The projects packaged in the wheel, which `decktalk init --example NAME` writes.

The starter is the default and is not an example: three sections, one equation, attributes only,
and a build without voice that takes about a minute. An example is a whole project that was really built, kept in
the wheel so that an author, or an agent, can read a working page rather than a snippet.

A name with no project behind it yet is reserved here rather than left out, so that the flag value
never changes meaning and the message says what it is waiting for.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import ConfigError

STARTER = "starter"


@dataclass(frozen=True)
class Example:
    """One `--example` value: where its project sits in the wheel, and what it shows."""

    name: str
    summary: str
    path: str | None
    """The directory under `template/`, or None while the example is only a reserved name."""

    @property
    def shipped(self) -> bool:
        return self.path is not None


EXAMPLES: tuple[Example, ...] = (
    Example(
        "lesson",
        "a lesson that teaches how a model learns, whose one long scene is drawn in code",
        "examples/lesson",
    ),
    Example(
        "product",
        "the project behind the product film, which is that film's deck and script without its voice",
        None,
    ),
    Example(
        "tutorial",
        "the six-section technical tutorial whose measured numbers the product film shows",
        None,
    ),
)


def listed_names() -> str:
    """Every `--example` value in one line, with each reserved name marked as one."""
    return ", ".join(ex.name if ex.shipped else f"{ex.name} (reserved)" for ex in EXAMPLES)


def example(name: str) -> Example:
    """The example called `name`. Raises ConfigError when no such name exists."""
    for ex in EXAMPLES:
        if ex.name == name:
            return ex
    raise ConfigError(f"unknown example {name!r} (the examples are {listed_names()})")


def reserved_message(ex: Example) -> str:
    """Why a reserved example cannot be written yet, in one sentence a reader can act on."""
    return (
        f"--example {ex.name} is a reserved name and no project stands behind it yet: {ex.summary}. "
        f"Run `decktalk init` for the starter, or `decktalk init --example lesson` for a finished "
        f"project to read."
    )
