"""Stage 3: record each page section with headless Chromium, driven by the resolved cues.

The page is opened as file:///<project>/<page>?scene=<scene>&<params>&t0=<settle>&beats=<id@t,...>
and recorded for its span in the timeline plus extra_seconds. The page is covered in
magenta from its first paint until the recorder starts the narration clock, which it does
only after `settle` seconds past load and at least `min_lead` seconds after the recorder
was created. The first clean frame in the recording is therefore narration t=0, and
`decktalk measure` finds it, regardless of when Chromium's capture actually began.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from ..artifacts import Sidecar
from ..errors import ConfigError, MissingInputError
from ..media.browser import chromium, record_page
from ..project import PageSection, Project

log = logging.getLogger(__name__)


def scene_url(project: Project, section: PageSection, params: dict[str, str], settle: float) -> str:
    page = project.path(section.page)
    if not page.exists():
        raise ConfigError(f"section {section.number}: page not found: {page}")
    query = {"scene": section.scene, **params}
    words = words_query(project, section)
    if words and "words" not in query:
        query["words"] = words
    query["t0"] = "signal"
    return page.resolve().as_uri() + "?" + urlencode(query)


def words_query(project: Project, section: PageSection) -> str | None:
    """The section's spoken words with their seconds after the section starts, for data-sync reveals."""
    timeline = project.timeline()
    if timeline is None or section.key not in timeline.sections:
        return None
    sec = timeline.sections[section.key]
    if not sec.words:
        return None
    return ",".join(
        f"{w.word.replace(',', '').replace('@', '')}@{max(0.0, w.start - sec.start):.2f}" for w in sec.words
    )


@dataclass
class Recording:
    section: PageSection
    path: Path
    sidecar: Sidecar
    seconds: float


def record(
    project: Project,
    *,
    only: list[int] | None = None,
    seconds: float | None = None,
    use_beats: bool = True,
) -> list[Recording]:
    cfg = project.settings.record
    video = project.settings.video
    timeline = project.timeline()
    if timeline is None and seconds is None:
        raise MissingInputError(f"{project.timeline_path} not found; run `decktalk narrate` first, or pass seconds")
    beats = project.beats() if use_beats else None
    wanted = set(only) if only else None

    jobs: list[tuple[PageSection, str, float, Path]] = []
    for section in project.page_sections:
        if wanted is not None and section.number not in wanted:
            continue
        span = timeline.span(section.key) if timeline else None
        length = seconds if seconds is not None else (span + section.extra_seconds if span else None)
        if not length:
            log.warning("section %s: no narration span yet; skipped", section.key)
            continue
        params = dict(section.params)
        if beats is not None and "beats" not in params:
            query = beats.query(section.key)
            if query:
                params["beats"] = query
        jobs.append(
            (section, scene_url(project, section, params, cfg.settle_seconds), length, project.recording(section))
        )
    if not jobs:
        raise ConfigError("nothing to record: no page sections matched")

    results: list[Recording] = []
    with chromium() as browser:
        for section, url, length, out in jobs:
            log.info("[rec ] section %s (%s?scene=%s)  %.1fs ...", section.key, section.page, section.scene, length)
            sidecar = record_page(
                browser,
                url,
                length,
                out,
                settle_seconds=cfg.settle_seconds,
                min_lead_seconds=cfg.min_lead_seconds,
                width=video.width,
                height=video.height,
                color_scheme=cfg.color_scheme,
            )
            log.info("       %s  (lead %.2fs)", out.relative_to(project.root), sidecar.lead_seconds)
            results.append(Recording(section=section, path=out, sidecar=sidecar, seconds=length))
    return results
