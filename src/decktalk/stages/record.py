"""Stage 3: record each page section with headless Chromium, driven by the resolved cues.

The page is opened as file:///<project>/<page>?scene=<scene>&<params>&t0=<settle>&cues=<id@t,...>
and recorded for its span in the timeline plus record_margin_seconds. The page is covered in
magenta from its first paint until the recorder starts the narration clock, which it does
only after `settle` seconds past load and at least `min_cover` seconds after the recorder
was created. The first clean frame in the recording is therefore narration t=0, and
`decktalk measure` finds it, regardless of when Chromium's capture actually began.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from ..artifacts import CueTimes, RecordingLog, TimelineSection
from ..errors import ConfigError, MissingInputError
from ..media.browser import chromium, record_page
from ..project import PageSection, Project

log = logging.getLogger(__name__)


def scene_params(section: PageSection, cue_times: CueTimes | None) -> dict[str, str]:
    """The section's own query parameters, plus its resolved cues as `cues` unless the section sets that key itself."""
    params = dict(section.params)
    if cue_times is not None and "cues" not in params:
        query = cue_times.query(section.key)
        if query:
            params["cues"] = query
    return params


def scene_url(project: Project, section: PageSection, params: dict[str, str]) -> str:
    """The file URL the recorder and the frame screenshots open for a page section."""
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
    return page.resolve().as_uri() + "?" + urlencode(query)


def _words_param(sec: TimelineSection) -> str | None:
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
    return _words_param(timeline.sections[section.key])


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
    return _words_param(timeline.sections[keys[at - 1]]) if at > 0 else None


@dataclass
class RecordResult:
    section: PageSection
    path: Path
    log: RecordingLog
    seconds: float


def record(
    project: Project,
    *,
    only: list[int] | None = None,
    seconds: float | None = None,
    use_cues: bool = True,
) -> list[RecordResult]:
    cfg = project.settings.record
    video = project.settings.video
    timeline = project.timeline()
    if timeline is None and seconds is None:
        raise MissingInputError(f"{project.timeline_path} not found; run `decktalk narrate` first, or pass seconds")
    cue_times = project.cue_times() if use_cues else None
    wanted = set(only) if only else None

    jobs: list[tuple[PageSection, str, float, Path]] = []
    for section in project.page_sections:
        if wanted is not None and section.number not in wanted:
            continue
        span = timeline.span(section.key) if timeline else None
        length = seconds if seconds is not None else (span + section.record_margin_seconds if span else None)
        if not length:
            log.warning("section %s: no narration span yet; skipped", section.key)
            continue
        url = scene_url(project, section, scene_params(section, cue_times))
        jobs.append((section, url, length, project.recording(section)))
    if not jobs:
        raise ConfigError("nothing to record: no page sections matched")

    results: list[RecordResult] = []
    with chromium() as browser:
        for section, url, length, out in jobs:
            log.info("[rec ] section %s (%s?scene=%s)  %.1fs ...", section.key, section.page, section.scene, length)
            for attempt in range(1, cfg.retries + 2):
                recording_log = record_page(
                    browser,
                    url,
                    length,
                    out,
                    settle_seconds=cfg.settle_seconds,
                    min_cover_seconds=cfg.min_cover_seconds,
                    width=video.width,
                    height=video.height,
                    color_scheme=cfg.color_scheme,
                )
                stall = recording_log.worst_stall_ms
                if stall <= cfg.stall_ms or attempt > cfg.retries:
                    break
                # A stalled page froze a reveal for a few frames, which no cut can repair, so
                # the section is recorded again while the machine is quieter.
                log.warning(
                    "       frames stalled for %d ms; recording section %s again (%d/%d)",
                    stall,
                    section.key,
                    attempt,
                    cfg.retries,
                )
            log.info("       %s  (clock start %.2fs)", out.relative_to(project.root), recording_log.clock_start_seconds)
            results.append(RecordResult(section=section, path=out, log=recording_log, seconds=length))
    return results
