"""The project's secrets: `.env` read once, from a path the caller names, and never printed.

    my-lesson/.env   ELEVENLABS_API_KEY (never committed)

A variable already in the environment wins over the file, and a value that still looks like the
placeholder `<your key>` counts as unset. No value ever reaches a log line, an error message or a
JSON payload, because a secret is named by its variable name and never by its value, so every value
this module hands back is a `Secret` that has to be revealed on purpose.

The environment is an argument rather than something this module reaches for, so two projects in one
process cannot read each other's, and `Machine.from_environment` stays the only place `os.environ` is
read at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from decktalk.errors import InputError
from decktalk.inputs.paths import at
from decktalk.secret import Secret

PLACEHOLDER_MARK = "<"
"""What an unfilled placeholder such as `<your key>` opens with, which counts as no value at all."""

COMMENT_MARK = " #"
"""What ends an unquoted value, so a trailing note never becomes part of a credential."""


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
        object.__setattr__(self, "_file_values", None)

    @property
    def environ(self) -> Mapping[str, str]:
        """The environment this project reads, which is whatever the machine was built from."""
        return cast("Mapping[str, str]", object.__getattribute__(self, "_environ"))

    @property
    def file_values(self) -> dict[str, str]:
        """What `.env` holds, parsed on the first question and kept, so the file is read once."""
        values = object.__getattribute__(self, "_file_values")
        if values is None:
            values = read_dotenv(self.file)
            object.__setattr__(self, "_file_values", values)
        return cast("dict[str, str]", values)

    def get(self, name: str) -> Secret:
        """The value of one variable, or an empty `Secret` when it is unset or still a placeholder."""
        value = self.environ.get(name) or self.file_values.get(name, "")
        return Secret("" if value.startswith(PLACEHOLDER_MARK) else value, name)

    def has(self, *names: str) -> bool:
        """True when every one of these variables is set, which is what `doctor` reports without reading one."""
        return all(self.get(name) for name in names)

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


__all__ = ["Env", "read_dotenv"]
