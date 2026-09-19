"""Stage 3: record each page section with headless Chromium, find narration t=0, and check the result.

One command does the whole of it. It opens the page on the local origin with the section's resolved
cues and spoken words, records it for its span in the narration, reads the webm to find where
narration t=0 sits under the magenta cover, checks the frames and what the page reported, and writes
all of that to `build/recordings/NN.json` before it moves on to the next section. The measurement
therefore belongs to the recording beside it, and a long run can be read while it runs.

    capture.py   the page URL, and driving Chromium with its retries
    start.py     where narration t=0 sits in one recording
    checks.py    duration, luma, KaTeX and page-error verdicts
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...artifacts import RecordingLog
from ...errors import ConfigError, MissingInputError
from ...jsonio import relative
from ...media.browser import chromium
from ...model import PageSection, Project
from ...verdicts import Findings, Verdict
from .capture import Job, capture_section, plan_job, prev_words_query, scene_params, scene_url, words_query
from .checks import check_recording, label
from .start import find_start

log = logging.getLogger(__name__)

__all__ = [
    "Job",
    "RecordResult",
    "SectionRecording",
    "capture_section",
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

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The row as JSON-ready data: what was recorded, where t=0 landed, and every verdict."""
        checks = self.log.checks
        return {
            "key": self.key,
            "file": relative(self.path, root),
            "seconds": round(self.log.requested_seconds, 3),
            "t0_seconds": self.log.t0_seconds,
            "t0_method": self.log.t0_method,
            "t0_guessed": self.log.t0_guessed,
            "duration": None if checks is None else round(checks.duration_seconds, 3),
            "wanted": None if checks is None else round(checks.wanted_seconds, 3),
            "luma": None if checks is None else {k: round(v, 2) for k, v in vars(checks.luma).items()},
            "verdicts": [v.name for v in self.verdicts],
            "stall_ms": self.log.worst_stall_ms or None,
            "page_errors": list(self.log.page_errors),
            "assets": list(self.log.assets),
        }


@dataclass
class RecordResult:
    """Every section one `record` run touched, in the order it touched them."""

    sections: list[SectionRecording] = field(default_factory=list)

    @property
    def page_errors(self) -> list[SectionRecording]:
        """The rows whose page threw or exposed no runtime catalog, which `build` stops on."""
        return [row for row in self.sections if row.log.page_errors]

    @property
    def findings(self) -> Findings:
        """Every verdict of every recording, tallied."""
        return Findings.of(v for row in self.sections for v in row.verdicts)

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {"recordings": [row.to_dict(root) for row in self.sections]}


def jobs(project: Project, only: list[int] | None, seconds: float | None, *, use_cues: bool) -> list[Job]:
    """One job per page section that has a length to record, in section order."""
    timeline = project.timeline()
    if timeline is None and seconds is None:
        raise MissingInputError(f"{project.timeline_path} not found; run `decktalk narrate` first, or pass seconds")
    cue_times = project.cue_times() if use_cues else None
    wanted = set(only) if only else None
    planned: list[Job] = []
    for section in project.page_sections:
        if wanted is not None and section.number not in wanted:
            continue
        span = timeline.span(section.key) if timeline else None
        length = seconds if seconds is not None else (span + section.record_margin_seconds if span else None)
        if not length:
            log.warning("section %s: no narration span yet; skipped", section.key)
            continue
        planned.append(plan_job(project, section, cue_times, length))
    if not planned:
        raise ConfigError("nothing to record: no page sections matched")
    return planned


def record(
    project: Project,
    *,
    only: list[int] | None = None,
    seconds: float | None = None,
    use_cues: bool = True,
) -> RecordResult:
    """Record every page section, measure narration t=0 on each webm, and check what came out.

    Each section's log is written as soon as that section is finished, so the measurement can never
    belong to another take and an agent can read the run as it goes.
    """
    cfg = project.settings.record
    result = RecordResult()
    with chromium(cfg.browser_path) as browser:
        for job in jobs(project, only, seconds, use_cues=use_cues):
            section = job.section
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
            if start.guessed:
                log.warning("       no magenta cover found; narration t=0 is a guess (%s)", start.method)
            log.info("       %s  (t=0 at %.3fs, %s)", relative(job.out, project.root), start.seconds, row.label)
            for message in recording_log.page_errors:
                log.warning("       page error: %s", message)
    return result
