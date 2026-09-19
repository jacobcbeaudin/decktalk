"""The commands that make the video: the stages in order, and `build`, which runs them all.

`build` is the only long run, so it is the one that keeps a progress log a caller can poll while
the run is still going, and it prints each stage's table as that stage finishes.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..jsonio import relative
from ..pipeline import Stage, TakeStatus
from ..stages.align import UnknownCueError
from ..stages.align import align as resolve
from ..stages.assemble import assemble as cut_and_mix
from ..stages.build import build as run_pipeline
from ..stages.build import required_inputs, stage_plan
from ..stages.narrate import narrate as voice
from ..stages.narrate.plan import plan_totals
from ..stages.record import record as capture
from ..stages.verify import verify as read_final
from ..verdicts import Finding, Findings, Verdict
from . import options as opt
from . import output
from .envelope import Outcome, of, wrote
from .options import load_project


def _narrate_files(project: Any, result: Any) -> list[Path]:
    """Every file a narrate run wrote: each take it made and its words, then the indexes beside them.

    A take is named by its content hash and lives in the take directory, which several projects may
    share, so the path comes from `takes_dir` rather than from the project's own build directory. A
    take the run reused is on disk already and is not a file this run wrote, so `written` leaves it
    out and a caller that re-reads what it names re-reads nothing it does not have to.
    """
    index = result.takes
    made = [] if index is None else [index.sections[key] for key in result.synthesized if key in index.sections]
    return [
        *(project.takes_dir / take.file for take in made),
        *(project.takes_dir / take.words_file for take in made),
        project.takes_path,
        project.narration_path,
    ]


def _recorded_files(project: Any, result: Any) -> list[Path]:
    """Each section this run recorded and the log written beside it, which is one artifact in two files.

    A section the run kept is on disk from an earlier run and is not a file this run wrote, which is
    the rule `_narrate_files` applies to a take the run reused.
    """
    return [
        path for row in result.sections if not row.kept for path in (row.path, project.workspace.recording_log(row.key))
    ]


def _assemble_files(result: Any) -> list[Path]:
    """Each section as it was cut, then every file the result itself says it wrote.

    The result is the one place that knows what `assemble` wrote, so the cut list, the transcript
    and the poster cannot go missing from `written` by being forgotten in a second list here.
    """
    return [*(section.path for section in result.sections), *result.written]


def narrate(opts: opt.NarrateOptions) -> Outcome:
    """One narrate run, or with `--dry-run` the plan it would follow, which sends nothing.

    The stage itself plans, so both paths give one `NarrateResult` and the payload has one shape
    whether the run spent anything or not.
    """
    project = load_project(opts)
    result = voice(
        project,
        only=opts.only or None,
        force=opts.force,
        allow_placeholders=opts.allow_placeholders,
        silent=opts.no_voice,
        model=opts.model,
        dry_run=opts.dry_run,
    )
    # A dry run does the work of no section, so it reports the plan rather than a pair of zeroes a
    # reader would take for "nothing to voice".
    planned = plan_totals(result.plans, result.narration, result.rate)
    did = (
        {"would_synthesize": planned[TakeStatus.SYNTHESIZE.value], "cached": planned[TakeStatus.CACHED.value]}
        if opts.dry_run
        else {"synthesized": len(result.synthesized), "cached": len(result.cached)}
    )
    summary = {
        "sections": len(result.plans),
        **did,
        "total_seconds": result.takes.total_seconds if result.takes is not None else None,
    }
    written = [] if opts.dry_run else wrote(project.root, *_narrate_files(project, result))
    text = output.plan_report(result) if opts.dry_run else output.narrate_table(result)
    return of(result, project.root, summary, written, text)


def align(opts: opt.AlignOptions) -> Outcome:
    project = load_project(opts)
    try:
        result = resolve(project, allow_unknown_cues=opts.allow_unknown_cues)
    except UnknownCueError as err:
        # cue-times.json is already written, so the run reports the result like any other finding
        # rather than stopping before the table or the envelope is printed.
        result = err.result
    summary = {"sections": len(result.sections), "unresolved": result.unresolved, "unknown": result.unknown}
    written = wrote(project.root, result.cue_times_file)
    return of(result, project.root, summary, written, output.align_table(result))


def record(opts: opt.RecordOptions) -> Outcome:
    project = load_project(opts)
    result = capture(project, only=opts.only or None, seconds=opts.seconds, use_cues=not opts.no_cues)
    written = wrote(project.root, *_recorded_files(project, result))
    summary = {
        "sections": len(result.sections),
        "recorded": len(result.sections) - len(result.kept_sections),
        "kept": len(result.kept_sections),
    }
    return of(result, project.root, summary, written, output.record_table(result))


def assemble(opts: opt.AssembleOptions) -> Outcome:
    project = load_project(opts)
    result = cut_and_mix(project, soundscape=not opts.no_soundscape, loudness=not opts.no_loudness, strict=opts.strict)
    written = wrote(project.root, *_assemble_files(result))
    summary = {"sections": len(result.sections), "seconds": round(result.duration, 2)}
    text = f"{relative(result.final, project.root)}  ({result.duration:.2f}s)"
    return of(result, project.root, summary, written, text)


def verify(opts: opt.VerifyOptions) -> Outcome:
    project = load_project(opts)
    result = read_final(project, checks=list(opts.checks or []) or None, only=opts.only or None)
    summary = {"sections": len(result.starts), "cues": len(result.cues), "seconds": round(result.total_seconds, 2)}
    return of(result, project.root, summary, [], output.verify_table(result))


class StagePrefix(logging.Filter):
    """Put the stage a run is in at the front of every line it logs, for a person watching it.

    A long run's stderr is a wall of lines from whichever stage is working, and the prefix is what
    turns that into a place in the pipeline without anyone opening the progress log. It goes on the
    handler rather than on the logger, because a stage logs through a logger of its own and a child
    logger's record reaches the parent's handlers and never the parent's filters.
    """

    label: str = ""

    def filter(self, record: logging.LogRecord) -> bool:
        if self.label:
            record.msg = f"{self.label} {record.msg}"
        return True

    def follow(self, add: bool) -> None:
        """Start or stop prefixing, on every handler the run's lines pass through."""
        for handler in logging.getLogger("decktalk").handlers:
            handler.addFilter(self) if add else handler.removeFilter(self)


# The files each stage of a run wrote, read from that stage's own result. A stage with no row here
# wrote nothing this list can name.
STAGE_FILES: dict[Stage, Callable[[Any, Any], list[Path]]] = {
    Stage.NARRATE: _narrate_files,
    Stage.ALIGN: lambda project, result: [result.cue_times_file],
    Stage.RECORD: _recorded_files,
    Stage.ASSEMBLE: lambda project, result: _assemble_files(result),
}


def build(opts: opt.BuildOptions) -> Outcome:
    project = load_project(opts)
    plan = stage_plan(opts.from_stage, opts.to_stage)
    if opts.dry_run:
        # A plan that needs a file no earlier stage in it writes is a plan that cannot run, and the
        # dry run is the call a caller makes to learn exactly that.
        missing = [relative(path, project.root) for path in required_inputs(project, plan)]
        payload = {"stages": [stage.value for stage in plan], "missing": missing}
        table = " -> ".join(stage.value for stage in plan) + (f"\nmissing: {', '.join(missing)}" if missing else "")
        return Outcome(payload=payload, summary={"stages": len(plan)}, findings=Findings(certain=len(missing)),
                       rows=[Finding(detail=f"{name} is not there", verdict=Verdict.MISSING, where=name)
                             for name in missing], text=table)  # fmt: skip
    path = project.path(opts.progress) if opts.progress else project.workspace.progress_path
    done, made = [], [path]
    opened = time.monotonic()
    prefix = StagePrefix()
    prefix.follow(True)

    def show(stage: Stage, result: Any) -> None:
        """Open a stage on its own event, and close it with what it wrote and what a person reads.

        The progress log is `build`'s own, so nothing is written here: this is the stage prefix a
        person reads on stderr, the elapsed line, and the table each stage prints as it finishes.
        """
        nonlocal opened
        where = f"[{plan.index(stage) + 1}/{len(plan)} {stage.value}]"
        if result is None:
            opened = time.monotonic()
            prefix.label = where
            return
        done.append(stage)
        made.extend(STAGE_FILES[stage](project, result) if stage in STAGE_FILES else [])
        print(f"{where} done in {time.monotonic() - opened:.1f}s", file=sys.stderr)
        if not opts.json:
            table = output.stage_table(stage, result)
            if table:
                print(table)

    try:
        result = run_pipeline(
            project,
            silent=opts.no_voice,
            force=opts.force,
            only=opts.only or None,
            soundscape=not opts.no_soundscape,
            loudness=not opts.no_loudness,
            strict=opts.strict,
            allow_unresolved_cues=opts.allow_unresolved_cues,
            allow_unknown_cues=opts.allow_unknown_cues,
            from_stage=opts.from_stage,
            to_stage=opts.to_stage,
            progress_path=path,
            report=show,
        )
    finally:
        prefix.label = ""
        prefix.follow(False)
    assembly = result.assembly
    written = wrote(project.root, *made)
    summary = {"stages": len(done), "seconds": round(assembly.duration, 2) if assembly else None}
    text = f"built {relative(assembly.final, project.root)}" if assembly else "nothing was assembled"
    return of(result, project.root, summary, written, text)
