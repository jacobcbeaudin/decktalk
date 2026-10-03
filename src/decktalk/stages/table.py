"""The six stages against the functions that run them, which `build` and the facade both call through.

A row is a stage's function, the result it answers with and the options it takes from a build.
`build` hands each stage only the options its row names, because a stage handed keywords it does
not read would accept a flag that changes nothing. The facade calls the same row for its verb, so a
test that replaces a row replaces the stage for both callers alike.

The options are named here rather than read off each function's signature, and a test holds the two
together, so a reader finds what a stage takes in one place and nothing is worked out at import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from decktalk.pipeline import Stage
from decktalk.results import (
    AssembleResult,
    CueResult,
    NarrateResult,
    RecordResult,
    Result,
    ScoreResult,
    VerifyResult,
)
from decktalk.stages.assemble import assemble
from decktalk.stages.cue import cue
from decktalk.stages.narrate import narrate
from decktalk.stages.record import record
from decktalk.stages.score import score
from decktalk.stages.verify import verify


@dataclass(frozen=True)
class StageCall[R: Result]:
    """One stage's function, called as `call(inputs, run, **options)`, its result and the options it takes."""

    call: Callable[..., R]
    result: type[R]
    options: tuple[str, ...]


CALLS: dict[Stage, StageCall[Any]] = {
    Stage.NARRATE: StageCall(narrate, NarrateResult, ("only", "force", "replace_voiced")),
    Stage.CUE: StageCall(cue, CueResult, ("only",)),
    Stage.RECORD: StageCall(record, RecordResult, ("only", "force")),
    Stage.SCORE: StageCall(score, ScoreResult, ("only", "replace_score")),
    Stage.ASSEMBLE: StageCall(assemble, AssembleResult, ("only", "score", "loudness", "strict")),
    Stage.VERIFY: StageCall(verify, VerifyResult, ("only",)),
}
"""Every stage of the pipeline against its row, which is the one table that says how a stage is called."""
