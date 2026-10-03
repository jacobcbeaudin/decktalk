"""The run declared once: the six stages in order, the artifacts they pass between them, and how a
moment ended.

The pipeline used to be described in three places, which were the stage order, an if-chain that
worked out what a partial run still needed, and about ten sentences across the stages telling a
reader to run an earlier command first. `PIPELINE` is the one declaration all three are read from,
so the precondition check, the `--from` and `--to` validation, the hint a `NOT_BUILT` error carries
and the next step `status` reports are one table a reader can see whole. `NEEDS` reads the same
table as a graph of stages, so which records a change leaves describing other inputs is derived
from the edges rather than kept as a rule of its own.

Each row also says which of two trusts its stage needs. `holds_key` is a stage that may buy, and so
reaches for the provider's key, and `opens_pages` is a stage that launches a browser and runs a
page's script. No row is both, and a host that runs strangers' decks reads the two parts off the
table, `Stage.voice_part()` in a process that holds the key and `Stage.render_part()` in one that
holds none, rather than keeping its own list of which stage is which.

A stage is a member of `Stage` and never its name as a string, so a misspelt stage fails where it is
written rather than making a comparison quietly false. The value of a member is the one word that
names it everywhere: the command that runs it alone, the word `--from`, `--to` and `--skip` take,
the `stage` of an event line and the key its row sits under in `build --json`.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum
from graphlib import TopologicalSorter


class Stage(Enum):
    """One stage of the pipeline, declared in run order. The value is the word that names it."""

    NARRATE = "narrate"
    CUE = "cue"
    RECORD = "record"
    SCORE = "score"
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

    @staticmethod
    def voice_part() -> tuple[Stage, ...]:
        """The stages that hold the key, in run order, which a host runs in a process that opens no page."""
        return tuple(spec.stage for spec in PIPELINE if spec.holds_key)

    @staticmethod
    def render_part() -> tuple[Stage, ...]:
        """The stages that hold no key, in run order, which a host runs in a process the key never reaches."""
        return tuple(spec.stage for spec in PIPELINE if not spec.holds_key)


class Outcome(Enum):
    """How a stage or a section ended, which is the one field that replaces four event names.

    A caller reads one field to learn what happened, where `stage.done`, `stage.kept`, `stage.skip`
    and `stage.fail` would make it branch four ways to learn the same fact. `kept` is a stage the
    run planned and did not repeat, because nothing it reads had changed since it last ran, and
    `skipped` is a stage the run did not plan at all. `stopped` is a run, a stage or a section the
    caller cancelled or interrupted, which is kept apart from `failed` because nothing went wrong.
    """

    OK = "ok"
    KEPT = "kept"
    SKIPPED = "skipped"
    STOPPED = "stopped"
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
    SCORE = "build/score"
    FINAL = "build/final"

    @property
    def written_by(self) -> Stage | None:
        """The stage that writes this artifact, or None when nothing in the pipeline does."""
        return next((spec.stage for spec in PIPELINE if self in spec.writes), None)

    @property
    def next_step(self) -> str:
        """The one sentence that tells a reader how to get this artifact built, read from its writer.

        Every refusal that meets a missing artifact carries this sentence, so a renamed command or a
        moved stage changes the advice in one place rather than in every stage that reads the file.
        """
        writer = self.written_by
        if writer is None:
            return f"Nothing in the pipeline writes {self.value}."
        if writer is Stage.NARRATE:
            # The one stage that spends money on every run has a way to make its artifact for nothing,
            # and a reader stopped by a missing take index should not have to find that flag elsewhere.
            return f"Run `decktalk {writer.value}` first, or `decktalk {writer.value} --no-spend` to spend nothing."
        return f"Run `decktalk {writer.value}` first."


@dataclass(frozen=True)
class StageSpec:
    """One row of the pipeline: a stage, what it reads and writes, the trust it needs and why it sits there.

    `holds_key` is true on a stage that may buy, and so reaches for the provider's key. `opens_pages`
    is true on a stage that launches a browser and runs a page's script.
    """

    stage: Stage
    reads: tuple[Artifact, ...]
    writes: tuple[Artifact, ...]
    holds_key: bool
    opens_pages: bool
    why: str


PIPELINE: tuple[StageSpec, ...] = (
    StageSpec(
        stage=Stage.NARRATE,
        reads=(),
        writes=(Artifact.TAKES,),
        holds_key=True,
        opens_pages=False,
        why="The script becomes spoken takes with a word clock, which every later stage measures against.",
    ),
    StageSpec(
        stage=Stage.CUE,
        reads=(Artifact.TAKES,),
        writes=(Artifact.CUE_TIMES,),
        holds_key=False,
        opens_pages=False,
        why="Each cue phrase becomes a second on its section clock, which the recorder plays to.",
    ),
    StageSpec(
        stage=Stage.RECORD,
        reads=(Artifact.TAKES, Artifact.CUE_TIMES),
        writes=(Artifact.RECORDINGS,),
        holds_key=False,
        opens_pages=True,
        why="The pages are recorded against those seconds, so the picture lands on its word.",
    ),
    StageSpec(
        stage=Stage.SCORE,
        reads=(),
        writes=(Artifact.SCORE,),
        holds_key=True,
        opens_pages=False,
        why="The music, the ambience and the effects are generated last of the paid work, so the unpaid "
        "draft loop stops at record.",
    ),
    StageSpec(
        stage=Stage.ASSEMBLE,
        reads=(Artifact.TAKES, Artifact.RECORDINGS, Artifact.SCORE),
        writes=(Artifact.FINAL,),
        holds_key=False,
        opens_pages=True,
        why="The recordings, the narration and the score are cut, mixed and encoded into one film.",
    ),
    StageSpec(
        stage=Stage.VERIFY,
        reads=(Artifact.CUE_TIMES, Artifact.FINAL),
        writes=(),
        holds_key=False,
        opens_pages=False,
        why="The finished film is measured against the clock the earlier stages promised.",
    ),
)
"""Every stage in run order, with the artifacts it reads and writes, the trust it needs and the reason it runs there."""

SPECS: dict[Stage, StageSpec] = {spec.stage: spec for spec in PIPELINE}
"""Each stage's row, so `Stage.spec` is one lookup rather than a scan."""


NEEDS: dict[Stage, frozenset[Stage]] = {
    spec.stage: frozenset(writer for artifact in spec.reads if (writer := artifact.written_by) is not None)
    for spec in PIPELINE
}
"""Each stage against the stages whose artifacts it reads, which is the table above read as a graph.

The declared order is one the graph admits and not the only one, because `score` reads nothing
another stage writes and runs after `record` only so the unpaid draft loop stops there.
"""


def downstream(changed: Collection[Stage]) -> tuple[Stage, ...]:
    """Every other stage that reads, directly or through another stage, what these stages write, in run order.

    The graph's own order reaches a stage only after every stage it reads from, so one pass carries a
    change as far as it goes, and a table that reads in a circle is refused here as a `CycleError`.
    """
    moved = set(changed)
    for stage in TopologicalSorter(NEEDS).static_order():
        if moved & NEEDS[stage]:
            moved.add(stage)
    return tuple(stage for stage in Stage if stage in moved and stage not in changed)


def required(plan: tuple[Stage, ...]) -> tuple[Artifact, ...]:
    """The artifacts a run of `plan` reads but does not write, which must be on disk before it starts.

    A run that starts at `assemble` reads the recordings a skipped `record` would have made, so the
    precondition check and the `NOT_BUILT` hint both read this rather than an if-chain of their own.
    """
    written = {artifact for stage in plan for artifact in stage.spec.writes}
    needed = [artifact for stage in plan for artifact in stage.spec.reads if artifact not in written]
    return tuple(dict.fromkeys(needed))


__all__ = ["Outcome", "Stage"]
