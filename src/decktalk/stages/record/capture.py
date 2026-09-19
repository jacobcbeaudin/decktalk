"""The URL a page section is opened at, and the capture that records it.

The page is opened at `http://project.localhost/<page>?scene=<scene>&<params>&words=…&cues=id@t,…`,
which the local origin serves from the project directory, and it is recorded for its span in the
narration plus `record_margin_seconds`. A section whose frames stall is recorded again while the
machine is quieter, up to `[record] retries` times.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ...artifacts import CueTimes, RecordingLog, TimelineSection
from ...errors import ConfigError
from ...media.browser import record_page
from ...media.origin import page_url
from ...model import PageSection, Project

log = logging.getLogger(__name__)


# ---- the page URL ---------------------------------------------------------------------------


def scene_params(section: PageSection, cue_times: CueTimes | None) -> dict[str, str]:
    """The section's own query parameters, plus its resolved cues unless the section sets `cues` itself."""
    params = dict(section.params)
    if cue_times is not None and "cues" not in params:
        query = cue_times.query(section.key)
        if query:
            params["cues"] = query
    return params


def scene_url(project: Project, section: PageSection, params: dict[str, str]) -> str:
    """The local origin URL the recorder and the frame screenshots open for a page section."""
    page = project.path(section.page)
    if not page.exists():
        raise ConfigError(f"section {section.number}: page not found: {page}")
    query = {"scene": section.scene, **params}
    words = words_query(project, section)
    if words and "words" not in query:
        query["words"] = words
    prev = prev_words_query(project, section)
    if prev and "prevwords" not in query:
        query["prevwords"] = prev
    query["t0"] = "signal"
    return page_url(section.page, query)


def words_param(sec: TimelineSection) -> str | None:
    """A timeline section's words as word@seconds pairs, in seconds after that section starts."""
    if not sec.words:
        return None
    return ",".join(
        f"{w.word.replace(',', '').replace('@', '')}@{max(0.0, w.start - sec.start):.2f}" for w in sec.words
    )


def words_query(project: Project, section: PageSection) -> str | None:
    """The section's spoken words with their seconds after the section starts, for data-text="spoken" reveals."""
    timeline = project.timeline()
    if timeline is None or section.key not in timeline.sections:
        return None
    return words_param(timeline.sections[section.key])


def prev_words_query(project: Project, section: PageSection) -> str | None:
    """The spoken words of the section just before this one in the narration, in seconds after that section starts.

    A page that opens on the previous section's last frame reads them, so a value it carries
    across the cut, such as a word's time, matches what the previous recording showed.
    """
    timeline = project.timeline()
    if timeline is None or section.key not in timeline.sections:
        return None
    keys = list(timeline.sections)
    at = keys.index(section.key)
    return words_param(timeline.sections[keys[at - 1]]) if at > 0 else None


@dataclass(frozen=True)
class Job:
    """One section the recorder is about to open, and what it would be recorded from."""

    section: PageSection
    url: str
    seconds: float
    out: Path
    log_path: Path


def plan_job(project: Project, section: PageSection, cue_times: CueTimes | None, seconds: float) -> Job:
    """What recording one section would open and write."""
    return Job(
        section=section,
        url=scene_url(project, section, scene_params(section, cue_times)),
        seconds=seconds,
        out=project.recording(section),
        log_path=project.recording_log(section),
    )


# ---- the capture ----------------------------------------------------------------------------


def capture_section(project: Project, browser: object, job: Job) -> RecordingLog:
    """Record one section, retrying while its frames stall, and return the log of the run that stuck."""
    cfg = project.settings.record
    video = project.settings.video
    recording_log = None
    for attempt in range(1, cfg.retries + 2):
        recording_log = record_page(
            browser,
            job.url,
            job.seconds,
            job.out,
            root=project.root,
            settle_seconds=cfg.settle_seconds,
            min_cover_seconds=cfg.min_cover_seconds,
            width=video.width,
            height=video.height,
            color_scheme=cfg.color_scheme,
        )
        stall = recording_log.worst_stall_ms
        if stall <= cfg.stall_ms or attempt > cfg.retries:
            break
        # A stalled page froze a reveal for a few frames, which no cut can repair, so the section
        # is recorded again while the machine is quieter.
        log.warning(
            "       frames stalled for %d ms; recording section %s again (%d/%d)",
            stall,
            job.section.key,
            attempt,
            cfg.retries,
        )
    assert recording_log is not None  # the loop runs at least once
    return recording_log
