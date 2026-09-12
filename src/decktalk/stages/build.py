"""The whole pipeline in order: narrate, beats, record, measure, check, assemble, verify."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..errors import ConfigError
from ..project import Project
from .assemble import AssembleResult, assemble
from .beats import BeatsResult, resolve_beats
from .measure import LeadMeasurement, RecordingCheck, check, measure
from .narrate import NarrateResult, narrate
from .record import Recording, record
from .verify import VerifyResult, verify

log = logging.getLogger(__name__)

Reporter = Callable[[str, Any], None]


@dataclass
class BuildResult:
    narration: NarrateResult | None = None
    beats: BeatsResult | None = None
    recordings: list[Recording] = field(default_factory=list)
    leads: list[LeadMeasurement] = field(default_factory=list)
    checks: list[RecordingCheck] = field(default_factory=list)
    assembly: AssembleResult | None = None
    verification: VerifyResult | None = None

    @property
    def ok(self) -> bool:
        return self.assembly is not None and self.verification is not None and self.verification.ok


def build(
    project: Project,
    *,
    silent: bool = False,
    force: bool = False,
    only: list[int] | None = None,
    nomix: bool = False,
    loudnorm: bool = True,
    strict: bool = False,
    allow_unresolved: bool = False,
    report: Reporter | None = None,
) -> BuildResult:
    """Run every stage. `report(stage, result)` is called after each one, for the CLI's tables."""

    def emit(stage: str, result: Any) -> None:
        if report:
            report(stage, result)

    out = BuildResult()
    log.info("===== narrate =====")
    out.narration = narrate(project, silent=silent, force=force)
    emit("narrate", out.narration)
    log.info("===== beats =====")
    out.beats = resolve_beats(project)
    emit("beats", out.beats)
    if out.beats.unresolved and not allow_unresolved:
        raise ConfigError(
            f"{out.beats.unresolved} cue(s) could not be matched to the narration; a step whose cues are "
            "unresolved never appears. Fix the phrases in cues.json (see the notes above) or pass "
            "allow_unresolved=True / --allow-unresolved:\n  " + "\n  ".join(out.beats.problems)
        )
    log.info("===== record =====")
    out.recordings = record(project, only=only)
    emit("record", out.recordings)
    log.info("===== measure =====")
    out.leads = measure(project, only=only)
    emit("measure", out.leads)
    log.info("===== check =====")
    out.checks = check(project, only=only)
    emit("check", out.checks)
    log.info("===== assemble =====")
    out.assembly = assemble(project, nomix=nomix, loudnorm=loudnorm, strict=strict)
    emit("assemble", out.assembly)
    log.info("===== verify =====")
    out.verification = verify(project)
    emit("verify", out.verification)
    return out
