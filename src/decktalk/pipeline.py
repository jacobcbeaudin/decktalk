"""The run declared once: the six stages in order, the artifacts they pass between them, and how a
moment ended.

The pipeline used to be described in three places, which were the stage order, an if-chain that
worked out what a partial run still needed, and about ten sentences across the stages telling a
reader to run an earlier command first. `PIPELINE` is the one declaration all three are read from,
so the precondition check, the `--from` and `--to` validation, the hint a `NOT_BUILT` error carries
and the next step `status` reports are one table a reader can see whole.

A stage is a member of `Stage` and never its name as a string, so a misspelt stage fails where it is
written rather than making a comparison quietly false. The value of a member is the one word that
names it everywhere: the command that runs it alone, the word `--from`, `--to` and `--skip` take,
the `stage` of an event line and the key its row sits under in `build --json`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath


class Stage(Enum):
    """One stage of the pipeline, declared in run order. The value is the word that names it."""

    NARRATE = "narrate"
    CUE = "cue"
    RECORD = "record"
    SOUNDSCAPE = "soundscape"
    ASSEMBLE = "assemble"
    VERIFY = "verify"

    @property
    def spec(self) -> StageSpec:
        """What this stage reads, what it writes and why it runs where it does."""
        return SPECS[self]

    @staticmethod
    def span(first: Stage | None, last: Stage | None) -> tuple[Stage, ...]:
        """The stages from `first` to `last`, both inclusive, in run order. None is the pipeline's own end."""
        stages = list(Stage)
        begin = stages.index(first) if first is not None else 0
        end = stages.index(last) if last is not None else len(stages) - 1
        return tuple(stages[begin : end + 1])


class Outcome(Enum):
    """How a stage or a section ended, which is the one field that replaces three event names.

    A caller reads one field to learn what happened, where `stage.done`, `stage.skip` and
    `stage.fail` would make it branch three ways to learn the same fact.
    """

    OK = "ok"
    SKIPPED = "skipped"
    FAILED = "failed"


class Artifact(Enum):
    """A file or a directory one stage writes and a later stage reads.

    The value is the artifact's path under the project root, written with forward slashes, because
    every path DeckTalk reports is project-relative and posix on all three platforms. `FINAL` is the
    directory the deliverables are written into, because the film is named after the project.
    """

    TAKES = "build/narrate/takes.json"
    CUE_TIMES = "build/cue-times.json"
    RECORDINGS = "build/recordings"
    SOUNDSCAPE = "build/soundscape"
    FINAL = "build/final"

    @property
    def path(self) -> PurePosixPath:
        """The artifact's project-relative path."""
        return PurePosixPath(self.value)

    def under(self, root: Path) -> Path:
        """The artifact's path under one project root, which is what a stage opens."""
        return root.joinpath(*self.path.parts)

    @property
    def written_by(self) -> Stage | None:
        """The stage that writes this artifact, or None when nothing in the pipeline does."""
        return next((spec.stage for spec in PIPELINE if self in spec.writes), None)


@dataclass(frozen=True)
class StageSpec:
    """One row of the pipeline: a stage, what it reads, what it writes and why it sits where it does."""

    stage: Stage
    reads: tuple[Artifact, ...]
    writes: tuple[Artifact, ...]
    why: str


PIPELINE: tuple[StageSpec, ...] = (
    StageSpec(
        stage=Stage.NARRATE,
        reads=(),
        writes=(Artifact.TAKES,),
        why="The script becomes spoken takes with a word clock, which every later stage measures against.",
    ),
    StageSpec(
        stage=Stage.CUE,
        reads=(Artifact.TAKES,),
        writes=(Artifact.CUE_TIMES,),
        why="Each cue phrase becomes a second on its section clock, which the recorder plays to.",
    ),
    StageSpec(
        stage=Stage.RECORD,
        reads=(Artifact.CUE_TIMES,),
        writes=(Artifact.RECORDINGS,),
        why="The pages are recorded against those seconds, so the picture lands on its word.",
    ),
    StageSpec(
        stage=Stage.SOUNDSCAPE,
        reads=(Artifact.TAKES,),
        writes=(Artifact.SOUNDSCAPE,),
        why="The music, the ambience and the effects are generated last of the paid work, so the unpaid "
        "draft loop stops at record.",
    ),
    StageSpec(
        stage=Stage.ASSEMBLE,
        reads=(Artifact.TAKES, Artifact.RECORDINGS, Artifact.SOUNDSCAPE),
        writes=(Artifact.FINAL,),
        why="The recordings, the narration and the soundscape are cut, mixed and encoded into one film.",
    ),
    StageSpec(
        stage=Stage.VERIFY,
        reads=(Artifact.CUE_TIMES, Artifact.FINAL),
        writes=(),
        why="The finished film is measured against the clock the earlier stages promised.",
    ),
)
"""Every stage in run order, with the artifacts it reads and writes and the reason it runs there."""

SPECS: dict[Stage, StageSpec] = {spec.stage: spec for spec in PIPELINE}
"""Each stage's row, so `Stage.spec` is one lookup rather than a scan."""


def required(plan: tuple[Stage, ...]) -> tuple[Artifact, ...]:
    """The artifacts a run of `plan` reads but does not write, which must be on disk before it starts.

    A run that starts at `assemble` reads the recordings a skipped `record` would have made, so the
    precondition check and the `NOT_BUILT` hint both read this rather than an if-chain of their own.
    """
    written = {artifact for stage in plan for artifact in stage.spec.writes}
    needed = [artifact for stage in plan for artifact in stage.spec.reads if artifact not in written]
    return tuple(dict.fromkeys(needed))


__all__ = [
    "PIPELINE",
    "Artifact",
    "Outcome",
    "Stage",
    "StageSpec",
]
