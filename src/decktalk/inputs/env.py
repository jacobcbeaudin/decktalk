"""The project's secrets: `.env` read once, from a path the caller names, and never printed.

    my-lesson/.env   ELEVENLABS_API_KEY (never committed)

A variable already in the environment wins over the file, and a value that still looks like the
placeholder `<your key>` counts as unset. No value ever reaches a log line, an error message or a
JSON payload, because a secret is named by its variable name and never by its value, so every value
this module hands back is a `Secret` that has to be revealed on purpose.

The environment is an argument rather than something this module reaches for, so two projects in one
process cannot read each other's, and the machine stays the only reader of `os.environ`.

Whether `.env` is read at all is the machine's decision, bound for each run through `reading_dotenv`.
A machine built from the process reads it, because it belongs to the author at the keyboard. A
machine a host built by hand does not, because a tenant's upload could otherwise carry a `.env` that
supplies a key, a voice id or a switch the host never gave it. A caller outside any run reads none,
so a host that reaches this layer without a machine gets the host's rule and never the author's.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import cast

from decktalk.errors import InputError
from decktalk.inputs.paths import at
from decktalk.secret import Secret

PLACEHOLDER_MARK = "<"
"""What an unfilled placeholder such as `<your key>` opens with, which counts as no value at all."""

COMMENT_MARK = " #"
"""What ends an unquoted value, so a trailing note never becomes part of a credential."""

DOTENV: ContextVar[bool] = ContextVar("decktalk_dotenv", default=False)
"""Whether the machine whose run is in progress reads a project's `.env`, which `reading_dotenv` sets.

It is false until a machine says otherwise, so a read with no run in progress fails closed.
"""


@contextmanager
def reading_dotenv(allowed: bool) -> Iterator[None]:
    """Read a project's `.env` or leave it unread while this is open, as the running machine decided."""
    token = DOTENV.set(allowed)
    try:
        yield
    finally:
        DOTENV.reset(token)


def read_dotenv(path: Path) -> dict[str, str]:
    """A small `.env` reader: `KEY=value`, with optional quotes, `#` comments and an `export` prefix."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    # An editor that writes a byte-order mark would otherwise hide the first key's name, so the file
    # is decoded with the mark consumed and a pasted key keeps working.
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = line.removeprefix("export ")
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ('"', "'") and value.count(value[0]) >= 2:
            quote = value[0]
            value = value[1 : value.index(quote, 1)]
        else:
            value = value.split(COMMENT_MARK, 1)[0].strip()
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Env:
    """One project's secrets. `file` is where they live, and every value it hands back is a `Secret`.

    The environment it reads is not a field, so no walker over `fields(Env)` can reach it and no
    dump of anything holding an `Env` can print a machine's whole environment.
    """

    file: Path

    def __init__(self, file: Path, environ: Mapping[str, str]) -> None:
        object.__setattr__(self, "file", file)
        object.__setattr__(self, "_environ", environ)

    @property
    def environ(self) -> Mapping[str, str]:
        """The environment this project reads, which is whatever the machine was built from."""
        return cast("Mapping[str, str]", object.__getattribute__(self, "_environ"))

    @cached_property
    def file_values(self) -> dict[str, str]:
        """What `.env` holds, parsed on the first question and kept, so the file is read once."""
        return read_dotenv(self.file)

    def get(self, name: str) -> Secret:
        """The value of one variable, or an empty `Secret` when it is unset or still a placeholder.

        The machine's decision about `.env` is asked at every lookup rather than when the project was
        opened, because a project is opened before the run whose machine decides it.
        """
        stated = self.file_values if DOTENV.get() else {}
        value = self.environ.get(name) or stated.get(name, "")
        return Secret("" if value.startswith(PLACEHOLDER_MARK) else value, name)

    def require(self, *names: str) -> list[Secret]:
        """The values of these variables, or an `INPUT` refusal naming every one that is not set."""
        values = [self.get(name) for name in names]
        missing = [name for name, value in zip(names, values, strict=True) if not value]
        if missing:
            raise InputError(
                f"{', '.join(missing)} is not set.",
                hint=f"Put it in {self.file.name} beside decktalk.toml, as .env.example shows, or export it.",
                location=at(self.file),
            )
        return values


__all__ = ["Env", "read_dotenv", "reading_dotenv"]
