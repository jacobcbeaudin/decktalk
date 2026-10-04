"""What the last build made, and whether the film and its measurement on disk still stand.

A build leaves `kept.json` under the build directory, which holds a digest of everything `assemble`
and `verify` read the last time they ran, what they wrote and what they found. A build whose digest
matches keeps both stages rather than repeating them, and `status` names nothing next once the film
on disk is the one the last build measured. The digests are over file contents and never over file
times, because a copy or a checkout moves every time and changes no byte.

`BUILT` says when each artifact the pipeline declares counts as written, which `build` reads to
decide what a run must make and `status` reads to name the next command.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import ClassVar

from pydantic import Field, JsonValue

from decktalk.artifacts.stored import Stored, engine_digest, file_digest
from decktalk.findings import Finding, Model
from decktalk.inputs import Inputs
from decktalk.pipeline import Artifact
from decktalk.settings import PROJECT_FILE
from decktalk.stages import score

BUILT: dict[Artifact, Callable[[Inputs], bool]] = {
    Artifact.TAKES: lambda inputs: inputs.workspace.takes_path.is_file(),
    Artifact.CUE_TIMES: lambda inputs: inputs.workspace.cue_times_path.is_file(),
    Artifact.RECORDINGS: lambda inputs: all(
        inputs.workspace.recording(section.key).is_file() for section in inputs.document.page_sections
    ),
    # A project that describes no score has nothing to generate, so that stage is never next, and one
    # whose bought music is not joined yet has the score next, which joins it for nothing.
    Artifact.SCORE: lambda inputs: inputs.document.score.empty or score.ready(inputs),
    # The film is what the final directory is for, and a directory a stopped run left behind is not one.
    Artifact.FINAL: lambda inputs: inputs.workspace.film.is_file(),
}
"""When each artifact the pipeline declares counts as built, which is one sentence of arithmetic each.

The path comes from the workspace rather than from the artifact's own value, because `[project]
build` may put the whole build directory somewhere else and the workspace is what knows where.
"""


class KeptStage(Model):
    """What one stage read, what it wrote and what it found the last time a build ran it."""

    digest: str = Field(description="The digest of everything the stage read, with the options it was run with.")
    options: dict[str, JsonValue] = Field(description="The options the stage was run with, as the build passed them.")
    outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Each file the stage wrote, project-relative, against the digest of its bytes.",
    )
    findings: tuple[Finding, ...] = Field(
        default=(),
        description="What the stage found, which a run that keeps the stage reports again.",
    )


class Kept(Stored):
    """The record a build leaves of the two stages it can keep, read by the next build and by status."""

    label: ClassVar[str] = "the record of what the last build kept"

    assemble: KeptStage | None = None
    verify: KeptStage | None = None


def read_kept(inputs: Inputs) -> Kept:
    """The record the last build left, or an empty one when there is none or it cannot be read.

    A record this version cannot read keeps nothing, which costs one assemble and one verify and is
    never wrong, so it is not worth a refusal.
    """
    return Kept.previous(inputs.workspace.kept_path) or Kept()


def assemble_digest(inputs: Inputs, options: Mapping[str, JsonValue]) -> str:
    """The digest of everything `assemble` reads, with the options a build runs it with.

    The list is deliberately wide: the project file, the script, the cue file, every file the local
    origin serves, the take index and the joined narration, the cue times, every recording and every
    generated sound, every setting in force and the engine's own version. A digest that missed an
    input would ship a film the inputs no longer describe, and one that reads too much costs only a
    repeated assemble.
    """
    settings = json.dumps(dataclasses.asdict(inputs.settings), sort_keys=True, default=str)
    read = (f"{inputs.relative(path).as_posix()}:{file_digest(path)}" for path in _assemble_reads(inputs))
    return engine_digest(settings, json.dumps(options, sort_keys=True), *read)


def verify_digest(inputs: Inputs, assembled: str, options: Mapping[str, JsonValue]) -> str:
    """The digest of what `verify` measures: the film's own bytes, what made it, and the options.

    What made the film is the assemble digest, which already carries every setting and every file
    the measurement is judged against, so the film is the only input added here.
    """
    return engine_digest(assembled, file_digest(inputs.workspace.film), json.dumps(options, sort_keys=True))


def outputs_of(inputs: Inputs, paths: Iterable[Path]) -> dict[str, str]:
    """Each of these files that is on disk, project-relative, against the digest of its bytes."""
    return {inputs.relative(path).as_posix(): file_digest(path) for path in paths if path.is_file()}


def intact(inputs: Inputs, stage: KeptStage) -> bool:
    """Whether every file a kept stage wrote is still on disk with the bytes it wrote.

    A stage run on its own after the build, such as `decktalk assemble --skip score`, rewrites the
    film without touching the record, and this is what stops the next build keeping that film.
    """
    return all(file_digest(inputs.root / name) == digest for name, digest in stage.outputs.items())


def assembled(inputs: Inputs, kept: Kept) -> str | None:
    """The assemble digest of the film on disk, or None when that film no longer stands.

    The film stands when the last build's assemble read exactly what is on disk now and wrote
    exactly the files that are there, which is the one question both a keeping build and this
    report ask before they trust a measurement of it.
    """
    record = kept.assemble
    if record is None or not holds_film(inputs, record):
        return None
    return record.digest if assemble_digest(inputs, record.options) == record.digest else None


def holds_film(inputs: Inputs, record: KeptStage) -> bool:
    """Whether a kept assemble wrote the film on disk, byte for byte, along with everything beside it."""
    return inputs.relative(inputs.workspace.film).as_posix() in record.outputs and intact(inputs, record)


def _assemble_reads(inputs: Inputs) -> list[Path]:
    """Every file `assemble` may read, each once, in an order that depends on nothing but their names."""
    workspace = inputs.workspace
    named = [inputs.root / PROJECT_FILE, inputs.script_path, inputs.cues_path]
    named += [inputs.root / served for served in inputs.served_paths()]
    named += [workspace.takes_path, workspace.narration_path, workspace.cue_times_path]
    named += [workspace.recordings_dir, workspace.score_dir, workspace.joined_dir]
    return sorted({found for path in named for found in _files(path)}, key=lambda path: path.as_posix())


def _files(path: Path) -> list[Path]:
    """The files one named path stands for, which is itself, everything under it, or nothing."""
    if path.is_dir():
        return [found for found in path.rglob("*") if found.is_file()]
    return [path]


__all__ = [
    "BUILT",
    "Kept",
    "KeptStage",
    "assemble_digest",
    "assembled",
    "holds_film",
    "intact",
    "outputs_of",
    "read_kept",
    "verify_digest",
]
