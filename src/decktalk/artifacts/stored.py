"""A build artifact as a file: one frozen model per file, read once and written atomically.

Every file under `build/` that a later stage, a user's own script or the page runtime reads is one
of these. The model is the file's published shape, so the field names are the JSON keys and a
reader holds members rather than comparing strings, and the same class both reads and writes, so no
two places can disagree about what a file holds.

A file is written under a temporary name in the same directory and renamed over the target, which
is atomic on every platform DeckTalk ships on, so a reader never opens a half-written artifact and
a run interrupted mid-write leaves the previous file whole.

An artifact that will not parse is reported as one that was never built, because the recovery is
the same: run the stage that writes it again. The hint names that stage, read from `PIPELINE`, so
no module here spells a "run this first" sentence of its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ValidationError

from decktalk.errors import NotBuiltError
from decktalk.findings import MODEL
from decktalk.pipeline import Artifact

INDENT = 2
"""How the artifacts are indented, which keeps a diff of one readable in a terminal."""


class Stored(BaseModel):
    """One file under `build/`, which knows how to read itself and how to write itself."""

    model_config = MODEL

    @classmethod
    def read(cls, path: Path) -> Self | None:
        """The artifact at `path`, or None when nothing has written one there yet."""
        if not path.is_file():
            return None
        return cls.parse(path)

    @classmethod
    def require(cls, path: Path, artifact: Artifact) -> Self:
        """The artifact at `path`, or a `NOT_BUILT` refusal naming the stage that writes it."""
        found = cls.read(path)
        if found is None:
            raise NotBuiltError(f"{artifact.value} has not been built.", hint=_rebuild(artifact))
        return found

    @classmethod
    def parse(cls, path: Path) -> Self:
        """The artifact at `path`, refusing a file that is there and cannot be read as this shape."""
        try:
            return cls.model_validate_json(path.read_bytes())
        except (ValidationError, ValueError, OSError) as exc:
            raise NotBuiltError(
                f"{path.name} is there and cannot be read as {cls.__name__.lower()} ({_first_line(exc)}).",
                hint=f"Delete {path.name} and build it again.",
            ) from exc

    def write(self, path: Path) -> Path:
        """Write this artifact over `path` in one step, and give back the path it was written to."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.writing")
        text = json.dumps(self.model_dump(mode="json"), indent=INDENT, allow_nan=False)
        temporary.write_text(text + "\n", encoding="utf-8")
        temporary.replace(path)
        return path


def _rebuild(artifact: Artifact) -> str:
    """The command that writes this artifact, which is the one next step a reader needs."""
    stage = artifact.written_by
    return f"Run `decktalk {stage.value}` first." if stage else f"Nothing in the pipeline writes {artifact.value}."


def _first_line(error: Exception) -> str:
    """The first line of a parser's complaint, because a reader relays this to a person."""
    return next((line.strip() for line in str(error).splitlines() if line.strip()), type(error).__name__)


__all__ = ["Stored"]
