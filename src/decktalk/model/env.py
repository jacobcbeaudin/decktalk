"""The project's secrets: `.env` read once, and never printed.

    my-lesson/.env   ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (never committed)

A real environment variable wins over the file, and a value that still looks like the placeholder
`<your key>` counts as unset. No value ever reaches a log line, an error message or a JSON payload,
because a secret is named by its variable name and never by its value, so every value this module
hands back is a `Secret` that has to be revealed on purpose.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ..errors import ConfigError
from ..secret import Secret


def read_dotenv(path: Path) -> dict[str, str]:
    """Tiny .env reader: KEY=value, optional quotes, # comments, export prefix."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    # An editor that writes a byte-order mark would otherwise hide the first key's name, so the
    # file is decoded with the mark consumed and a pasted key keeps working.
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in ('"', "'") and value.count(value[0]) >= 2:
            quote = value[0]
            value = value[1 : value.index(quote, 1)]  # quoted: take the inside, ignore a trailing comment
        else:
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


@dataclass(frozen=True)
class Env:
    """One project's secrets. `file` is where they live, and every value it hands back is a `Secret`.

    The environment it reads is not a field, so no walker over `fields(Env)` can reach it and no
    `asdict` of anything holding an `Env` can print a machine's whole environment.
    """

    file: Path

    def __init__(self, file: Path, environ: Mapping[str, str] | None = None) -> None:
        object.__setattr__(self, "file", file)
        object.__setattr__(self, "_environ", os.environ if environ is None else environ)
        object.__setattr__(self, "_file_values", None)

    @property
    def environ(self) -> Mapping[str, str]:
        """The environment this project reads, which is `os.environ` unless a caller named another."""
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
        return Secret("" if value.startswith("<") else value, name)

    def require(self, *names: str) -> list[Secret]:
        """The values of these variables, or a ConfigError naming every one that is not set."""
        values = [self.get(name) for name in names]
        missing = [name for name, value in zip(names, values, strict=True) if not value]
        if missing:
            raise ConfigError(
                f"{', '.join(missing)} is not set.",
                hint=f"Put it in {self.file.name} beside decktalk.toml, as .env.example shows, or export it.",
                path=self.file,
            )
        return values
