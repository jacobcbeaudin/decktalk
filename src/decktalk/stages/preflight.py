"""Preflight: what a voiced build would spend and show, with no credits and no recording.

takes    what `narrate` would voice, keep cached, or move, as `narrate --dry-run` plans it, with the
         characters each section sends and speaks.
cues     every cue of cues.json resolved against the words each section will have: a cached or
         moved take's own words, or estimated words at silent_words_per_minute for a section that
         would be voiced. The notes and findings are those of `decktalk align`, the repeated-phrase
         warning included. cue-times.json is not written.
frames   for each cue, the page frozen just before the cue fires and frozen at the cue, in
         build/preflight. They are compared as `verify` compares frames: the share of pixels whose
         luma changes by more than diff_level at probe_width by probe_height. A frozen frame shows
         every reveal in its end state and nothing in motion, so the share estimates what verify's
         probe reads once the reveal has settled, with a control of 0. The share reads NO CHANGE
         below min_changed_percent or min_margin_percent, THIN CHANGE? below thin_change_factor
         times either floor, and changed otherwise.
seams    for each page section that sets seamless after a page section, the previous
         section's last frozen state against this section's first one. A share above
         max_pop_percent reads POP AT CUT.

The frozen frames follow the runtime's cue mode. The first cued slide mounts at t=0, and every other
slide mounts at its earliest cue. The frame before a cue is its slide with the cues before it fired,
or the slide on screen before it when the cue mounts its slide. A freeze fires a slide's cues in
preview order, so a slide whose cue times run in another order gets a note. The page reads
`catalog[].cues` and `&before=`, which decktalk-runtime.js provides.

A skipped cue row carries one of these reasons:

    SkipReason.AT_SECTION_START    the cue fires within the first frame, so no frame comes before it
    SkipReason.NO_SLIDE            no slide of the scene owns the cue
    SkipReason.NOT_IN_SLIDE_CUES   the slide owns the cue by its id prefix, and a freeze can stop only at a listed cue
    SkipReason.NO_CATALOG          the page is missing, or its catalog registers the scene with no slides and cues
    SkipReason.OPTED_OUT           cues.json sets "verify": false on the cue

A seam row is skipped as SkipReason.NO_CUES when a side has no resolved cue, and as CLIP when a side is a clip.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..artifacts import CueTimes, Word
from ..jsonio import relative
from ..media import frames
from ..media.browser import await_ready, chromium, page_error_text, screenshot
from ..model import PageSection, Project
from ..settings import NarrationConfig, VerifyConfig
from ..verdicts import Findings, SkipReason, Verdict
from .align import AlignResult, resolve_sections, unknown_cue_ids
from .narrate import (
    CACHED,
    MOVED,
    UNKNOWN,
    TakePlan,
    estimated_words,
    narration_plan,
    plan_totals,
    section_config,
)
from .record import prev_words_query, words_query
from .verify import opted_out, thin_change

log = logging.getLogger(__name__)

ORDER_NOTE = "the slide's cues fire in another order when frozen, so these frames may differ from the recording"

Slides = dict[str, list[str]]  # Each slide id of a scene, in page order, with its cue ids in preview order.


# ---- frame planning, with no browser ------------------------------------------------------


@dataclass(frozen=True)
class Freeze:
    """One frozen state of a page: a slide, with its cues fired up to `cue`, or before `before`, or all of them."""

    slide: str
    cue: str | None = None
    before: str | None = None

    def query(self) -> dict[str, str]:
        if self.cue is not None:
            return {"slide": self.slide, "after": self.cue}
        if self.before is not None:
            return {"slide": self.slide, "before": self.before}
        return {"slide": self.slide}

    @property
    def label(self) -> str:
        """The query as one file-name-safe word, such as slide-2.1-after-2.1aloud."""
        text = "-".join(part for pair in self.query().items() for part in pair)
        return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


@dataclass(frozen=True)
class FramePair:
    """The two frozen states whose difference estimates one cue's reveal, or why there are none."""

    cue: str
    seconds: float
    slide: str | None
    before: Freeze | None = None
    after: Freeze | None = None
    reason: SkipReason | None = None
    detail: str = ""
    note: str = ""


def slide_cues(catalog: list[dict[str, Any]] | None, scene: str) -> tuple[Slides | None, str]:
    """Each slide of a scene with its cue ids in preview order, or the reason the scene gives none.

    A catalog entry carries `slides` and `cues`, which the runtime page contract defines. A page
    that registers a scene without them tells `preflight` nothing it can freeze, so that scene
    reads as a scene with no catalog.
    """
    if not catalog:
        return None, "is missing or has no runtime catalog"
    entry = next((c for c in catalog if str(c.get("scene")) == scene), None)
    if entry is None:
        return None, f"registers no scene {scene}"
    slides, cues = entry.get("slides"), entry.get("cues")
    if not isinstance(slides, list) or not isinstance(cues, dict):
        return None, f"registers scene {scene} without a slides list and a cues map"
    return {str(sid): [str(c) for c in cues.get(str(sid), [])] for sid in slides}, ""


def owner_slide(cue: str, slides: Slides) -> str | None:
    """The slide that owns a cue, by the runtime's rules: the same id or a listed cue first, else the longest prefix."""
    best: str | None = None
    for sid, cues in slides.items():
        if sid == cue or cue in cues:
            return sid
        if cue.startswith(sid) and (best is None or len(sid) > len(best)):
            best = sid
    return best


def mounts(slides: Slides, cue_times: dict[str, float]) -> list[tuple[str, float]]:
    """(slide, mount time) in mount order: each slide at its earliest cue, and the first at 0 at the latest."""
    at: dict[str, float] = {}
    for cue, t in sorted(cue_times.items(), key=lambda item: item[1]):
        sid = owner_slide(cue, slides)
        if sid is not None:
            at[sid] = min(at.get(sid, math.inf), t)
    seq = sorted(at.items(), key=lambda item: item[1])
    if seq:
        seq[0] = (seq[0][0], min(seq[0][1], 0.0))
    return seq


def _fired(slides: Slides, cue_times: dict[str, float], sid: str, until: float, *, inclusive: bool) -> list[str]:
    """The slide's listed cues that have fired by `until`, in preview order."""
    return [
        c
        for c in slides[sid]
        if c in cue_times
        and owner_slide(c, slides) == sid
        and (cue_times[c] <= until + 1e-9 if inclusive else cue_times[c] < until - 1e-9)
    ]


def plan_frames(slides: Slides, cue_times: dict[str, float], fps: int) -> list[FramePair]:
    """One FramePair per cue of a section, in cue time order."""
    seq = mounts(slides, cue_times)
    mount = dict(seq)
    pairs: list[FramePair] = []
    for cue, t in sorted(cue_times.items(), key=lambda item: item[1]):
        sid = owner_slide(cue, slides)
        if sid is None:
            detail = "no slide of the scene owns the cue"
            pairs.append(FramePair(cue, t, None, reason=SkipReason.NO_SLIDE, detail=detail))
            continue
        order = slides[sid]
        if cue not in order:
            detail = f"slide {sid} owns the cue by its id, and its preview list does not name it"
            pairs.append(FramePair(cue, t, sid, reason=SkipReason.NOT_IN_SLIDE_CUES, detail=detail))
            continue
        if t * fps < 1:
            detail = "the cue fires within the first frame, so no frame comes before it"
            pairs.append(FramePair(cue, t, sid, reason=SkipReason.AT_SECTION_START, detail=detail))
            continue
        k = order.index(cue)
        earlier = [(s, m) for s, m in seq if m < t - 1e-9 and s != sid]
        if mount[sid] < t - 1e-9 or not earlier:
            before = Freeze(sid, cue=order[k - 1]) if k > 0 else Freeze(sid, before=cue)
        else:
            # The cue mounts its slide, so the frame before it shows the slide that was on screen.
            prev = earlier[-1][0]
            fired = _fired(slides, cue_times, prev, t, inclusive=False)
            if fired:
                before = Freeze(prev, cue=fired[-1])
            elif slides[prev]:
                before = Freeze(prev, before=slides[prev][0])
            else:
                before = Freeze(prev)
        by_time = set(_fired(slides, cue_times, sid, t, inclusive=True))
        frozen = {c for c in order[: k + 1] if c in cue_times and owner_slide(c, slides) == sid}
        note = "" if by_time == frozen else ORDER_NOTE
        pairs.append(FramePair(cue, t, sid, before, Freeze(sid, cue=cue), note=note))
    return pairs


def last_state(slides: Slides, cue_times: dict[str, float]) -> Freeze | None:
    """The frozen state a section ends on: its last mounted slide with every listed cue fired."""
    seq = mounts(slides, cue_times)
    if not seq:
        return None
    sid = seq[-1][0]
    fired = _fired(slides, cue_times, sid, math.inf, inclusive=True)
    return Freeze(sid, cue=fired[-1]) if fired else Freeze(sid)


def first_state(slides: Slides, cue_times: dict[str, float], fps: int) -> Freeze | None:
    """The frozen state a section opens on: its first slide with the cues of its first frame fired."""
    seq = mounts(slides, cue_times)
    if not seq:
        return None
    sid = seq[0][0]
    fired = _fired(slides, cue_times, sid, 1.0 / fps, inclusive=False)
    if fired:
        return Freeze(sid, cue=fired[-1])
    return Freeze(sid, before=slides[sid][0]) if slides[sid] else Freeze(sid)


# ---- results ------------------------------------------------------------------------------


def _rel(path: Path | None, root: Path) -> str | None:
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
            "verdict": self.verdict,
            "reason": self.reason,
            "detail": self.detail or None,
            "note": self.note or None,
            "before": _rel(self.before, root),
            "after": _rel(self.after, root),
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
            "verdict": self.verdict,
            "reason": self.reason,
            "detail": self.detail or None,
            "last": _rel(self.last, root),
            "first": _rel(self.first, root),
        }


@dataclass
class PreflightResult:
    voice: dict[str, Any]
    narration: NarrationConfig
    takes: list[TakePlan]
    note: str | None
    align: AlignResult
    estimated: list[str]  # Section keys whose cue times come from estimated words.
    cues: list[CueEstimate] = field(default_factory=list)
    seams: list[SeamEstimate] = field(default_factory=list)
    frames: Path | None = None  # build/preflight, or None when no frame was rendered.
    root: Path | None = None  # The project root, which the table prints paths against.
    allow_unknown_cues: bool = False  # The run was told to carry on past an unknown cue id.

    @property
    def placeholders(self) -> list[str]:
        return sorted({p for plan in self.takes for p in plan.segment.placeholders})

    @property
    def short(self) -> int:
        return sum(
            1
            for s in self.align.sections
            if not s.skipped and s.min_seconds is not None and s.speech_end < s.min_seconds
        )

    @property
    def findings(self) -> Findings:
        """Certain: a placeholder a voiced run refuses, UNRESOLVED, UNKNOWN CUE, NO CHANGE, POP AT CUT.

        Uncertain: speech shorter than min_seconds, and THIN CHANGE?.
        """
        unknown = 0 if self.allow_unknown_cues else self.align.unknown
        own = Findings(certain=len(self.placeholders) + self.align.unresolved + unknown, uncertain=self.short)
        return own + Findings.of(c.verdict for c in self.cues) + Findings.of(k.verdict for k in self.seams)

    def to_dict(self, root: Path) -> dict[str, Any]:
        cue_times = self.align.to_dict(root)
        cue_times.pop("cue_times_file", None)
        return {
            "voice": self.voice,
            "note": self.note,
            "placeholders": self.placeholders,
            "takes": [p.to_dict(self.narration) for p in self.takes],
            "totals": plan_totals(self.takes, self.narration),
            "cue_times": {**cue_times, "estimated_sections": self.estimated},
            "cues": [c.to_dict(root) for c in self.cues],
            "seams": [k.to_dict(root) for k in self.seams],
            "frames": _rel(self.frames, root),
        }


# ---- the stage ----------------------------------------------------------------------------


def planned_words(project: Project, plan: TakePlan) -> tuple[list[Word], float, bool]:
    """(words, length, estimated) a section will have after a voiced run, in seconds after the section starts."""
    seg = plan.segment
    key = seg.key
    lead = project.lead_seconds(key)
    takes = project.takes()
    entry = takes.sections.get(key) if takes is not None and not takes.estimated else None
    if plan.status == CACHED and entry is not None:
        return project.section_words(key, entry.words_file), entry.duration_seconds + lead, False
    if plan.status == MOVED and plan.source is not None:
        return project.section_words(key, plan.source.words_file), plan.source.duration_seconds + lead, False
    if plan.status == UNKNOWN and entry is not None and entry.spoken == seg.spoken:
        # The voice is not set up, but the take was voiced from this exact text.
        return project.section_words(key, entry.words_file), entry.duration_seconds + lead, False
    cfg = section_config(project, seg)
    length = seg.silent_seconds(cfg)
    words = [Word(w.word, round(w.start + lead, 3), round(w.end + lead, 3)) for w in estimated_words(seg, length, cfg)]
    return words, length + lead, True


def preflight(
    project: Project,
    *,
    only: list[int] | None = None,
    frames: bool = True,
    model: str | None = None,
    allow_unknown_cues: bool = False,
) -> PreflightResult:
    """Plan the takes, resolve the cues, and estimate every reveal and seam from frozen renders.

    `only` keeps these section numbers. With `frames` off, no browser starts and no file is written.
    Otherwise the frozen frames go to build/preflight, which is emptied first. Nothing else is written.
    With `allow_unknown_cues`, a cue id that appears nowhere in its page is not a finding.
    """
    cfg = project.settings.narration
    _all, spoken = project.script_sections()
    wanted = set(only or ())

    def named(number: int) -> bool:
        return not only or number in wanted

    # A seam compares the previous section's last picture with this section's first, so a
    # previous section that `only` leaves out still gets its take and cues resolved. It is not reported.
    behind = {
        prev.number
        for prev, sec in zip(project.sections, project.sections[1:], strict=False)
        if only and sec.seamless and sec.number in wanted and prev.number not in wanted
    }
    model = model or project.voice.model or cfg.model
    plans, note = narration_plan(project, [s for s in spoken if named(s.index) or s.index in behind], model=model)
    take_words: dict[str, tuple[list[Word], float]] = {}
    estimated: list[str] = []
    for plan in plans:
        words, length, guessed = planned_words(project, plan)
        take_words[plan.segment.key] = (words, length)
        if guessed and named(plan.segment.index):
            estimated.append(plan.segment.key)
    all_specs = project.cue_specs()
    specs = [s for s in all_specs if named(s.number)]
    unknown_ids = unknown_cue_ids(project, specs)
    cue_times, rows, unresolved = resolve_sections(specs, take_words, unknown_ids=unknown_ids, estimated=True)
    result = PreflightResult(
        voice={"provider": project.voice.provider, "model": model, "settings": project.voice.api_settings()},
        narration=cfg,
        takes=[plan for plan in plans if named(plan.segment.index)],
        note=note,
        align=AlignResult(
            cue_times=cue_times,
            sections=rows,
            unresolved=unresolved,
            estimated=bool(estimated),
            unknown=len(unknown_ids),
        ),
        estimated=estimated,
        root=project.root,
        allow_unknown_cues=allow_unknown_cues,
    )
    if frames:
        carried = cue_times
        extra = [s for s in all_specs if s.number in behind]
        if extra:
            extra_ids = unknown_cue_ids(project, extra)
            behind_cue_times, _rows, _unresolved = resolve_sections(
                extra, take_words, unknown_ids=extra_ids, estimated=True
            )
            carried = CueTimes({**behind_cue_times.sections, **cue_times.sections})
        result.cues, result.seams = frame_estimates(project, carried, only=only)
        result.frames = project.build / "preflight"
    return result


def freeze_url(project: Project, section: PageSection, freeze: Freeze) -> str:
    """The page URL of a frozen state, with the section's own params and the words the recorder would pass."""
    params = {k: v for k, v in section.params.items() if k not in ("cues", "t0", "slide", "after", "before")}
    words = words_query(project, section)
    if words and "words" not in params:
        params["words"] = words
    prev = prev_words_query(project, section)
    if prev and "prevwords" not in params:
        params["prevwords"] = prev
    return project.path(section.page).resolve().as_uri() + "?" + urlencode({**freeze.query(), **params})


def frame_estimates(
    project: Project, cue_times: CueTimes, *, only: list[int] | None = None
) -> tuple[list[CueEstimate], list[SeamEstimate]]:
    """Render the frozen frames into build/preflight and compare them for every cue and every seam."""
    vcfg = project.settings.verify
    video = project.settings.video
    out_dir = project.build / "preflight"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    skip = opted_out(project)
    size = {"level": vcfg.diff_level, "width": vcfg.probe_width, "height": vcfg.probe_height}
    wanted = [s for s in project.sections if not only or s.number in set(only)]
    cues: list[CueEstimate] = []
    seams: list[SeamEstimate] = []
    with chromium(project.settings.record.browser_path) as browser:
        page = browser.new_page(viewport={"width": video.width, "height": video.height})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(page_error_text(e)))
        catalogs: dict[str, list[dict[str, Any]] | None] = {}
        rendered: dict[str, Path] = {}

        def scene_slides(sec: PageSection) -> tuple[Slides | None, SkipReason | None, str]:
            if sec.page not in catalogs:
                html = project.path(sec.page)
                if not html.exists():
                    catalogs[sec.page] = None
                else:
                    page.goto(html.resolve().as_uri())
                    await_ready(page)
                    catalogs[sec.page] = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            slides, why = slide_cues(catalogs[sec.page], sec.scene)
            if slides is None:
                return None, SkipReason.NO_CATALOG, f"{sec.page} {why}"
            return slides, None, ""

        def render_frozen(sec: PageSection, freeze: Freeze) -> Path:
            url = freeze_url(project, sec, freeze)
            if url not in rendered:
                target = out_dir / sec.key / f"{freeze.label}.png"
                screenshot(page, url, target, settle_ms=project.settings.record.screenshot_settle_ms)
                rendered[url] = target
            return rendered[url]

        for sec in wanted:
            if not isinstance(sec, PageSection) or not cue_times.sections.get(sec.key):
                continue
            section_cue_times = cue_times.times(sec.key)
            slides, reason, detail = scene_slides(sec)
            if slides is None:
                for cue, t in sorted(section_cue_times.items(), key=lambda item: item[1]):
                    cues.append(CueEstimate(f"{sec.number}:{cue}", t, None, None, Verdict.SKIPPED, reason, detail))
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
        for e in sorted(set(errors)):
            log.warning("[page] preflight  page error: %s", e)
    log.info(
        "[pre ] %d frozen frame(s) in %s",
        len(rendered),
        out_dir.relative_to(project.root) if out_dir.is_relative_to(project.root) else out_dir,
    )
    return cues, seams
