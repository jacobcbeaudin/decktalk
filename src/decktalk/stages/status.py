"""What the project's files say, what is built from them, what has gone stale, and what to do next.

    decktalk.toml   what this presentation is
    script.md       the narration this project speaks
    cues.json       which spoken phrase each moment lands on
    build/          everything the six stages have written so far

This is the one command that judges by reading rather than by producing. Reading the four input
files against each other is its work rather than its obstacle, so a file the loaders refuse is
reported here and never raised: a report that stopped on the first broken file would tell an author
about one problem at a time, and the point of asking is to learn all of them at once.

What to do next is read from `PIPELINE` and never from a chain of tests of its own. Each stage
declares the artifact it writes, so the first artifact that is not on disk names the stage that
writes it, and a stage added to the pipeline reaches this report with no line changed here.

Whether a recording still stands is `record`'s rule, asked of `record`, because one rule decides
what a run skips and what this report calls stale and neither compares file times.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from decktalk.errors import DeckTalkError
from decktalk.events import Level, Line, StageStart
from decktalk.findings import Code, Location
from decktalk.inputs import ClipSection, Inputs, PageSection, Section
from decktalk.inputs.paths import at
from decktalk.inputs.workspace import EVENTS_SUFFIX
from decktalk.machine import Run
from decktalk.media import ffmpeg
from decktalk.page import Q
from decktalk.pipeline import PIPELINE, Artifact, Stage
from decktalk.results import LiveRun, SectionKind, SectionStatus, StatusResult
from decktalk.stages import judge
from decktalk.stages.record import stale_recording

LINE = TypeAdapter(Line)
"""The one reader of an event file, so a line this library cannot read is never taken for a run."""

DRAFT = {Stage.NARRATE: "--no-voice"}
"""The flag that makes a stage's first move the cheap one, which is the unpaid draft of the voice.

A project with no take index at all has never been narrated, so the move it is told to make is the
rehearsal that spends nothing. Every other stage costs only time, and so is named on its own.
"""

BUILT: dict[Artifact, Callable[[Inputs], bool]] = {
    Artifact.TAKES: lambda inputs: inputs.workspace.takes_path.is_file(),
    Artifact.CUE_TIMES: lambda inputs: inputs.workspace.cue_times_path.is_file(),
    Artifact.RECORDINGS: lambda inputs: all(
        inputs.workspace.recording(section.key).is_file() for section in inputs.document.page_sections
    ),
    # A project that describes no soundscape has nothing to generate, so that stage is never next.
    Artifact.SOUNDSCAPE: lambda inputs: inputs.document.soundscape.empty or _holds(inputs.workspace.soundscape_dir),
    # The film is what the final directory is for, and a directory a stopped run left behind is not one.
    Artifact.FINAL: lambda inputs: inputs.workspace.film.is_file(),
}
"""When each artifact the pipeline declares counts as built, which is one sentence of arithmetic each.

The path comes from the workspace rather than from the artifact's own value, because `[project]
build` may put the whole build directory somewhere else and the workspace is what knows where.
"""


def _holds(directory: Path) -> bool:
    """Whether a directory artifact holds anything at all, which is what makes it written."""
    return directory.is_dir() and any(directory.iterdir())


def next_command(inputs: Inputs) -> str:
    """The whole command to run next, read off the pipeline rather than worked out here.

    The stages run in a fixed order and each declares what it writes, so the first artifact that is
    not on disk names the stage that writes it. A project with everything built is told to measure
    it, because the last thing to do with a finished film is to check that it kept its promises.
    """
    for spec in PIPELINE:
        for artifact in spec.writes:
            if not BUILT[artifact](inputs):
                stage = artifact.written_by or spec.stage
                flag = DRAFT.get(stage)
                return f"decktalk {stage.value} {flag}" if flag else f"decktalk {stage.value}"
    return f"decktalk {Stage.VERIFY.value}"


def source_of(section: Section) -> str:
    """The page or the file this section plays, as a reader would open it.

    A page is named with the scene it plays, because two sections of one deck differ by nothing
    else, and the word for that is the query key the runtime publishes rather than a second spelling
    of it here.
    """
    if isinstance(section, PageSection):
        return f"{section.page}?{Q.SCENE.value}={section.scene}"
    return section.clip


def voiced_text(inputs: Inputs) -> dict[int, str]:
    """What each section's script says now, so a take of older words is not reported as this one's.

    A script that will not parse is reported by `judgements`, and every section then falls back to
    holding a take at all, which is still true and is all this report can honestly say.
    """
    try:
        return {segment.index: segment.spoken for segment in inputs.script()}
    except DeckTalkError:
        return {}


def section_rows(inputs: Inputs, run: Run) -> tuple[SectionStatus, ...]:
    """One row per section: what it plays, what is on disk for it, and whether that is still true."""
    takes = inputs.takes()
    spoken = voiced_text(inputs)
    rows: list[SectionStatus] = []
    for section in inputs.document.sections:
        take = takes.of(section.number) if takes is not None else None
        on_disk = take is not None and (inputs.workspace.takes_dir / take.file).is_file()
        said = spoken.get(section.number)
        voiced = on_disk and (said is None or take is None or take.spoken == said)
        recorded = isinstance(section, PageSection) and inputs.workspace.recording(section.key).is_file()
        rows.append(
            SectionStatus(
                section=section.number,
                key=section.key,
                kind=SectionKind.CLIP if section.is_clip else SectionKind.PAGE,
                source=source_of(section),
                voiced=voiced,
                recorded=recorded,
                cut=inputs.workspace.section_video(section.key).is_file(),
                stale=_stale(inputs, run, section, recorded=recorded),
            )
        )
    return tuple(rows)


def _stale(inputs: Inputs, run: Run, section: Section, *, recorded: bool) -> bool:
    """Whether this section's recording no longer matches the project, with the reason as a line.

    The row carries the fact and the stream carries the sentence, because a reader that dispatches
    on a boolean still wants to be told which of the several things that moved.
    """
    if not recorded or not isinstance(section, PageSection):
        return False
    why = stale_recording(inputs, section)
    if why is None:
        return False
    run.note(f"{why[0].upper()}{why[1:]}, so it would be recorded again.", level=Level.WARNING)
    return True


def judgements(inputs: Inputs, run: Run) -> None:
    """Every file the project names and has not got, and every file it has that will not parse.

    A file that is absent is a certain `FILE_MISSING`, because the next command cannot read it. A
    file that is there and will not parse has no code in the frozen list, so it is one sentence on
    the stream carrying the loader's own words, its file and its line.
    """
    _judge_input(inputs, run, inputs.script_path, inputs.script, missing=True)
    _judge_input(inputs, run, inputs.cues_path, inputs.cues, missing=False)
    for section in inputs.document.sections:
        named = section.page if isinstance(section, PageSection) else section.clip
        optional = isinstance(section, ClipSection) and section.optional
        path = inputs.path(named)
        if path.is_file() or optional:
            continue
        what = "page" if isinstance(section, PageSection) else "clip"
        run.found(
            judge(
                Code.FILE_MISSING,
                f"section {section.number} plays the {what} {named}, which is not on disk.",
                Location(where=named, file=inputs.relative(path), section=section.number),
            )
        )
    for stray in inputs.stray_section_videos():
        gone = inputs.relative(stray)
        run.note(f"{gone} is a cut of a section this project no longer has, so no film uses it.", level=Level.WARNING)


def _judge_input(inputs: Inputs, run: Run, path: Path, load: Callable[[], object], *, missing: bool) -> None:
    """One authored file read for this report, whose absence may be a finding and whose refusal is a line."""
    if not path.is_file():
        if missing:
            run.found(
                judge(
                    Code.FILE_MISSING,
                    f"{inputs.relative(path)} is not on disk, so this project speaks nothing.",
                    at(path, inputs.root),
                )
            )
        return
    try:
        load()
    except DeckTalkError as refused:
        where = refused.location
        line = f" Line {where.line}." if where is not None and where.line is not None else ""
        hint = f" {refused.hint}" if refused.hint else ""
        run.note(f"{inputs.relative(path)} will not parse: {refused}{line}{hint}", level=Level.ERROR)


def live_runs(inputs: Inputs, run: Run) -> tuple[LiveRun, ...]:
    """Every other run of this project whose events file has not closed, oldest first.

    A run is over when it has written its `run.done` line, so a build somebody left going is found
    by reading what it has written rather than by asking the machine about a process id. This run's
    own file is left out, because a caller asking what is running wants the runs it is not making.
    """
    directory = inputs.workspace.events_dir
    if not directory.is_dir():
        return ()
    mine = inputs.workspace.events_path(run.id).name
    rows: list[LiveRun] = []
    for path in sorted(directory.glob(f"*{EVENTS_SUFFIX}")):
        if path.name == mine:
            continue
        found = _live_run(inputs, run, path)
        if found is not None:
            rows.append(found)
    return tuple(sorted(rows, key=lambda row: row.started))


def _live_run(inputs: Inputs, run: Run, path: Path) -> LiveRun | None:
    """The run one events file describes, or None when it has closed or cannot be read."""
    lines: list[Line] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                lines.append(LINE.validate_json(raw))
    except (OSError, ValidationError, json.JSONDecodeError) as refused:
        run.note(f"{inputs.relative(path)} is not a run this version can read ({refused}).", level=Level.WARNING)
        return None
    if not lines or any(line.event == "run.done" for line in lines):
        return None
    # A stage a run opened is the stage it is in until it opens the next one, so the last one it
    # opened is where it is now, whether or not that stage has written its own closing line.
    staged = [line.stage for line in lines if isinstance(line, StageStart)]
    return LiveRun(
        run=lines[0].run,
        events=inputs.relative(path),
        started=lines[0].time,
        stage=staged[-1] if staged else None,
    )


def status(inputs: Inputs, run: Run) -> StatusResult:
    """Report what is written, what is built, what is stale, and what to do next.

    Nothing is written and only the built film is measured, because how long a film runs is a fact
    about its own bytes and the cut list beside it is a record of what a run meant to write.
    """
    judgements(inputs, run)
    film = inputs.workspace.film
    built = film.is_file()
    return run.result(
        StatusResult,
        name=inputs.document.name,
        script=inputs.relative(inputs.script_path),
        cues=inputs.relative(inputs.cues_path),
        sections=section_rows(inputs, run),
        film=inputs.relative(film) if built else None,
        film_seconds=ffmpeg.probe_duration(film) if built else None,
        runs=live_runs(inputs, run),
        next=next_command(inputs),
    )


__all__ = ["BUILT", "live_runs", "next_command", "section_rows", "source_of", "status"]
