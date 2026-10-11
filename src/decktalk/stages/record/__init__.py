"""Stage 3: make each recorded section's picture, a page's one frame at a time, and judge the result.

One command does the whole of it. Through `decktalk.pictures` it opens a page section on the local
origin with the section's resolved cues and spoken words and steps its section clock through its span
in the narration one frame at a time, or runs the program a render section names, judges the frames
and what the page or the program reported, and writes all of that to
`build/recordings/NN.json` before it moves on to the next section. Frame zero of every recording is
second zero of its section, so there is no start to find. The measurement belongs to the recording
beside it, and a long run can be read while it runs.

    capture.py   what a recording is made from, and the page cut into its scenes
    render.py    the clips a project makes
    checks.py    the frames, the page's own codes and the origins it reached for
    pool.py      how many recorders work at once, and the chunks each section is split into

Frame `k` of a page section is the page at second `k / fps` however it was reached, so each page
section is split into chunks of its frames and several recorders, each a worker with pictures of its
own whose Chromium is launched on its first page section, record chunks at once, longest first across
every section. Each chunk is a lossless piece of its own starting on a keyframe, and the recorder
that finishes a section's last chunk joins the pieces into the section's video without an encode. A
section too short to repay another page load is one chunk, and a render section's program draws its
whole video as one.
The rows, the progress lines and the result come back in section order whatever order the recorders
finish in.

Before anything is recorded, every clip the project makes and has not got is made, because a pane
plays its clip while the page is stepped and the section is keyed on the clip's content.

A section none of whose parts has moved is kept rather than recorded again, because the run would
produce the same pixels. The parts are its turns' text, voice and word times, the markup of its own
scene, the part of the page every scene shares, its loaded assets, its cues, its length, the frame,
the colour scheme, the page policy, the motion it renders with and the engine. They are cut per scene
and per turn, so an edit to one slide or one turn records the sections it touches, leaves the rest of
the page's sections alone, and is named by the parts that moved.
A section named by `--section` is always recorded, which is how an author asks for another take of a
page that has not changed.

The order the recorder writes in is the whole of its safety. A section's new video is drafted beside
the one it replaces, as every file a program writes is, so a recording that fails leaves the last
video and its log as they were. Once the draft is whole, the old log goes, the draft is moved onto
the video, and the log of what was just recorded is written last, so a crash can never leave a new
picture under an old judgement.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from decktalk.artifacts import RecordingChecks, RecordingLog, UnreadableCache, UnreadablePaidRecord
from decktalk.errors import STOPS, InputError, ToolError
from decktalk.events import Level, SectionDone, SectionStart, Unit
from decktalk.files import drafted
from decktalk.findings import Finding
from decktalk.inputs import Inputs, RecordedSection, RenderSection, programs
from decktalk.logs import cache_decision, within
from decktalk.machine.run import Run
from decktalk.media import stepping
from decktalk.media.pagereport import Recording
from decktalk.media.pages import STOPPED_HINT
from decktalk.page import SECOND_DIGITS
from decktalk.pictures import Pictures, pictures, splits
from decktalk.pipeline import Artifact, Outcome, Stage
from decktalk.results import RecordResult, SectionRecording
from decktalk.stages import page_seconds, selects
from decktalk.stages.pool import Halt, Pool
from decktalk.stages.record import render
from decktalk.stages.record.capture import (
    Job,
    TurnText,
    plan_job,
    said,
)
from decktalk.stages.record.checks import check_recording, recording_findings
from decktalk.stages.record.pool import (
    Chunk,
    Span,
    automatic,
    available_cpus,
    chunked,
    encode_threads,
    run_deadline,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class LogSink:
    """Where one section's recording log is kept, cleared as its video is replaced and written after it.

    The recorder that finishes a section clears this just before its whole draft is moved onto the
    video, and writes it once the video is in place, so no log ever judges a video it was not
    written for. What is written
    then is everything the recorders know, and the stage writes the log again with the frame
    measurements and the judgements as soon as it has measured them, so a run stopped in between
    leaves a log with no checks in it, which the next run reads as a section it has not finished
    recording.
    """

    inputs: Inputs
    job: Job

    def clear(self) -> None:
        """Delete the log of the recording that is about to be replaced, before its video is."""
        self.job.log_path.unlink(missing_ok=True)

    def write(self, recording: Recording) -> None:
        """Write what the recorder knows about the video now on disk, before anything measures it."""
        self.log(recording).write(self.job.log_path)

    def log(
        self,
        recording: Recording,
        *,
        checks: RecordingChecks | None = None,
        findings: tuple[Finding, ...] = (),
    ) -> RecordingLog:
        """The log of one recording, with whatever the stage has measured of it so far.

        The parts are taken again over what the page really loaded, which the plan's parts may not
        count, and are the parts the plan took for a section a command draws.
        """
        return RecordingLog(
            section=self.job.section.number,
            made_from=self.job.made_from_now(self.inputs, list(recording.assets)),
            recording=recording,
            findings=findings,
            checks=checks,
        )


def stale_recording(inputs: Inputs, section: RecordedSection, script: Sequence[TurnText] | None = None) -> str | None:
    """Why the recording on disk for this section no longer matches the project, or None when it does.

    The reason is the opening clause of a sentence, capitalised, which a line goes on after with its own ending.

    This is the one rule that decides whether a recording still stands, so what `record` skips and
    what `status` calls stale are the same question answered once, and neither compares file times.
    The reason names the parts that moved, such as the words of one turn or the markup of the
    section's own scene, and never a list of everything that could have. `script` gives the turns'
    text and voice as the script has them, which `status` passes so a turn edited since its take was
    made is named before narrate has run. Without it the turns are their takes, which is what a
    `record` run plays.
    """
    takes = inputs.takes()
    seconds = page_seconds(takes, section, inputs.settings.video.fps) if takes is not None else 0.0
    if not seconds:
        return f"Section {section.number} has no narration span yet"
    cue_times = inputs.cue_times()
    job = plan_job(inputs, section, cue_times, seconds, script)
    if job.unchanged:
        return None
    if not job.out.exists():
        return f"Section {section.number} has no recording"
    if job.previous is None:
        return f"Section {section.number} has no log of what it was recorded from"
    if job.previous.checks is None:
        return f"Section {section.number} was stopped before its recording was measured"
    return f"Section {section.number}: {said(job.moved)} changed since it was recorded"


def plan(inputs: Inputs, run: Run, only: Sequence[int] | None) -> list[Job]:
    """One job per page section this run considers, in section order.

    A selection no page section matches is refused with the page sections named, as narrate names
    the spoken ones. A section with no span in the take index has no length to record, so it is named
    in one sentence and left out rather than recorded for a length nobody stated.
    """
    takes = inputs.takes(required=True)
    cue_times = inputs.cue_times()
    wanted = selects(only)
    chosen = [section for section in inputs.document.recorded_sections if wanted(section.number)]
    if only is not None and not chosen:
        every = [section.number for section in inputs.document.recorded_sections]
        raise InputError(
            f"no page section matches {list(only)}, so there is nothing to record.",
            hint=f"The page sections are {every}.",
        )
    planned: list[Job] = []
    fps = inputs.settings.video.fps
    for section in chosen:
        seconds = page_seconds(takes, section, fps)
        if not seconds:
            run.note(f"Section {section.number} has no narration span yet, so it is not recorded.", level=Level.WARNING)
            continue
        planned.append(plan_job(inputs, section, cue_times, seconds))
    if not planned:
        raise InputError(
            "no page section has a length to record.",
            hint=f"{Artifact.TAKES.next_step} Or name a section that plays a page.",
        )
    return planned


def passed_over(inputs: Inputs, run: Run, only: Sequence[int] | None) -> None:
    """Report every section this run did not name whose recording no longer matches the project.

    A run that names sections says nothing about the others, and the film is assembled from all of
    them, so a section left behind out of date is reported rather than left for a reader to notice
    by eye. A section with no recording has no picture to keep, and its absence is no finding of a run
    that did not touch it, so it is passed over before any of its inputs is read: `status` reports
    it. A section whose words do not read cannot be judged, whether they are the provider's or the
    ones DeckTalk estimated for a placeholder, and this run records nothing from them, so that is a
    line naming the file rather than a refusal: the run that records that section is the one refused
    over them.
    """
    if not only:
        return
    named = set(only)
    for section in inputs.document.recorded_sections:
        if section.number in named or not inputs.workspace.recording(section.key).exists():
            continue
        try:
            why = stale_recording(inputs, section)
        except (UnreadablePaidRecord, UnreadableCache) as unread:
            run.note(
                f"Section {section.number} cannot be judged, because {inputs.relative(unread.path).as_posix()} "
                "does not read, and this run did not name it, so the film keeps the picture recorded before.",
                level=Level.WARNING,
            )
            continue
        if why is None:
            continue
        run.note(
            f"{why}, and this run did not name it, so the film keeps the picture recorded before.",
            level=Level.WARNING,
        )


@dataclass(eq=False)
class Underway:
    """One section whose chunks the recorders are recording, which the recorder that finishes the last one joins.

    The section opens on the stream when its first chunk starts. Each chunk is recorded into a piece
    beside the section's video. The recorder that finishes the last chunk joins the pieces into a
    draft, puts it in place of the video and its log, writes the log of what was recorded, measures
    and judges it, writes the log again with the judgements, and closes the section on the stream, so
    no recorder ever waits on another.
    """

    inputs: Inputs
    run: Run
    job: Job
    chunks: tuple[Chunk, ...]
    sink: LogSink
    lock: threading.Lock = field(default_factory=threading.Lock)
    started: float | None = None
    pieces: list[stepping.Piece] = field(default_factory=list)
    ended: bool = False

    def piece_path(self, chunk: Chunk) -> Path:
        """Where one chunk's piece is written, beside the section's video and hidden from a listing of it."""
        out = self.job.out
        return out.with_name(f".{out.stem}.piece{chunk.first}{out.suffix}")

    def begin(self) -> None:
        """Open the section on the stream and delete what a stopped run left, once, when its first chunk starts."""
        with self.lock:
            if self.started is not None:
                return
            self.started = time.monotonic()
            self.run.emit(SectionStart, stage=Stage.RECORD, section=self.job.section.number)
            for stale in self.job.out.parent.glob(f".{self.job.out.stem}.piece*"):
                stale.unlink(missing_ok=True)

    def add(self, done: stepping.Piece) -> bool:
        """Keep one chunk's piece, and say whether it was the last the section was waiting for.

        A piece finished after another chunk of its section failed is deleted, because nothing joins it.
        """
        with self.lock:
            if self.ended:
                done.path.unlink(missing_ok=True)
                return False
            self.pieces.append(done)
            return len(self.pieces) == len(self.chunks)

    def end(self, outcome: Outcome) -> None:
        """Close the section on the stream once, with how it ended, and delete any piece it did not join."""
        with self.lock:
            if self.started is None or self.ended:
                return
            self.ended = True
            elapsed = time.monotonic() - self.started
            self.run.emit(
                SectionDone,
                stage=Stage.RECORD,
                section=self.job.section.number,
                outcome=outcome,
                elapsed_seconds=elapsed,
            )
            for left in self.pieces:
                left.path.unlink(missing_ok=True)

    def finish(self, draw: Callable[[Path], Recording]) -> SectionRecording:
        """Draw the section's video into a draft, put it in place, leave its log beside it judged, and close it.

        `draw` writes the whole video into the draft it is handed. Only then does the old log go and
        the draft move onto the video, so a draw that fails leaves the last recording and its log.
        """
        inputs, run, job = self.inputs, self.run, self.job
        with drafted(job.out) as draft:
            recording = draw(draft)
            self.sink.clear()
        self.sink.write(recording)
        for name in recording.page_errors:
            run.note(f"Section {job.section.number} threw while it was recorded: {name}", level=Level.ERROR)
        for name in recording.missing:
            run.note(
                f"Section {job.section.number} asked for {name}, which the project does not have.", level=Level.WARNING
            )
        checks = check_recording(job.out, recording)
        section = job.section
        page = job.url if isinstance(section, RenderSection) else inputs.relative(inputs.path(section.page)).as_posix()
        found = recording_findings(
            recording,
            checks,
            page=page,
            where=inputs.relative(job.out),
            section=job.section.number,
            settings=inputs.settings,
        )
        for finding in found:
            run.found(finding)
        self.sink.log(recording, checks=checks, findings=found).write(job.log_path)
        run.wrote(job.out)
        run.wrote(job.log_path)
        self.end(Outcome.RAN)
        return row(inputs, job, checks.duration_seconds, kept=False)

    def record(
        self, opened: Pictures, chunk: Chunk, check: Callable[[], None], *, threads: int
    ) -> SectionRecording | None:
        """Record one chunk, and when it is the section's last, join, measure and judge the section.

        Gives back the section's row from the recorder that finished it, and None from every other.
        `check` raises when the section should stop, which the recorder asks before every frame.
        `threads` is the encode's thread count for every chunk of the run.
        """
        with within(stage=Stage.RECORD, section=self.job.section.number):
            self.begin()
            try:
                source = opened.source(self.job.shot, threads=threads)
                made = source.piece(chunk.frames, self.piece_path(chunk), check)
                if not self.add(made):
                    return None
                return self.finish(lambda draft: source.join(self.pieces, draft))
            except BaseException as failure:
                self.end(Outcome.STOPPED if isinstance(failure, STOPS) else Outcome.FAILED)
                raise


def row(inputs: Inputs, job: Job, seconds: float, *, kept: bool) -> SectionRecording:
    """One section's row of the result: the file, how long it runs and whether this run made it."""
    return SectionRecording(
        section=job.section.number,
        key=job.section.key,
        file=inputs.relative(job.out) if job.out.exists() else None,
        seconds=round(seconds, SECOND_DIGITS),
        frames=round(seconds * inputs.settings.video.fps),
        kept=kept,
    )


def kept_row(inputs: Inputs, run: Run, job: Job) -> SectionRecording:
    """The row of a section whose recording still stands, with its own pair of lines on the stream.

    A kept section is work the run planned and did not repeat, so its `section.done` carries `kept`,
    as a take narrate finds on disk does, and a renderer counts it apart from a section that was
    really recorded.
    """
    with run.section(Stage.RECORD, job.section.number) as ending:
        ending.outcome = Outcome.KEPT
        previous = job.previous
        seconds = previous.checks.duration_seconds if previous is not None and previous.checks is not None else 0.0
        return row(inputs, job, seconds, kept=True)


def _decided(job: Job, *, force: bool, named: set[int]) -> bool:
    """Whether a section is recorded again, which is recorded with the one token that says why."""
    number = job.section.number
    why = "forced" if force else "named" if number in named else "unchanged" if job.unchanged else "changed"
    cache_decision(log, "recording", hit=why == "unchanged", why=why, key=job.section.key, section=number)
    return why != "unchanged"


def make_clips(inputs: Inputs, run: Run, *, force: bool) -> None:
    """Make every clip the project makes and has not got, or every one of them when the run is forced."""
    video = inputs.settings.video
    for clip in inputs.document.clips:
        if inputs.path(clip.file).is_file() and not force:
            continue
        made = render.make_clip(
            clip,
            root=inputs.root,
            fps=video.fps,
            width=video.width,
            height=video.height,
            timeout=inputs.settings.tools.timeout_seconds,
        )
        run.wrote(made)


def expected(job: Job, fps: int) -> tuple[int, int]:
    """A section's frames and the steps recording it is expected to take.

    The steps are what the section's last recording took, scaled to its frames now, because an edit
    rarely changes how much of a section holds still. A section never recorded is expected to step
    on every frame.
    """
    frames = stepping.frame_count(job.seconds, fps)
    before = job.previous.recording if job.previous is not None else None
    if before is None or not before.steps:
        return frames, frames
    then = stepping.frame_count(before.requested_seconds, before.fps)
    return frames, max(1, min(frames, round(before.steps * frames / then))) if then else frames


def underway(inputs: Inputs, run: Run, todo: Sequence[Job], workers: int) -> tuple[dict[int, Underway], list[Chunk]]:
    """Each section to record with its chunks, and every chunk in the order the recorders are handed them."""
    fps = inputs.settings.video.fps
    spans = [Span(job.section.number, *expected(job, fps), splits=splits(job.section)) for job in todo]
    chunks = chunked(spans, workers, inputs.settings.record)
    sections = {
        job.section.number: Underway(
            inputs,
            run,
            job,
            tuple(sorted((chunk for chunk in chunks if chunk.section == job.section.number), key=lambda c: c.first)),
            LogSink(inputs, job),
        )
        for job in todo
    }
    log.debug(
        "%d sections are recorded in %d chunks on %d recorders.",
        len(sections),
        len(chunks),
        workers,
        extra={"data": {"sections": len(sections), "chunks": len(chunks), "workers": workers}},
    )
    return sections, chunks


def record(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    force: bool = False,
) -> RecordResult:
    """Record every page section this run names, and judge each one as soon as it is finished.

    Every program this run would start is held to `[record] programs` first, before any of them
    starts. The clips the project makes come next. The sections that must be recorded are split into chunks,
    which go to a pool of recorders as `[record] concurrency` allows, longest first. The sections that are
    kept are reported by this
    thread, so every row is in section order.

    Each chunk's page is held to its own deadlines, and the run as a whole to `run_deadline`, which
    every recorder asks before every frame, so no page can hold the run open.
    """
    programs.admitted(inputs.settings.record, inputs.programs(only, force=force))
    make_clips(inputs, run, force=force)
    planned = plan(inputs, run, only)
    passed_over(inputs, run, only)
    named = set(only or ())
    todo = [job for job in planned if _decided(job, force=force, named=named)]
    recorder = inputs.settings.record
    workers = automatic(recorder.concurrency)
    threads = encode_threads(recorder.encode_threads, cpus=available_cpus(), workers=workers)
    sections, chunks = underway(inputs, run, todo, workers)
    allowed = run_deadline(chunks, workers, recorder)
    ends = time.monotonic() + allowed

    def opening(stack: ExitStack) -> Pictures:
        return stack.enter_context(pictures(inputs, run))

    def one(opened: Pictures, chunk: Chunk, halt: Halt) -> SectionRecording | None:
        def check() -> None:
            halt.check()
            if time.monotonic() >= ends:
                raise ToolError(
                    f"record ran past its deadline of {allowed:g} seconds while section {chunk.section} was recorded.",
                    hint=STOPPED_HINT,
                )

        return sections[chunk.section].record(opened, chunk, check, threads=threads)

    rows: list[SectionRecording] = []
    total = len(planned)
    try:
        with Pool(chunks, workers, opening, one, run.cancel) as pool:
            for done, job in enumerate(planned, start=1):
                number = job.section.number
                if number in sections:
                    made = [pool.result(chunk) for chunk in sections[number].chunks]
                    rows.append(next(made_row for made_row in made if made_row is not None))
                else:
                    rows.append(kept_row(inputs, run, job))
                label = f"section {number} of {inputs.document.name}"
                run.progress(Stage.RECORD, done=done, total=total, unit=Unit.SECTION, label=label, section=number)
    except BaseException:
        # A section a halt stopped between its chunks never reaches its last one, so it is closed here.
        for section in sections.values():
            section.end(Outcome.STOPPED)
        raise
    return run.result(RecordResult, sections=tuple(rows))


__all__ = [
    "LogSink",
    "TurnText",
    "Underway",
    "record",
    "stale_recording",
]
