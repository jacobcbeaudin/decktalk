"""Stage 3: record each page section in a headless browser, find narration t=0, and judge the result.

One command does the whole of it. It opens the page on the local origin with the section's resolved
cues and spoken words, records it for its span in the narration, reads the webm to find where
narration t=0 sits under the cover, judges the frames and what the page reported, and writes all of
that to `build/recordings/NN.json` before it moves on to the next section. The measurement therefore
belongs to the recording beside it, and a long run can be read while it runs.

    capture.py   the page URL, what a recording is keyed on, and the page cut into its scenes
    start.py     where narration t=0 sits in one recording
    checks.py    the frames, the page's own codes and the origins it reached for

A section whose scene, the page around it, its loaded assets, its words, its cues and the motion it
renders with have not moved is kept rather than recorded again, because the run would produce the
same pixels. The key is cut per scene, so an edit to one slide records the sections that play that
scene and leaves the rest of the page's sections alone. A section named by `--section` is always
recorded, which is how an author asks for another take of a page that has not changed.

The order the recorder writes in is the whole of its safety. The log of the recording being replaced
goes before anything is captured, the webm is placed next, and the log of what was just recorded is
written last, so the pair on disk is complete or absent and a crash can never leave a new picture
under an old narration t=0.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path

from playwright.sync_api import Browser

from decktalk.artifacts import RecordingChecks, RecordingLog, Takes
from decktalk.errors import InputError
from decktalk.events import Level, SectionDone, SectionStart, Unit
from decktalk.findings import Code, Finding, Location
from decktalk.inputs import Inputs, PageSection
from decktalk.machine import Run
from decktalk.media import browser
from decktalk.media.browser import Recording
from decktalk.media.origin import Allowed
from decktalk.page import CAPTURE_FPS
from decktalk.pipeline import Artifact, Outcome, Stage
from decktalk.results import RecordResult, SectionRecording
from decktalk.stages import clock, judge, selects, since
from decktalk.stages.record.capture import (
    Job,
    plan_job,
    scene_params,
    scene_url,
    section_hash,
    served_paths,
    words_query,
)
from decktalk.stages.record.checks import check_recording, recording_findings
from decktalk.stages.record.start import Start, find_start

SECOND_DIGITS = 3
"""Truth: three decimal places of a second is one millisecond, which is finer than any frame."""


class LogSink:
    """Where one section's recording log is kept, cleared before the capture and written after it.

    The media layer clears this before it captures anything and writes it once the webm is in place,
    which is what keeps the pair on disk complete or absent. What it writes here is everything the
    recorder knows, and the stage writes the log again with narration t=0, the frame measurements and
    the judgements as soon as it has measured them, so a run stopped in between leaves a log with no
    t=0 in it, which the next run reads as a section it has not finished recording.
    """

    def __init__(self, inputs: Inputs, section: PageSection, url: str, seconds: float, path: Path) -> None:
        self.inputs = inputs
        self.section = section
        self.url = url
        self.seconds = seconds
        self.path = path
        self.recording: Recording | None = None

    def clear(self) -> None:
        """Delete the log of the recording that is about to be replaced, before anything is captured."""
        self.path.unlink(missing_ok=True)

    def write(self, recording: Recording) -> None:
        """Write what the recorder knows about the webm now on disk, before anything measures it."""
        self.recording = recording
        self.log(recording).write(self.path)

    def log(
        self,
        recording: Recording,
        *,
        start: Start | None = None,
        checks: RecordingChecks | None = None,
        findings: tuple[Finding, ...] = (),
    ) -> RecordingLog:
        """The log of one recording, with whatever the stage has measured of it so far.

        The digest is taken again over what the page really loaded, because the page may have asked
        for a file the last run never saw and a key that did not count it would call the section
        unchanged on the next run.
        """
        return RecordingLog(
            section=self.section.number,
            url=recording.url,
            input_hash=section_hash(self.inputs, self.section, self.url, self.seconds, list(recording.assets)),
            requested_seconds=recording.requested_seconds,
            settle_seconds=recording.settle_seconds,
            load_seconds=recording.load_seconds,
            clock_start_seconds=recording.clock_start_seconds,
            t0_seconds=None if start is None else start.seconds,
            t0_method=None if start is None else start.method,
            t0_guessed=start is not None and start.guessed,
            assets=tuple(Path(name) for name in recording.assets),
            external=recording.external,
            findings=findings,
            checks=checks,
            report=recording.report,
        )


def stale_recording(inputs: Inputs, section: PageSection) -> str | None:
    """Why the recording on disk for this section no longer matches the project, or None when it does.

    This is the one rule that decides whether a recording still stands, so what `record` skips and
    what `status` calls stale are the same question answered once, and neither compares file times.
    """
    takes = inputs.takes()
    take = takes.of(section.number) if takes is not None else None
    if take is None or not take.span_seconds:
        return f"section {section.number} has no narration span yet"
    cue_times = inputs.cue_times()
    job = plan_job(inputs, section, cue_times, take.span_seconds + section.record_margin_seconds)
    if job.unchanged:
        return None
    if not job.out.exists():
        return f"section {section.number} has no recording"
    if job.previous is None or not job.previous.input_hash:
        return f"section {section.number} was recorded before this project could tell what it was recorded from"
    return (
        f"section {section.number}: its scene, the page around it, its assets, its words, its cues or "
        "the motion it renders with changed since it was recorded"
    )


def plan(inputs: Inputs, run: Run, only: Sequence[int] | None) -> list[Job]:
    """One job per page section this run considers, in section order.

    A section with no span in the take index has no length to record, so it is named in one sentence
    and left out rather than recorded for a length nobody stated.
    """
    takes = Takes.require(inputs.workspace.takes_path, Artifact.TAKES)
    cue_times = inputs.cue_times()
    wanted = selects(only)
    planned: list[Job] = []
    for section in inputs.document.page_sections:
        if not wanted(section.number):
            continue
        take = takes.of(section.number)
        if take is None or not take.span_seconds:
            run.note(f"Section {section.number} has no narration span yet, so it is not recorded.", level=Level.WARNING)
            continue
        planned.append(plan_job(inputs, section, cue_times, take.span_seconds + section.record_margin_seconds))
    if not planned:
        raise InputError(
            "no page section has a length to record.",
            hint="Run `decktalk narrate` first, or name a section that plays a page.",
        )
    return planned


def passed_over(inputs: Inputs, run: Run, only: Sequence[int] | None) -> None:
    """Report every section this run did not name whose recording no longer matches the project.

    A run that names sections says nothing about the others, and the film is assembled from all of
    them, so a section left behind out of date is reported rather than left for a reader to notice
    by eye.
    """
    if not only:
        return
    named = set(only)
    for section in inputs.document.page_sections:
        if section.number in named:
            continue
        why = stale_recording(inputs, section)
        if why is None:
            continue
        where = inputs.relative(inputs.workspace.recording(section.key))
        if not inputs.workspace.recording(section.key).exists():
            run.found(
                judge(
                    Code.FILE_MISSING,
                    f"section {section.number} has no recording at {where.as_posix()}, and this run did not "
                    "name it, so the film would be cut from a picture that is not there.",
                    Location(where=where.as_posix(), file=where, section=section.number),
                    stage=Stage.RECORD,
                )
            )
            continue
        run.note(
            f"{why}, and this run did not name it, so the film keeps the picture recorded before.",
            level=Level.WARNING,
        )


def capture(inputs: Inputs, run: Run, opened: Browser, job: Job, sink: LogSink) -> Recording:
    """Record one section, retrying while its frames stall, and give back the recording that stuck.

    A stalled page froze a reveal for a few frames, which no cut can repair, so the section is
    recorded again while the machine is quieter.
    """
    settings = inputs.settings
    # The local name is not `record`, because that is this module's own stage function.
    recorder, video = settings.record, settings.video
    allowed = Allowed.of(inputs.root, served_paths(inputs))
    documents = inputs.documents()
    recording: Recording | None = None
    for attempt in range(1, recorder.retries + 2):
        recording = browser.record_page(
            opened,
            job.url,
            job.seconds,
            job.out,
            allowed=allowed,
            log_sink=sink,
            settle_seconds=recorder.settle_seconds,
            min_cover_seconds=recorder.min_cover_seconds,
            width=video.width,
            height=video.height,
            color_scheme=recorder.color_scheme,
            motion=settings.motion,
            documents=documents,
        )
        gap = recording.report.worst_gap_ms
        if gap <= recorder.frame_gap_max_ms or attempt > recorder.retries:
            break
        run.note(
            f"Section {job.section.number} stalled for {gap} ms, which is over the "
            f"{recorder.frame_gap_max_ms} ms limit, so it is recorded again ({attempt} of {recorder.retries}).",
            level=Level.WARNING,
        )
    if recording is None:  # pragma: no cover  (the loop runs at least once)
        raise InputError(f"section {job.section.number} was not recorded.")
    for name in recording.page_errors:
        run.note(f"Section {job.section.number} threw while it was recorded: {name}", level=Level.ERROR)
    return recording


def recorded(inputs: Inputs, run: Run, opened: Browser, job: Job) -> SectionRecording:
    """Record one section, measure it, and leave its log beside the webm with every judgement in it."""
    sink = LogSink(inputs, job.section, job.url, job.seconds, job.log_path)
    recording = capture(inputs, run, opened, job, sink)
    start = find_start(job.out, recording.settle_seconds, inputs.settings.record)
    checks = check_recording(job.out, recording)
    page = inputs.relative(inputs.path(job.section.page)).as_posix()
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
    if start.guessed:
        run.note(
            f"Section {job.section.number} shows no cover, so narration t=0 is a guess "
            f"at {start.seconds:g}s ({start.method}), and every reveal in the section moves with it.",
            level=Level.WARNING,
        )
    log = sink.log(recording, start=start, checks=checks, findings=found)
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
        frames=round(seconds * CAPTURE_FPS),
        kept=kept,
    )


def kept_row(inputs: Inputs, run: Run, job: Job) -> SectionRecording:
    """The row of a section this run left alone, with its own pair of lines on the stream.

    A kept section is work the run decided not to do, so its `section.done` carries `skipped` rather
    than `ok` and a renderer counts it apart from a section that was really recorded.
    """
    started = clock()
    run.check()
    number = job.section.number
    run.emit(SectionStart, stage=Stage.RECORD, section=number)
    previous = job.previous
    seconds = previous.checks.duration_seconds if previous is not None and previous.checks is not None else 0.0
    run.emit(SectionDone, stage=Stage.RECORD, section=number, outcome=Outcome.SKIPPED, seconds=since(started))
    return row(inputs, job, seconds, kept=True)


def record(
    inputs: Inputs,
    run: Run,
    *,
    only: Sequence[int] | None = None,
    force: bool = False,
) -> RecordResult:
    """Record every page section this run names, and judge each one as soon as it is finished."""
    started = clock()
    planned = plan(inputs, run, only)
    passed_over(inputs, run, only)
    named = set(only or ())
    rows: list[SectionRecording] = []
    total = len(planned)
    with ExitStack() as stack:
        opened: Browser | None = None
        for done, job in enumerate(planned, start=1):
            again = force or job.section.number in named
            if job.unchanged and not again:
                rows.append(kept_row(inputs, run, job))
            else:
                if opened is None:
                    opened = stack.enter_context(browser.chromium(inputs.settings.record.browser_path))
                with run.section(Stage.RECORD, job.section.number):
                    rows.append(recorded(inputs, run, opened, job))
            label = f"section {job.section.number} of {inputs.document.name}"
            run.progress(
                Stage.RECORD, done=done, total=total, unit=Unit.SECTION, label=label, section=job.section.number
            )
    return run.result(RecordResult, sections=tuple(rows), seconds=since(started))


__all__ = [
    "Job",
    "LogSink",
    "record",
    "scene_params",
    "scene_url",
    "stale_recording",
    "words_query",
]
