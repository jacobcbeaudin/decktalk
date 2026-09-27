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

Whether the film and its measurement still stand is this module's rule, and `build` asks it. A build
leaves `kept.json` under the build directory, which holds a digest of everything `assemble` and
`verify` read the last time they ran, what they wrote and what they found. A build whose digest
matches keeps both stages rather than repeating them. The digests are over file contents and never
over file times, because a copy or a checkout moves every time and changes no byte.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError

from decktalk.errors import DeckTalkError
from decktalk.events import Level, Line, StageStart
from decktalk.findings import MODEL, Code, Finding, Location
from decktalk.inputs import ClipSection, Inputs, PageSection, Section
from decktalk.inputs.paths import at
from decktalk.inputs.workspace import EVENTS_SUFFIX
from decktalk.machine import Run
from decktalk.media import ffmpeg
from decktalk.page import Q
from decktalk.pipeline import PIPELINE, Artifact, Stage
from decktalk.results import LiveRun, SectionKind, SectionStatus, StatusResult
from decktalk.settings import PROJECT_FILE
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


KEPT_FILE = "kept.json"
"""What the record of the last assemble and verify is called, under the project's build directory."""

PACKAGE = "decktalk"
"""The distribution whose version every digest carries, so a new engine never keeps an old engine's film."""

DIGEST = "blake2b"
"""The hash every kept digest is taken with, which is fast on large files and in the standard library."""

FIELD_END = b"\0"
"""What ends each field of a digest, which no path, no version and no JSON text contains."""


class KeptStage(BaseModel):
    """What one stage read, what it wrote and what it found the last time a build ran it."""

    model_config = MODEL

    key: str = Field(description="The digest of everything the stage read, with the options it was run with.")
    options: dict[str, JsonValue] = Field(description="The options the stage was run with, as the build passed them.")
    outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Each file the stage wrote, project-relative, against the digest of its bytes.",
    )
    findings: tuple[Finding, ...] = Field(
        default=(),
        description="What the stage found, which a run that keeps the stage reports again.",
    )


class Kept(BaseModel):
    """The record a build leaves of the two stages it can keep, read by the next build and by status."""

    model_config = MODEL

    assemble: KeptStage | None = None
    verify: KeptStage | None = None


def read_kept(inputs: Inputs) -> Kept:
    """The record the last build left, or an empty one when there is none or it cannot be read.

    A record this version cannot read keeps nothing, which costs one assemble and one verify and is
    never wrong, so it is not worth a refusal.
    """
    path = kept_path(inputs)
    try:
        return Kept.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return Kept()


def write_kept(inputs: Inputs, kept: Kept) -> Path:
    """Write the record a build leaves, and give back where it went."""
    path = kept_path(inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kept.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def kept_path(inputs: Inputs) -> Path:
    """Where this project keeps its record, which is under the build directory the project names."""
    return inputs.workspace.build / KEPT_FILE


def assemble_key(inputs: Inputs, options: Mapping[str, JsonValue]) -> str:
    """The digest of everything `assemble` reads, with the options a build runs it with.

    The list is deliberately wide: the project file, the script, the cue file, every file the local
    origin serves, the take index and the joined narration, the cue times, every recording and every
    generated sound, every setting in force and the engine's own version. A digest that missed an
    input would ship a film the inputs no longer describe, and one that reads too much costs only a
    repeated assemble.
    """
    fields = [_engine(), json.dumps(dataclasses.asdict(inputs.settings), sort_keys=True, default=str)]
    fields.append(json.dumps(options, sort_keys=True))
    for path in _assemble_reads(inputs):
        fields += [inputs.relative(path).as_posix(), digest_of(path) if path.is_file() else ""]
    return _digest(fields)


def verify_key(inputs: Inputs, assembled: str, options: Mapping[str, JsonValue]) -> str:
    """The digest of what `verify` measures: the film's own bytes, what made it, and the options.

    What made the film is the assemble digest, which already carries every setting and every file
    the measurement is judged against, so the film is the only input added here.
    """
    film = inputs.workspace.film
    return _digest([assembled, digest_of(film) if film.is_file() else "", json.dumps(options, sort_keys=True)])


def outputs_of(inputs: Inputs, paths: Iterable[Path]) -> dict[str, str]:
    """Each of these files that is on disk, project-relative, against the digest of its bytes."""
    return {inputs.relative(path).as_posix(): digest_of(path) for path in paths if path.is_file()}


def intact(inputs: Inputs, stage: KeptStage) -> bool:
    """Whether every file a kept stage wrote is still on disk with the bytes it wrote.

    A stage run on its own after the build, such as `decktalk assemble --no-loudness`, rewrites the
    film without touching the record, and this is what stops the next build keeping that film.
    """
    return all(
        (inputs.root / name).is_file() and digest_of(inputs.root / name) == digest
        for name, digest in stage.outputs.items()
    )


def assembled(inputs: Inputs, kept: Kept) -> str | None:
    """The assemble digest of the film on disk, or None when that film no longer stands.

    The film stands when the last build's assemble read exactly what is on disk now and wrote
    exactly the files that are there, which is the one question both a keeping build and this
    report ask before they trust a measurement of it.
    """
    record = kept.assemble
    if record is None or not holds_film(inputs, record):
        return None
    return record.key if assemble_key(inputs, record.options) == record.key else None


def holds_film(inputs: Inputs, record: KeptStage) -> bool:
    """Whether a kept assemble wrote the film on disk, byte for byte, along with everything beside it."""
    return inputs.relative(inputs.workspace.film).as_posix() in record.outputs and intact(inputs, record)


def digest_of(path: Path) -> str:
    """The digest of one file's bytes, read in blocks so a long film is never held in memory."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, DIGEST).hexdigest()


def _digest(fields: Iterable[str]) -> str:
    """The digest of several fields, each ended by a byte no field holds so two cannot run together."""
    hashed = hashlib.new(DIGEST)
    for text in fields:
        hashed.update(text.encode("utf-8"))
        hashed.update(FIELD_END)
    return hashed.hexdigest()


def _engine() -> str:
    """This engine's version, or a word that keeps nothing when the package is not installed."""
    try:
        return version(PACKAGE)
    except PackageNotFoundError:
        return "unknown"


def _assemble_reads(inputs: Inputs) -> list[Path]:
    """Every file `assemble` may read, each once, in an order that depends on nothing but their names."""
    workspace = inputs.workspace
    named = [inputs.root / PROJECT_FILE, inputs.script_path, inputs.cues_path]
    named += [inputs.root / served for served in inputs.served_paths()]
    named += [workspace.takes_path, workspace.narration_path, workspace.cue_times_path]
    named += [workspace.recordings_dir, workspace.soundscape_dir]
    files: dict[Path, None] = {}
    for path in named:
        for found in _files(path):
            files[found] = None
    return sorted(files, key=lambda path: path.as_posix())


def _files(path: Path) -> list[Path]:
    """The files one named path stands for, which is itself, everything under it, or nothing."""
    if path.is_dir():
        return [found for found in path.rglob("*") if found.is_file()]
    return [path]


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
        # `voiced` is the take's own word, so a placeholder take on disk is not one a voice spoke
        # and the column that says what this project has paid for never counts it.
        voiced = on_disk and take is not None and take.voiced and (said is None or take.spoken == said)
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


__all__ = [
    "BUILT",
    "Kept",
    "KeptStage",
    "assemble_key",
    "assembled",
    "live_runs",
    "next_command",
    "read_kept",
    "section_rows",
    "source_of",
    "status",
    "verify_key",
    "write_kept",
]
