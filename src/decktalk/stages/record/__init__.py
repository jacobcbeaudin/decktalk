"""Stage 3: record each page section with headless Chromium, find narration t=0, and check the result.

One command does the whole of it. It opens the page on the local origin with the section's resolved
cues and spoken words, records it for its span in the narration, reads the webm to find where
narration t=0 sits under the magenta cover, checks the frames and what the page reported, and writes
all of that to `build/recordings/NN.json` before it moves on to the next section. The measurement
therefore belongs to the recording beside it, and a long run can be read while it runs.

A section whose scene, the page around it, its loaded assets, words and cues have not moved is kept
rather than recorded again, because the run would produce the same pixels. The key is cut per scene,
so an edit to one slide records the sections that play that scene and leaves the rest of the page's
sections alone. A section named by `--only` is always recorded, which is how an author asks for a
take again without editing anything.

    capture.py   the page URL, and driving Chromium with its retries
    start.py     where narration t=0 sits in one recording
    checks.py    duration, luma, KaTeX and page-error verdicts
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...artifacts import RecordingLog
from ...errors import ConfigError, MissingInputError
from ...jsonio import relative
from ...media.browser import chromium
from ...model import PageSection, Project
from ...verdicts import Finding, Findings, Verdict
from .capture import Job, capture_section, plan_job, prev_words_query, scene_params, scene_url, words_query
from .checks import check_recording, label
from .start import find_start

log = logging.getLogger(__name__)

__all__ = [
    "Job",
    "RecordResult",
    "SectionRecording",
    "capture_section",
    "stale_recording",
    "plan_job",
    "prev_words_query",
    "record",
    "scene_params",
    "scene_url",
    "words_query",
]


@dataclass
class SectionRecording:
    """One section's recording: the file, the log it was written with, and what the log judged."""

    section: PageSection
    path: Path
    log: RecordingLog
    kept: bool = False  # The recording was already made from these inputs, so this run left it alone.

    @property
    def key(self) -> str:
        return self.section.key

    @property
    def verdicts(self) -> tuple[Verdict, ...]:
        return self.log.checks.verdicts if self.log.checks else ()

    @property
    def ok(self) -> bool:
        return not self.verdicts

    @property
    def label(self) -> str:
        """Every verdict as one line, with the stall length beside STALLED, as the table prints it."""
        return label(self.log.checks, self.log.worst_stall_ms)

    @property
    def detail(self) -> str | None:
        """The one sentence a judged row carries, with the measured number a reader needs in it."""
        if not self.verdicts:
            return None
        checks = self.log.checks
        measured = "" if checks is None else f" It ran {checks.duration_seconds:.1f}s of {checks.wanted_seconds:.1f}s."
        stall = f" The worst frame gap was {self.log.worst_stall_ms}ms." if self.log.worst_stall_ms else ""
        outside = f" It loaded from {', '.join(self.log.external)}." if self.log.external else ""
        return f"Section {self.key} recorded {self.label}.{measured}{stall}{outside}"

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The row as JSON-ready data: what was recorded, where t=0 landed, and every verdict."""
        checks = self.log.checks
        return {
            "key": self.key,
            "file": relative(self.path, root),
            "kept": self.kept,
            "seconds": round(self.log.requested_seconds, 3),
            "t0_seconds": self.log.t0_seconds,
            "t0_method": self.log.t0_method,
            "t0_guessed": self.log.t0_guessed,
            "duration": None if checks is None else round(checks.duration_seconds, 3),
            "wanted": None if checks is None else round(checks.wanted_seconds, 3),
            "luma": None if checks is None else {k: round(v, 2) for k, v in vars(checks.luma).items()},
            "verdicts": [v.to_dict() for v in self.verdicts],
            "detail": self.detail,
            "stall_ms": self.log.worst_stall_ms or None,
            "page_errors": list(self.log.page_errors),
            "assets": list(self.log.assets),
            "external": list(self.log.external),
        }


@dataclass
class RecordResult:
    """Every section one `record` run touched, in the order it touched them."""

    sections: list[SectionRecording] = field(default_factory=list)
    # A section `--only` left out whose recording no longer matches the project. The run did not
    # touch it, and the film it goes into would show the old picture, so it is reported rather than
    # left for the author to notice by eye.
    rows: list[Finding] = field(default_factory=list)

    @property
    def kept_sections(self) -> list[SectionRecording]:
        """The sections this run left alone because nothing they are recorded from had moved."""
        return [row for row in self.sections if row.kept]

    @property
    def page_errors(self) -> list[SectionRecording]:
        """The rows whose page threw or exposed no runtime catalog, which `build` stops on."""
        return [row for row in self.sections if row.log.page_errors]

    @property
    def findings(self) -> Findings:
        """Every verdict of every recording, and every section this run left behind out of date."""
        return Findings.of(v for row in self.sections for v in row.verdicts) + Findings.of(r.verdict for r in self.rows)

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "recordings": [row.to_dict(root) for row in self.sections],
            "stale": [row.to_dict() for row in self.rows],
        }


def jobs(project: Project, only: list[int] | None, seconds: float | None, *, use_cues: bool) -> list[Job]:
    """One job per page section that has a length to record, in section order."""
    takes = project.takes()
    if takes is None and seconds is None:
        raise MissingInputError(
            f"{relative(project.takes_path, project.root)} is not there, so no section has a length to record.",
            hint="Run `decktalk narrate` first, or pass --seconds.",
            path=project.takes_path,
        )
    cue_times = project.cue_times() if use_cues else None
    wanted = set(only) if only else None
    planned: list[Job] = []
    for section in project.page_sections:
        if wanted is not None and section.number not in wanted:
            continue
        span = takes.span(section.key) if takes else None
        length = seconds if seconds is not None else (span + section.record_margin_seconds if span else None)
        if not length:
            log.warning("section %s: there is no narration span yet, so it is skipped", section.key)
            continue
        planned.append(plan_job(project, section, cue_times, length))
    if not planned:
        raise ConfigError("nothing to record: no page sections matched")
    return planned


def stale_recording(project: Project, section: PageSection) -> str | None:
    """Why the recording on disk for this section no longer matches the project, or None when it does.

    This is the one rule that decides whether a recording still stands, so what `record` skips and
    what any other reader calls stale are the same question answered once, and neither compares file
    times.
    """
    takes = project.takes()
    span = takes.span(section.key) if takes else None
    if not span:
        return f"section {section.key} has no narration span yet"
    cue_times = project.cue_times() if project.cue_times_path.exists() else None
    job = plan_job(project, section, cue_times, span + section.record_margin_seconds)
    if job.unchanged:
        return None
    if not job.out.exists():
        return f"section {section.key} has no recording"
    if job.previous is None or not job.previous.input_hash:
        return f"section {section.key} was recorded before this project could tell what it was recorded from"
    return (
        f"section {section.key}: its scene, the page around it, its assets, its words or its cues "
        "changed since it was recorded"
    )


def record(
    project: Project,
    *,
    only: list[int] | None = None,
    seconds: float | None = None,
    use_cues: bool = True,
    opening: Callable[[PageSection], None] | None = None,
    report: Callable[[SectionRecording], None] | None = None,
) -> RecordResult:
    """Record every page section, measure narration t=0 on each webm, and check what came out.

    Each section's log is written as soon as that section is finished, so the measurement can never
    belong to another take and an agent can read the run as it goes. `opening(section)` is called
    before every section, whether it is recorded or kept, and `report(row)` the moment that section
    is finished, which together are how `build` reports a stage that takes minutes per section.
    """
    cfg = project.settings.record
    named = set(only or ())
    result = RecordResult()
    planned = jobs(project, only, seconds, use_cues=use_cues)
    # A section named by --only is recorded whatever its inputs say, because that is how an author
    # asks for another take of a page that has not changed.
    fresh = [job for job in planned if not job.unchanged or job.section.number in named]
    # A run that names sections says nothing about the others, so each one it passed over is asked
    # whether its recording still stands. A build assembles every section, not only the named ones.
    for section in project.page_sections if named else []:
        if section.number in named:
            continue
        why = stale_recording(project, section)
        if why is not None:
            result.rows.append(
                Finding(
                    verdict=Verdict.MISSING if "no recording" in why else Verdict.INCONSISTENT,
                    section=section.number,
                    where=relative(project.recording(section), project.root),
                    detail=f"{why}, and --only did not name it, so the film keeps what was recorded before.",
                )
            )
    for job in planned:
        if job not in fresh:
            assert job.previous is not None
            if opening:
                opening(job.section)
            log.info(
                "[rec ] section %s  kept: its scene, the page around it, its assets, words and cues are unchanged",
                job.section.key,
            )
            kept = SectionRecording(job.section, job.out, job.previous, kept=True)
            result.sections.append(kept)
            if report:
                report(kept)
    if not fresh:
        return result
    with chromium(cfg.browser_path) as browser:
        for job in fresh:
            section = job.section
            if opening:
                opening(section)
            log.info(
                "[rec ] section %s (%s?scene=%s)  %.1fs ...", section.key, section.page, section.scene, job.seconds
            )
            recording_log = capture_section(project, browser, job)
            start = find_start(job.out, recording_log.settle_seconds, cfg)
            recording_log.t0_seconds, recording_log.t0_method = start.seconds, start.method
            recording_log.t0_guessed = start.guessed
            recording_log.checks = check_recording(job.out, recording_log, cfg)
            recording_log.save(job.log_path)
            row = SectionRecording(section=section, path=job.out, log=recording_log)
            result.sections.append(row)
            if report:
                report(row)
            if start.guessed:
                log.warning("       no magenta cover was found, so narration t=0 is a guess (%s)", start.method)
            log.info("       %s  (t=0 at %.3fs, %s)", relative(job.out, project.root), start.seconds, row.label)
            for message in recording_log.page_errors:
                log.warning("       page error: %s", message)
    result.sections.sort(key=lambda row: row.key)
    return result
