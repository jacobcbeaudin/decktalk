"""Rendering the frozen frames into build/preflight and comparing them.

A frozen frame shows every reveal in its end state and nothing in motion, so the share of pixels that
change between the frame before a cue and the frame at it estimates what `verify`\'s probe will read
once the reveal has settled, with a control of 0. The same two frames either side of a cut estimate
whether a section that carries the picture before it will pop.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...artifacts import CueTimes
from ...jsonio import relative
from ...media import frames
from ...media.browser import await_ready, chromium, open_page, page_error_text, screenshot
from ...media.origin import page_url
from ...model import PageSection, Project
from ...pagescan import slide_findings
from ...settings import VerifyConfig
from ...verdicts import Finding, SkipReason, Verdict
from ..record import prev_words_query, words_query
from ..verify import opted_out, thin_change
from .freeze import Freeze, Slides, first_state, last_state, plan_frames, slide_cues

log = logging.getLogger(__name__)


def rel_to(path: Path | None, root: Path) -> str | None:
    """One optional path relative to the project root, which the frozen-frame rows carry."""
    return None if path is None else relative(path, root)


def cue_verdict(share: float, cfg: VerifyConfig) -> Verdict:
    """NO CHANGE, THIN CHANGE?, or changed for a frozen share, whose control is 0 so its margin is the share."""
    if share < cfg.min_changed_percent or share < cfg.min_margin_percent:
        return Verdict.NO_CHANGE
    return Verdict.THIN_CHANGE if thin_change(share, share, cfg) else Verdict.CHANGED


@dataclass
class CueEstimate:
    check: str  # SECTION:CUE
    cue_seconds: float
    slide: str | None
    changed_percent: float | None
    verdict: Verdict
    reason: SkipReason | None = None
    detail: str = ""
    note: str = ""
    before: Path | None = None
    after: Path | None = None

    def to_dict(self, root: Path) -> dict[str, Any]:
        section, cue = self.check.split(":", 1)
        return {
            "section": int(section),
            "cue": cue,
            "cue_seconds": round(self.cue_seconds, 3),
            "slide": self.slide,
            "changed_percent": None if self.changed_percent is None else round(self.changed_percent, 2),
            "verdict": self.verdict.to_dict(),
            "reason": None if self.reason is None else self.reason.value,
            "detail": self.detail or None,
            "note": self.note or None,
            "before": rel_to(self.before, root),
            "after": rel_to(self.after, root),
        }


@dataclass
class SeamEstimate:
    key: str
    changed_percent: float | None
    verdict: Verdict
    reason: SkipReason | None = None
    detail: str = ""
    last: Path | None = None
    first: Path | None = None

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "key": self.key,
            "changed_percent": None if self.changed_percent is None else round(self.changed_percent, 2),
            "verdict": self.verdict.to_dict(),
            "reason": None if self.reason is None else self.reason.value,
            "detail": self.detail or None,
            "last": rel_to(self.last, root),
            "first": rel_to(self.first, root),
        }


def freeze_url(project: Project, section: PageSection, freeze: Freeze) -> str:
    """The page URL of a frozen state, with the section's own params and the words the recorder would pass."""
    params = section.freeze_params
    words = words_query(project, section)
    if words and "words" not in params:
        params["words"] = words
    prev = prev_words_query(project, section)
    if prev and "prevwords" not in params:
        params["prevwords"] = prev
    return page_url(section.page, {**freeze.query(), **params})


def section_scan(
    catalog: list[dict[str, Any]] | None, sec: PageSection, times: dict[str, float], video: Any
) -> list[Finding]:
    """Every static judgement one section's slides support: off the stage, under the captions, too close.

    The rows come from the catalog the probe measured, which carries one box per revealed element,
    so nothing here opens a file or reads a frame.
    """
    entry = next((c for c in catalog or [] if str(c.get("scene")) == sec.scene), None)
    elements = entry.get("elements") if isinstance(entry, dict) else None
    if not isinstance(elements, dict):
        return []
    rows = [row for slide in elements.values() if isinstance(slide, list) for row in slide]
    return slide_findings(rows, times, width=video.width, height=video.height, page=sec.page, section=sec.number)


def frame_estimates(
    project: Project, cue_times: CueTimes, *, only: list[int] | None = None
) -> tuple[list[CueEstimate], list[SeamEstimate], list[str], list[tuple[str, str]], list[Finding]]:
    """Render the frozen frames, compare them, and collect what the pages warned about and threw.

    A page that threw is reported apart from a page that warned, because a warning is what the
    runtime could not honour and a throw is the page failing, which is a certain finding.
    """
    vcfg = project.settings.verify
    video = project.settings.video
    out_dir = project.preflight_dir
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    skip = opted_out(project)
    size = {"level": vcfg.diff_level, "width": vcfg.probe_width, "height": vcfg.probe_height}
    wanted = [s for s in project.sections if not only or s.number in set(only)]
    cues: list[CueEstimate] = []
    seams: list[SeamEstimate] = []
    static: list[Finding] = []
    with chromium(project.settings.record.browser_path) as browser:
        page, _assets = open_page(browser, project.root, width=video.width, height=video.height)
        # A page error names the page that was open when it was thrown, because one Chromium page
        # draws every section's frames and the message alone would not say which deck failed.
        errors: list[tuple[str, str]] = []
        drawing = {"page": ""}
        page.on("pageerror", lambda e: errors.append((drawing["page"], page_error_text(e))))
        catalogs: dict[str, list[dict[str, Any]] | None] = {}
        rendered: dict[str, Path] = {}
        warnings: list[str] = []

        def scene_slides(sec: PageSection) -> tuple[Slides | None, SkipReason | None, str]:
            # The catalog probe opens the page too, so a throw here belongs to this page rather
            # than to whichever page the last frozen frame was drawn from.
            drawing["page"] = sec.page
            if sec.page not in catalogs:
                html = project.path(sec.page)
                if not html.exists():
                    catalogs[sec.page] = None
                else:
                    page.goto(page_url(sec.page))
                    await_ready(page)
                    catalogs[sec.page] = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            slides, why = slide_cues(catalogs[sec.page], sec.scene)
            if slides is None:
                return None, SkipReason.NO_CATALOG, f"{sec.page} {why}"
            return slides, None, ""

        def render_frozen(sec: PageSection, freeze: Freeze) -> Path:
            url = freeze_url(project, sec, freeze)
            drawing["page"] = sec.page
            if url not in rendered:
                target = out_dir / sec.key / f"{freeze.label}.png"
                said = screenshot(page, url, target, settle_ms=project.settings.record.screenshot_settle_ms)
                warnings.extend(w for w in said if w not in warnings)
                rendered[url] = target
            return rendered[url]

        for sec in wanted:
            if not isinstance(sec, PageSection) or not cue_times.sections.get(sec.key):
                continue
            section_cue_times = cue_times.times(sec.key)
            slides, reason, detail = scene_slides(sec)
            # The measured catalog says where every reveal sits, so the judgements that need no
            # picture are made here, from the same catalog the freeze planning reads.
            static.extend(section_scan(catalogs.get(sec.page), sec, section_cue_times, video))
            if slides is None:
                # A page the recorder cannot drive plays nothing, so this is certain rather than skipped.
                for cue, t in sorted(section_cue_times.items(), key=lambda item: item[1]):
                    cues.append(CueEstimate(f"{sec.number}:{cue}", t, None, None, Verdict.PAGE_ERROR, reason, detail))
                continue
            for pair in plan_frames(slides, section_cue_times, video.fps):
                check = f"{sec.number}:{pair.cue}"
                if (sec.key, pair.cue) in skip:
                    detail = 'cues.json sets "verify": false'
                    cues.append(
                        CueEstimate(
                            check, pair.seconds, pair.slide, None, Verdict.SKIPPED, SkipReason.OPTED_OUT, detail
                        )
                    )
                    continue
                if pair.reason is not None or pair.before is None or pair.after is None:
                    cues.append(
                        CueEstimate(check, pair.seconds, pair.slide, None, Verdict.SKIPPED, pair.reason, pair.detail)
                    )
                    continue
                a, b = render_frozen(sec, pair.before), render_frozen(sec, pair.after)
                share = frames.changed_images_percent(a, b, **size)
                verdict = cue_verdict(share, vcfg)
                cues.append(
                    CueEstimate(check, pair.seconds, pair.slide, share, verdict, note=pair.note, before=a, after=b)
                )

        for prev, sec in zip(project.sections, project.sections[1:], strict=False):
            if not sec.seamless or sec not in wanted:
                continue
            if not isinstance(sec, PageSection) or not isinstance(prev, PageSection):
                detail = "a clip has no frozen frame. `decktalk verify` checks this seam after assemble"
                seams.append(SeamEstimate(sec.key, None, Verdict.SKIPPED, SkipReason.CLIP, detail))
                continue
            prev_slides, _r1, _d1 = scene_slides(prev)
            slides, _r2, _d2 = scene_slides(sec)
            last = last_state(prev_slides, cue_times.times(prev.key)) if prev_slides else None
            first = first_state(slides, cue_times.times(sec.key), video.fps) if slides else None
            if last is None or first is None:
                detail = "a side of the cut has no resolved cue, or its page has no catalog"
                seams.append(SeamEstimate(sec.key, None, Verdict.SKIPPED, SkipReason.NO_CUES, detail))
                continue
            a, b = render_frozen(prev, last), render_frozen(sec, first)
            share = frames.changed_images_percent(a, b, **size)
            verdict = Verdict.OK if share <= vcfg.max_pop_percent else Verdict.POP_AT_CUT
            seams.append(SeamEstimate(sec.key, share, verdict, last=a, first=b))
        for where, message in sorted(set(errors)):
            log.warning("[page] preflight  page error (%s): %s", where, message)
        thrown = sorted(set(errors))
    log.info(
        "[pre ] %d frozen frame(s) in %s",
        len(rendered),
        out_dir.relative_to(project.root) if out_dir.is_relative_to(project.root) else out_dir,
    )
    return cues, seams, warnings, thrown, static
