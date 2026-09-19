"""The whole pipeline in order: narrate, align, record, assemble, verify."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..model import Project
from ..verdicts import Findings
from .align import AlignResult, UnknownCueError, align
from .assemble import AssembleResult, assemble
from .narrate import NarrateResult, narrate
from .record import RecordResult, record
from .verify import VerifyResult, verify

log = logging.getLogger(__name__)

Reporter = Callable[[str, Any], None]


@dataclass
class BuildResult:
    """What each stage of one run produced, in the order the run made them."""

    narration: NarrateResult | None = None
    align: AlignResult | None = None
    recordings: RecordResult | None = None
    assembly: AssembleResult | None = None
    verification: VerifyResult | None = None

    @property
    def ok(self) -> bool:
        return self.assembly is not None and self.verification is not None and self.verification.ok

    @property
    def findings(self) -> Findings:
        """Every stage's findings, added. A stage that did not run adds nothing."""
        stages = (self.narration, self.align, self.recordings, self.assembly, self.verification)
        total = Findings()
        for stage in stages:
            if stage is not None:
                total += stage.findings
        return total

    def to_dict(self, root: Path) -> dict[str, Any]:
        """One entry per stage that ran, each the stage's own JSON-ready data."""
        named = (("narrate", self.narration), ("align", self.align), ("record", self.recordings),
                 ("assemble", self.assembly), ("verify", self.verification))  # fmt: skip
        return {name: None if stage is None else stage.to_dict(root) for name, stage in named}


def build(
    project: Project,
    *,
    silent: bool = False,
    force: bool = False,
    only: list[int] | None = None,
    soundscape: bool = True,
    loudness: bool = True,
    strict: bool = False,
    allow_unresolved_cues: bool = False,
    allow_unknown_cues: bool = False,
    report: Reporter | None = None,
) -> BuildResult:
    """Run every stage. `report(stage, result)` is called after each one, for the CLI's tables.

    The build stops after align when a cue phrase is unresolved, unless allow_unresolved_cues is
    set, and when a cue id appears nowhere in its page, unless allow_unknown_cues is set. The
    verify stage checks section starts and cuts, and `decktalk verify` measures the cues.
    """

    def emit(stage: str, result: Any) -> None:
        if report:
            report(stage, result)

    out = BuildResult()
    log.info("===== narrate =====")
    out.narration = narrate(project, silent=silent, force=force)
    emit("narrate", out.narration)
    log.info("===== align =====")
    # The flag passes straight through, and the align table still prints before the build stops.
    try:
        out.align = align(project, allow_unknown_cues=allow_unknown_cues)
    except UnknownCueError as exc:
        out.align = exc.result
        emit("align", out.align)
        raise
    emit("align", out.align)
    if out.align.unresolved and not allow_unresolved_cues:
        raise ConfigError(
            f"{out.align.unresolved} cue(s) could not be matched to the narration; a slide whose cues are "
            "unresolved never appears. Fix the phrases in cues.json (see the notes above) or pass "
            "allow_unresolved_cues=True / --allow-unresolved-cues:\n  " + "\n  ".join(out.align.problems)
        )
    log.info("===== record =====")
    out.recordings = record(project, only=only)
    emit("record", out.recordings)
    broken = out.recordings.page_errors
    if broken:
        # A page that threw recorded whatever was left on the stage, usually nothing, so the
        # build stops here rather than delivering a blank section as if it were fine.
        raise ConfigError(
            f"{len(broken)} section(s) hit a page error while recording. "
            "Fix the page and run `decktalk build` again:\n  "
            + "\n  ".join(f"section {r.key}: {e}" for r in broken for e in r.log.page_errors)
        )
    log.info("===== assemble =====")
    out.assembly = assemble(project, soundscape=soundscape, loudness=loudness, strict=strict)
    emit("assemble", out.assembly)
    log.info("===== verify =====")
    out.verification = verify(project, checks=[])
    emit("verify", out.verification)
    return out
