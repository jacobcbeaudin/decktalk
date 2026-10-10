"""Stage 3: record each page section in a headless browser, one frame at a time, and judge the result.

One command does the whole of it. It opens the page on the local origin with the section's resolved
cues and spoken words, steps its section clock through its span in the narration one frame at a
time, judges the frames and what the page reported, and writes all of that to
`build/recordings/NN.json` before it moves on to the next section. Frame zero of every recording is
second zero of its section, so there is no start to find. The measurement belongs to the recording
beside it, and a long run can be read while it runs.

    capture.py   the page URL, what a recording is made from, and the page cut into its scenes
    render.py    a section whose picture a command draws, which is the second source of a picture
    checks.py    the frames, the page's own codes and the origins it reached for
    pool.py      how many sections record at once, on the pool in `decktalk.stages.pool`

Sections are recorded several at a time, each worker with a Chromium of its own, because each
recording is one browser and one encode and the sections of a film share nothing but the project.
The rows, the progress lines and the result come back in section order whatever order the workers
finish in.

A section none of whose parts has moved is kept rather than recorded again, because the run would
produce the same pixels. The parts are its turns' text, voice and word times, the markup of its own
scene, the part of the page every scene shares, its loaded assets, its cues, its length, the frame,
the colour scheme, the page policy, the motion it renders with and the engine. They are cut per scene
and per turn, so an edit to one slide or one turn records the sections it touches, leaves the rest of
the page's sections alone, and is named by the parts that moved.
A section named by `--section` is always recorded, which is how an author asks for another take of a
page that has not changed.

The order the recorder writes in is the whole of its safety. The log of the recording being replaced
goes before anything is captured, the video is placed next, and the log of what was just recorded is
written last, so the pair on disk is complete or absent and a crash can never leave a new picture
under an old judgement.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass

from decktalk.artifacts import RecordingChecks, RecordingLog, UnreadableCache, UnreadablePaidRecord
from decktalk.errors import InputError
from decktalk.events import Level, Unit
from decktalk.findings import Finding
from decktalk.inputs import Inputs, PageSection
from decktalk.logs import cache_decision
from decktalk.machine.run import Run
from decktalk.media import browser, stepping
from decktalk.media.origin import Allowed
from decktalk.media.pagereport import PageReport, Recording
from decktalk.page import SECOND_DIGITS
from decktalk.pipeline import Artifact, Outcome, Stage
from decktalk.results import RecordResult, SectionRecording
from decktalk.stages import selects
from decktalk.stages.pool import Halt, Pool
from decktalk.stages.record import render
from decktalk.stages.record.capture import (
    Job,
    TurnText,
    plan_job,
    said,
)
from decktalk.stages.record.checks import check_recording, recording_findings
from decktalk.stages.record.pool import automatic

log = logging.getLogger(__name__)


@dataclass(frozen=True, eq=False)
class LogSink:
    """Where one section's recording log is kept, cleared before the capture and written after it.

    The media layer clears this before it captures anything and writes it once the video is in place,
    which is what keeps the pair on disk complete or absent. What it writes here is everything the
    recorder knows, and the stage writes the log again with the frame measurements and the judgements
    as soon as it has measured them, so a run stopped in between leaves a log with no checks in it,
    which the next run reads as a section it has not finished recording.
    """

    inputs: Inputs
    job: Job

    def clear(self) -> None:
        """Delete the log of the recording that is about to be replaced, before anything is captured."""
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


def stale_recording(inputs: Inputs, section: PageSection, script: Sequence[TurnText] | None = None) -> str | None:
    """Why the recording on disk for this section no longer matches the project, or None when it does.

    This is the one rule that decides whether a recording still stands, so what `record` skips and
    what `status` calls stale are the same question answered once, and neither compares file times.
    The reason names the parts that moved, such as the words of one turn or the markup of the
    section's own scene, and never a list of everything that could have. `script` gives the turns'
    text and voice as the script has them, which `status` passes so a turn edited since its take was
    made is named before narrate has run. Without it the turns are their takes, which is what a
    `record` run plays.
    """
    takes = inputs.takes()
    span = takes.span_seconds(section.number) if takes is not None else 0.0
    if not span:
        return f"section {section.number} has no narration span yet"
    cue_times = inputs.cue_times()
    job = plan_job(inputs, section, cue_times, span + section.record_margin_seconds, script)
    if job.unchanged:
        return None
    if not job.out.exists():
        return f"section {section.number} has no recording"
    if job.previous is None:
        return f"section {section.number} has no log of what it was recorded from"
    if job.previous.checks is None:
        return f"section {section.number} was stopped before its recording was measured"
    return f"section {section.number}: {said(job.moved)} changed since it was recorded"


def plan(inputs: Inputs, run: Run, only: Sequence[int] | None) -> list[Job]:
    """One job per page section this run considers, in section order.

    A selection no page section matches is refused with the page sections named, as narrate names
    the spoken ones. A section with no span in the take index has no length to record, so it is named
    in one sentence and left out rather than recorded for a length nobody stated.
    """
    takes = inputs.takes(required=True)
    cue_times = inputs.cue_times()
    wanted = selects(only)
    chosen = [section for section in inputs.document.page_sections if wanted(section.number)]
    if only is not None and not chosen:
        every = [section.number for section in inputs.document.page_sections]
        raise InputError(
            f"no page section matches {list(only)}, so there is nothing to record.",
            hint=f"The page sections are {every}.",
        )
    planned: list[Job] = []
    for section in chosen:
        span = takes.span_seconds(section.number)
        if not span:
            run.note(f"Section {section.number} has no narration span yet, so it is not recorded.", level=Level.WARNING)
            continue
        planned.append(plan_job(inputs, section, cue_times, span + section.record_margin_seconds))
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
    for section in inputs.document.page_sections:
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


def capture(
    inputs: Inputs, run: Run, opened: browser.Chromium, job: Job, sink: LogSink, check: Callable[[], None]
) -> Recording:
    """Record one section by stepping its clock through its span, and give back what the page reported."""
    settings = inputs.settings
    # The variable is not called `record`, because that is this module's own stage function.
    recorder, video = settings.record, settings.video
    if job.section.rendered:
        return rendered_section(inputs, job, sink)
    allowed = Allowed.of(inputs.root, inputs.served_paths())
    documents = inputs.documents()
    taken = stepping.record_page(
        opened,
        job.url,
        job.seconds,
        job.out,
        allowed=allowed,
        log_sink=sink,
        width=video.width,
        height=video.height,
        fps=video.fps,
        frame_wait_seconds=recorder.frame_wait_seconds,
        skip_still=recorder.skip_still,
        skip_audit_every=recorder.skip_audit_every,
        color_scheme=recorder.color_scheme,
        motion=settings.motion,
        documents=documents,
        check=check,
    )
    for name in taken.page_errors:
        run.note(f"Section {job.section.number} threw while it was recorded: {name}", level=Level.ERROR)
    for name in taken.missing:
        run.note(
            f"Section {job.section.number} asked for {name}, which the project does not have.", level=Level.WARNING
        )
    return taken


def rendered_section(inputs: Inputs, job: Job, sink: LogSink) -> Recording:
    """Draw one rendered section by running its command, and keep the log in the order a page's recording is kept.

    The command is the second source of a section's picture, so what it hands back is the same
    recording a page's is: the video at the section's own path, and a report carrying the lanes it
    said it drew for verify to mask and measure.
    """
    render.admitted(inputs.settings.record.page_policy, number=job.section.number)
    sink.clear()
    done = render.run_command(
        job.section.render_command,
        root=inputs.root,
        work=inputs.workspace.render_dir(job.section.key),
        out=job.out,
        document=job.document or {},
        timeout=inputs.settings.tools.timeout_seconds,
        label=f"section {job.section.number}",
    )
    taken = Recording(
        url=job.url,
        assets=(),
        external=(),
        requested_seconds=round(job.seconds, SECOND_DIGITS),
        fps=inputs.settings.video.fps,
        load_seconds=done.seconds,
        page_errors=(),
        report=PageReport(motions=done.motions),
    )
    sink.write(taken)
    return taken


def recorded(
    inputs: Inputs, run: Run, opened: browser.Chromium, job: Job, check: Callable[[], None]
) -> SectionRecording:
    """Record one section, measure it, and leave its log beside the webm with every judgement in it.

    `check` raises when the section should stop, which the recorder asks before every frame.
    """
    sink = LogSink(inputs, job)
    recording = capture(inputs, run, opened, job, sink, check)
    checks = check_recording(job.out, recording)
    page = job.url if job.section.rendered else inputs.relative(inputs.path(job.section.page)).as_posix()
    where = inputs.relative(job.out)
    found = recording_findings(
        recording,
        checks,
        page=page,
        where=where,
        section=job.section.number,
        settings=inputs.settings,
    )
    for finding in found:
        run.found(finding)
    log = sink.log(recording, checks=checks, findings=found)
    log.write(job.log_path)
    run.wrote(job.out)
    run.wrote(job.log_path)
    return row(inputs, job, checks.duration_seconds, kept=False)


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


def record(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    force: bool = False,
) -> RecordResult:
    """Record every page section this run names, and judge each one as soon as it is finished.

    The sections that must be recorded go to a pool of workers as `[record] concurrency` allows, and
    the sections that are kept are reported by this thread, so every row is in section order.
    """
    planned = plan(inputs, run, only)
    passed_over(inputs, run, only)
    named = set(only or ())
    todo = [job for job in planned if _decided(job, force=force, named=named)]
    by_number = {job.section.number: job for job in todo}
    recorder = inputs.settings.record

    def opening(stack: ExitStack) -> browser.Chromium:
        return stack.enter_context(
            browser.chromium(
                inputs.settings.tools.chromium,
                policy=recorder.page_policy,
                spend=run.spend,
                switches=recorder.capture_switches,
            )
        )

    def one(opened: browser.Chromium, number: int, halt: Halt) -> SectionRecording:
        with run.section(Stage.RECORD, number):
            return recorded(inputs, run, opened, by_number[number], halt.check)

    rows: list[SectionRecording] = []
    total = len(planned)
    workers = automatic(recorder.concurrency, len(todo))
    with Pool(list(by_number), workers, opening, one, run.cancel) as pool:
        for done, job in enumerate(planned, start=1):
            number = job.section.number
            rows.append(pool.result(number) if number in by_number else kept_row(inputs, run, job))
            label = f"section {number} of {inputs.document.name}"
            run.progress(Stage.RECORD, done=done, total=total, unit=Unit.SECTION, label=label, section=number)
    return run.result(RecordResult, sections=tuple(rows))


__all__ = [
    "LogSink",
    "TurnText",
    "record",
    "stale_recording",
]
