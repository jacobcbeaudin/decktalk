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
writes it, and a stage added to the pipeline reaches this report with no line changed here. Once
everything is on disk, anything that moved since it was built names `build`, which keeps what did
not move and redoes the rest.

The takes directory keeps every take the project ever bought, so after a voice change or an edit it
holds takes no section plays. This report lists them with their size and never removes one, because
each is a paid record and its author decides with `git rm`. A take counts as played when the take
index or a section's current digest names it, so a report that cannot name the digests, with no
voice named, claims nothing about any take.

Whether a recording still stands is `record`'s rule, asked of `record`, because one rule decides
what a run skips and what this report calls stale and neither compares file times.

Whether the film and its measurement still stand is `kept.py`'s rule, which `build` asks too, so
this report names nothing next once the film on disk is the one the last build measured.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from decktalk.artifacts import WORDS_SUFFIX, is_placeholder
from decktalk.artifacts.takes import TAKE_DIGEST
from decktalk.errors import DeckTalkError
from decktalk.events import AnyEvent, Level, StageStart
from decktalk.findings import Code, Location, judge
from decktalk.inputs import ClipSection, Inputs, PageSection, Section
from decktalk.inputs.paths import at
from decktalk.inputs.workspace import EVENTS_SUFFIX
from decktalk.machine.run import Run
from decktalk.media import ffmpeg
from decktalk.page import Q
from decktalk.pipeline import PIPELINE, Stage
from decktalk.results import LiveRun, SectionKind, SectionStatus, StatusResult, UnplayedTakes
from decktalk.stages import voice_model
from decktalk.stages.kept import BUILT, assembled, read_kept, verify_digest
from decktalk.stages.narrate.plan import named_voice, take_inputs
from decktalk.stages.record import stale_recording

log = logging.getLogger(__name__)

LINE = TypeAdapter(AnyEvent)
"""The one reader of an event file, so a line this library cannot read is never taken for a run."""

ALIGNED_DIR = "aligned"
"""The folder inside the takes directory that aligned words are kept in, beside the takes they time."""

BUILD = "build"
"""The command a stale project runs, which redoes what moved and keeps every stage whose inputs did not."""

DRAFT = {Stage.NARRATE: "--no-spend"}
"""The flag that makes a stage's first move the cheap one, which is the unpaid draft of the voice.

A project with no take index at all has never been narrated, so the move it is told to make is the
rehearsal that spends nothing. Every other stage costs only time, and so is named on its own.
"""


def next_command(inputs: Inputs, *, stale: bool = False) -> str | None:
    """The whole command to run next, read off the pipeline rather than worked out here, or None.

    The stages run in a fixed order and each declares what it writes, so the first artifact that is
    not on disk names the stage that writes it. Once everything is on disk, a project where anything
    has moved since it was built is told to build, because a build keeps every stage whose inputs
    did not move and redoes the rest, and a lone `verify` would measure a film its inputs no longer
    describe. `stale` is what the section rows already found, so the recordings are judged once.
    A film the last build made and measured from exactly these inputs leaves nothing to do, and a
    film nobody has measured yet is told to be measured.
    """
    for spec in PIPELINE:
        for artifact in spec.writes:
            if not BUILT[artifact](inputs):
                stage = artifact.written_by or spec.stage
                flag = DRAFT.get(stage)
                return f"decktalk {stage.value} {flag}" if flag else f"decktalk {stage.value}"
    kept = read_kept(inputs)
    made = assembled(inputs, kept)
    if stale or _words_moved(inputs) or (kept.assemble is not None and made is None):
        return f"decktalk {BUILD}"
    measured = kept.verify
    if made is not None and measured is not None and verify_digest(inputs, made, measured.options) == measured.digest:
        return None
    return f"decktalk {Stage.VERIFY.value}"


def _words_moved(inputs: Inputs) -> bool:
    """Whether any spoken section's take says other words than the script does now, or is missing."""
    takes = inputs.takes()
    if takes is None:
        return True
    for section, said in voiced_text(inputs).items():
        if section in inputs.document.clip_numbers:
            continue
        take = takes.of(section)
        if take is None or take.spoken != said:
            return True
    return False


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
        return {section.number: section.spoken for section in inputs.script()}
    except DeckTalkError as unread:
        log.debug("The script did not parse, so no take is judged by its words.", exc_info=unread)
        return {}


def section_rows(inputs: Inputs, run: Run) -> tuple[SectionStatus, ...]:
    """One row per section: what it plays, what is on disk for it, and whether that is still true."""
    takes = inputs.takes()
    spoken = voiced_text(inputs)
    rows: list[SectionStatus] = []
    for section in inputs.document.sections:
        take = takes.of(section.number) if takes is not None else None
        on_disk = take is not None and inputs.workspace.take_path(take.digest).is_file()
        said = spoken.get(section.number)
        # `voiced` is the take's own word, so a placeholder take on disk is not one a voice spoke
        # and the column that says what this project has paid for never counts it.
        bought = on_disk and take is not None and take.voiced
        voiced = bought and (said is None or take.spoken == said)
        recorded = isinstance(section, PageSection) and inputs.workspace.recording(section.key).is_file()
        rows.append(
            SectionStatus(
                section=section.number,
                key=section.key,
                kind=SectionKind.CLIP if section.is_clip else SectionKind.PAGE,
                source=source_of(section),
                voiced=voiced,
                voiced_stale=bought and not voiced,
                recorded=recorded,
                assembled=inputs.workspace.section_video(section.key).is_file(),
                stale=_stale(inputs, run, section, recorded=recorded),
            )
        )
    return tuple(rows)


def _stale(inputs: Inputs, run: Run, section: Section, *, recorded: bool) -> bool:
    """Whether this section's recording no longer matches the project, with the reason as a line."""
    if not recorded or not isinstance(section, PageSection):
        return False
    why = stale_recording(inputs, section)
    if why is None:
        return False
    run.note(f"{why[0].upper()}{why[1:]}, so it would be recorded again.", level=Level.WARNING)
    return True


def judgements(inputs: Inputs, run: Run) -> None:
    """Every file the project names and has not got, and every file it has that will not parse.

    A file that is absent is a `FILE_MISSING` error, because the next command cannot read it. A
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
    lines: list[AnyEvent] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                lines.append(LINE.validate_json(raw))
    except (OSError, ValidationError) as refused:
        run.note(f"{inputs.relative(path)} is not a run this version can read ({refused}).", level=Level.WARNING)
        return None
    if not lines or any(line.event == "run.done" for line in lines):
        return None
    # A stage a run opened is the stage it is in until it opens the next one, so the last one it
    # opened is where it is now, whether or not that stage has written its own closing line.
    staged = [line.stage for line in lines if isinstance(line, StageStart)]
    return LiveRun(
        run=lines[0].run,
        events_file=inputs.relative(path),
        started=lines[0].time,
        stage=staged[-1] if staged else None,
    )


def played_takes(inputs: Inputs) -> set[str] | None:
    """Every take a section plays, or None when the voice is unnamed and the script's own takes cannot be named.

    A take is played when the take index names it, since the film plays that take until narrate runs
    again, or when a section's current text, voice and settings name it, since the next narrate plays
    that one. Both are counted, so no take the film still needs is ever called unplayed.
    """
    voice_id = named_voice(inputs)
    if not voice_id:
        return None
    provider = inputs.settings.voice.provider
    try:
        model = voice_model(inputs)
        named = {
            take_inputs(inputs, section, provider=provider, voice_id=voice_id, model=model).digest
            for section in inputs.spoken()
        }
    except DeckTalkError as unread:
        log.debug("The script's takes could not be named, so no take is called unplayed.", exc_info=unread)
        return None
    index = inputs.takes()
    return named | ({row.digest for row in index.sections} if index is not None else set())


def _take_of(name: str) -> str | None:
    """The digest of the voiced take a file in the takes directory belongs to, or None when it is not one.

    A take's audio is its digest and one suffix, and its words file is its digest and `.words.json`,
    so a copy set aside as `.unreadable`, or any file of the author's own, names no take.
    """
    digest, _, rest = name.partition(".")
    if re.fullmatch(TAKE_DIGEST, digest) is None or is_placeholder(digest):
        return None
    return digest if rest == WORDS_SUFFIX.removeprefix(".") or rest.isalnum() else None


def unplayed_takes(inputs: Inputs) -> UnplayedTakes | None:
    """The takes and aligned words in the takes directory that no section plays, read and never touched.

    Each is a paid record or the measured timing of one, so DeckTalk lists them for their author to
    remove with `git rm` and deletes none of them. No stage of this version reads aligned words, so
    every file under `aligned/` is one no section plays. A file that is not named like a take, such
    as a damaged copy set aside as `.unreadable`, is not a take and is left out.
    """
    played = played_takes(inputs)
    if played is None:
        return None
    takes = inputs.workspace.takes
    named = ((path, _take_of(path.name)) for path in sorted(takes.iterdir() if takes.is_dir() else ()))
    pairs = [(path, digest) for path, digest in named if digest is not None and path.is_file()]
    gone = {digest for _path, digest in pairs if digest not in played}
    unplayed = [path for path, digest in pairs if digest in gone]
    aligned_dir = takes / ALIGNED_DIR
    aligned = sorted(aligned_dir.glob(f"*{WORDS_SUFFIX}")) if aligned_dir.is_dir() else []
    aligned = [path for path in aligned if path.is_file()]
    files = sorted([*unplayed, *aligned])
    return UnplayedTakes(
        directory=inputs.relative(takes),
        takes=len(gone),
        aligned=len(aligned),
        bytes=sum(path.stat().st_size for path in files),
        files=tuple(inputs.relative(path) for path in files),
    )


def status(inputs: Inputs, run: Run) -> StatusResult:
    """Report what is written, what is built, what is stale, and what to do next.

    Nothing is written and only the built film is measured, because how long a film runs is a fact
    about its own bytes and the placements beside it are a record of what a run meant to write.
    """
    judgements(inputs, run)
    rows = section_rows(inputs, run)
    film = inputs.workspace.film
    built = film.is_file()
    return run.result(
        StatusResult,
        name=inputs.document.name,
        script=inputs.relative(inputs.script_path),
        cues_file=inputs.relative(inputs.cues_path),
        sections=rows,
        film=inputs.relative(film) if built else None,
        film_seconds=ffmpeg.probe_duration(film) if built else None,
        runs=live_runs(inputs, run),
        unplayed=unplayed_takes(inputs),
        next=next_command(inputs, stale=any(row.stale for row in rows)),
    )


__all__ = [
    "live_runs",
    "next_command",
    "section_rows",
    "source_of",
    "played_takes",
    "status",
    "unplayed_takes",
]
