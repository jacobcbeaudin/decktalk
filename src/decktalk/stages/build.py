"""The whole pipeline in order: narrate, align, record, assemble, verify.

`build` runs the five stages, or the run of them `--from` and `--to` name, and writes what it is
doing to `build/progress.jsonl` as it goes. Each line is one event, so an agent reads a file instead
of tailing a log, and the file is truncated at the start of a run so it only ever describes the run
in front of it.

The fifth stage is the real `verify`, cues and all. A build that exits 0 has therefore measured every
reveal against its word, which is the promise the product makes, and not only checked that the
sections start on a picture.

A stage that `--from` skips leaves the artifact it would have written to the run before it, so a run
that starts past a missing artifact stops and names the file rather than assembling stale work.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..errors import ConfigError, MissingInputError
from ..jsonio import relative
from ..model import PageSection, Project
from ..verdicts import Findings
from .align import AlignResult, UnknownCueError, align
from .assemble import AssembleResult, assemble
from .narrate import NarrateResult, narrate
from .record import RecordResult, SectionRecording, record
from .verify import VerifyResult, verify

log = logging.getLogger(__name__)

Reporter = Callable[[str, Any], None]

# The five stages in the only order they run in. Both ends of --from and --to are inclusive.
STAGES = ("narrate", "align", "record", "assemble", "verify")


@dataclass
class Progress:
    """The run's own account of itself, one JSON line per event, appended as the run goes.

    The file is opened for truncation when the run starts and each line is flushed as it is written,
    so a reader that opens the file mid-run sees every event that has happened and nothing else. Each
    row carries the process that wrote it, which is how a reader tells a live run from a dead one.
    """

    path: Path
    stages: tuple[str, ...]

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")

    def event(self, stage: str, event: str, *, section: int | None = None, detail: str | None = None) -> None:
        """Append one event. `detail` is one sentence, because a reader relays it to a person."""
        row = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "pid": os.getpid(),
            "stage": stage,
            "stage_index": self.stages.index(stage) + 1,
            "stage_count": len(self.stages),
            "section": section,
            "event": event,
            "detail": detail,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()


def stopped_on(exc: BaseException) -> str:
    """One sentence naming what ended a run, carrying the reason when the exception gives one.

    A reader relays this row to a person, and the name of an exception class alone says nothing a
    person can act on, so the first line of its message goes with it.
    """
    name = type(exc).__name__
    first = next((line.strip() for line in str(exc).splitlines() if line.strip()), "")
    return f"The run stopped on {name}: {first}" if first else f"The run stopped on {name}."


def stage_plan(from_stage: str | None, to_stage: str | None) -> tuple[str, ...]:
    """The stages this run executes, both ends inclusive, in the fixed order."""
    for name in (from_stage, to_stage):
        if name is not None and name not in STAGES:
            raise ConfigError(f"unknown stage {name!r}; the five stages are {', '.join(STAGES)}")
    first = STAGES.index(from_stage) if from_stage else 0
    last = STAGES.index(to_stage) if to_stage else len(STAGES) - 1
    if first > last:
        raise ConfigError(f"--from {STAGES[first]} comes after --to {STAGES[last]}")
    return STAGES[first : last + 1]


def required_inputs(project: Project, plan: tuple[str, ...]) -> list[Path]:
    """The artifacts a skipped stage would have written, which this run reads instead of writing.

    A run that starts at `assemble` needs the recordings a skipped `record` would have made, and a
    run that starts anywhere past `narrate` needs the narration clock. Only a missing one matters.
    """
    needed: list[Path] = []
    if "narrate" not in plan and {"align", "record", "assemble", "verify"} & set(plan):
        needed.append(project.timeline_path)
    if "align" not in plan and {"record", "assemble", "verify"} & set(plan):
        needed.append(project.cue_times_path)
    if "record" not in plan and {"assemble"} & set(plan):
        needed += [project.recording(sec) for sec in project.page_sections]
    if "assemble" not in plan and "verify" in plan:
        needed.append(project.final)
    return [path for path in needed if not path.exists()]


@dataclass
class BuildResult:
    """What each stage of one run produced, in the order the run made them."""

    stages: tuple[str, ...] = STAGES
    narration: NarrateResult | None = None
    align: AlignResult | None = None
    recordings: RecordResult | None = None
    assembly: AssembleResult | None = None
    verification: VerifyResult | None = None
    progress: Path | None = None

    @property
    def ran(self) -> tuple[tuple[str, Any], ...]:
        """Each stage and the result it returned, in the order the stages run."""
        results = (self.narration, self.align, self.recordings, self.assembly, self.verification)
        return tuple(zip(STAGES, results, strict=True))

    @property
    def ok(self) -> bool:
        """True when every stage the run planned finished and none of them found anything at all.

        The exit code is not this: an uncertain finding, such as a slate standing in for a clip the
        project does not have, fails the run only under `--strict`, as it does on every command.
        """
        done = [result for name, result in self.ran if name in self.stages]
        return all(result is not None for result in done) and self.findings == Findings()

    @property
    def findings(self) -> Findings:
        """Every stage's findings, added, with each fault counted once. A stage that did not run adds nothing.

        `verify` repeats what each recording log judged, which is the point of its `recordings` table
        after a build someone else recorded. A log this run judged itself is counted once, through
        `record`, and a log it did not, because `--only` named other sections or a section had no
        narration span, is counted here through `verify`, which is the only stage that read it.
        """
        total = Findings()
        for name, result in self.ran:
            if result is None:
                continue
            if name == "verify" and self.recordings is not None and self.verification is not None:
                judged = {row.key for row in self.recordings.sections}
                unjudged = (row for row in self.verification.recordings if row.key not in judged)
                total = total + self.verification.film_findings
                total = total + Findings.of(v for row in unjudged for v in row.verdicts)
            else:
                total = total + result.findings
        return total

    def to_dict(self, root: Path) -> dict[str, Any]:
        """One entry per stage that ran, each the stage's own JSON-ready data."""
        doc: dict[str, Any] = {name: None if result is None else result.to_dict(root) for name, result in self.ran}
        doc["stages"] = list(self.stages)
        doc["progress"] = None if self.progress is None else relative(self.progress, root)
        return doc


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
    from_stage: str | None = None,
    to_stage: str | None = None,
    progress_path: Path | None = None,
    report: Reporter | None = None,
) -> BuildResult:
    """Run the stages `from_stage` to `to_stage`, both inclusive. `report(stage, result)` sees each one.

    The build stops after align when a cue phrase is unresolved, unless `allow_unresolved_cues` is
    set, and when a cue id appears nowhere in its page, unless `allow_unknown_cues` is set. It stops
    after record when a page threw, because that section recorded nothing worth assembling.
    """
    plan = stage_plan(from_stage, to_stage)
    out = BuildResult(stages=plan, progress=progress_path or project.progress_path)
    assert out.progress is not None
    steps = Progress(path=out.progress, stages=plan)
    steps.start()
    missing = required_inputs(project, plan)
    if missing:
        names = ", ".join(relative(path, project.root) for path in missing)
        steps.event(plan[0], "fail", detail=f"The run needs {names}, which an earlier stage writes.")
        raise MissingInputError(
            f"this run starts at {plan[0]} and needs {names}, which an earlier stage writes. "
            "Run the earlier stage, or start the build further back with --from."
        )
    sections = [s for s in project.page_sections if not only or s.number in only]
    running = plan[0]

    def emit(stage: str, result: Any) -> None:
        if report:
            report(stage, result)

    def run(stage: str, detail: str) -> bool:
        nonlocal running
        if stage not in plan:
            return False
        running = stage
        log.info("===== %s =====", stage)
        steps.event(stage, "start", detail=detail)
        return True

    try:
        if run("narrate", "Narrating the script into one take per spoken section."):
            out.narration = narrate(project, silent=silent, force=force)
            wrote = len(out.narration.synthesized)
            steps.event("narrate", "done", detail=f"Wrote {wrote} take(s) and reused the rest.")
            emit("narrate", out.narration)
        if run("align", "Matching every cue phrase against the narrated words."):
            # The flag passes straight through, and the align table still prints before the build stops.
            try:
                out.align = align(project, allow_unknown_cues=allow_unknown_cues)
            except UnknownCueError as exc:
                out.align = exc.result
                steps.event("align", "fail", detail="A cue id appears nowhere in the page that plays it.")
                emit("align", out.align)
                raise
            steps.event("align", "done", detail=f"Left {out.align.unresolved} cue(s) unresolved.")
            emit("align", out.align)
            if out.align.unresolved and not allow_unresolved_cues:
                steps.event("align", "fail", detail=f"{out.align.unresolved} cue phrase(s) were not found.")
                raise ConfigError(
                    f"{out.align.unresolved} cue(s) could not be matched to the narration; a slide whose cues are "
                    "unresolved never appears. Fix the phrases in cues.json (see the notes above) or pass "
                    "allow_unresolved_cues=True / --allow-unresolved-cues:\n  " + "\n  ".join(out.align.problems)
                )
        if run("record", f"Recording up to {len(sections)} section(s) with headless Chromium."):

            def opening(section: PageSection) -> None:
                where = 1 + [s.number for s in sections].index(section.number)
                steps.event("record", "start", section=section.number, detail=f"Section {where} of {len(sections)}.")

            def recorded(row: SectionRecording) -> None:
                what = "Kept the recording on disk" if row.kept else "Recorded the section"
                event = "skip" if row.kept else "done"
                steps.event("record", event, section=row.section.number, detail=f"{what}: {row.label}.")

            out.recordings = record(project, only=only, opening=opening, report=recorded)
            kept = len(out.recordings.kept_sections)
            done = len(out.recordings.sections) - kept
            steps.event("record", "done", detail=f"Recorded {done} section(s) and kept {kept}.")
            emit("record", out.recordings)
            broken = out.recordings.page_errors
            if broken:
                # A page that threw recorded whatever was left on the stage, usually nothing, so the
                # build stops here rather than delivering a blank section as if it were fine.
                steps.event("record", "fail", detail=f"{len(broken)} section(s) hit a page error.")
                raise ConfigError(
                    f"{len(broken)} section(s) hit a page error while recording. "
                    "Fix the page and run `decktalk build` again:\n  "
                    + "\n  ".join(f"section {r.key}: {e}" for r in broken for e in r.log.page_errors)
                )
        if run("assemble", f"Cutting {len(project.sections)} section(s) and mixing the soundtrack."):
            out.assembly = assemble(project, soundscape=soundscape, loudness=loudness, strict=strict)
            steps.event("assemble", "done", detail=f"Wrote {out.assembly.duration:.2f} seconds of film.")
            emit("assemble", out.assembly)
        if run("verify", "Measuring every reveal on the finished film against its cue."):
            # Every cue, so a build that exits 0 has measured each reveal against its word.
            out.verification = verify(project)
            found = out.verification.findings
            steps.event(
                "verify",
                "done",
                detail=f"Found {found.certain} certain and {found.uncertain} uncertain finding(s).",
            )
            emit("verify", out.verification)
    except BaseException as exc:
        # Whatever ends the run, the log closes on a row for the stage that was running, because a
        # reader cannot tell a stage that is still working from one whose process died.
        steps.event(running, "fail", detail=stopped_on(exc))
        raise
    return out
