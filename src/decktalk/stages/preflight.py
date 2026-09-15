"""Preflight: what a voiced build would spend and show, with no credits and no recording.

takes    what `narrate` would voice, keep cached, or move, as `narrate --dry-run` plans it, with the
         characters each section sends and speaks.
cues     every cue of cues.json resolved against the words each section will have: a cached or
         moved take's own words, or estimated words at silent_words_per_minute for a section that
         would be voiced. The notes and findings are those of `decktalk beats`, the repeated-phrase
         warning included. beats.json is not written.
frames   for each cue, the page frozen just before the cue fires and frozen at the cue, in
         build/preflight. They are compared as `verify` compares frames: the share of pixels whose
         luma changes by more than diff_level at probe_width by probe_height. A frozen frame shows
         every reveal in its end state and nothing in motion, so the share estimates what verify's
         probe reads once the reveal has settled, with a control of 0. The share reads NO CHANGE
         below min_changed_percent or min_margin_percent, THIN CHANGE? below thin_change_factor
         times either floor, and changed otherwise.
carries  for each page section that sets carries_previous after a page section, the previous
         section's last frozen state against this section's first one. A share above
         max_pop_percent reads POP AT CUT.

The frozen frames follow the runtime's cue mode. The first cued step mounts at t=0, and every other
step mounts at its earliest cue. The frame before a cue is its step with the cues before it fired,
or the step on screen before it when the cue mounts its step. A freeze fires a step's cues in
autoplay order, so a step whose cue times run in another order gets a note. The page reads
`catalog[].cues` and `&before=`, which decktalk-runtime.js has had since this command came in.

A skipped cue row carries one of these reasons:

    AT_SECTION_START   the cue fires within the first frame, so no frame comes before it
    NO_STEP            no step of the scene owns the cue
    NOT_IN_STEP_CUES   the step owns the cue by its id prefix, and a freeze can stop only at a listed cue
    NO_CATALOG         the page is missing, has no runtime catalog, or does not register the scene
    RUNTIME_OUTDATED   the page's decktalk-runtime.js has no catalog cues, so run `decktalk runtime`
    OPTED_OUT          cues.json sets "verify": false on the cue

A carry row is skipped as NO_CUES when a side has no resolved cue, and as CLIP when a side is a clip.
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

from ..artifacts import Beats, Word
from ..config import NarrationConfig, VerifyConfig
from ..media import ffmpeg
from ..media.browser import await_ready, chromium, page_error_text, screenshot
from ..project import PageSection, Project
from ..verdicts import CHANGED, NO_CHANGE, OK, POP_AT_CUT, SKIPPED, THIN_CHANGE, Findings, count
from .beats import BeatsResult, load_cues, resolve_sections, unknown_cue_ids
from .narrate import (
    CACHED,
    MOVED,
    UNKNOWN,
    TakePlan,
    estimated_words,
    narration_plan,
    plan_totals,
    script_segments,
    section_config,
)
from .record import prev_words_query, words_query
from .verify import opted_out, thin_change

log = logging.getLogger(__name__)

AT_SECTION_START = "AT_SECTION_START"
NO_STEP = "NO_STEP"
NOT_IN_STEP_CUES = "NOT_IN_STEP_CUES"
NO_CATALOG = "NO_CATALOG"
RUNTIME_OUTDATED = "RUNTIME_OUTDATED"
OPTED_OUT = "OPTED_OUT"
NO_CUES = "NO_CUES"
CLIP = "CLIP"

ORDER_NOTE = "the step's cues fire in another order when frozen, so these frames may differ from the recording"

Steps = dict[str, list[str]]  # Each step id of a scene, in page order, with its cue ids in autoplay order.


# ---- frame planning, with no browser ------------------------------------------------------


@dataclass(frozen=True)
class Freeze:
    """One frozen state of a page: a step, with its cues fired up to `cue`, or before `before`, or all of them."""

    step: str
    cue: str | None = None
    before: str | None = None

    def query(self) -> dict[str, str]:
        if self.cue is not None:
            return {"step": self.step, "cue": self.cue}
        if self.before is not None:
            return {"step": self.step, "before": self.before}
        return {"step": self.step}

    @property
    def label(self) -> str:
        """The query as one file-name-safe word, such as step-2.1-cue-2.1aloud."""
        text = "-".join(part for pair in self.query().items() for part in pair)
        return re.sub(r"[^A-Za-z0-9._-]+", "_", text)


@dataclass(frozen=True)
class FramePair:
    """The two frozen states whose difference estimates one cue's reveal, or why there are none."""

    cue: str
    seconds: float
    step: str | None
    before: Freeze | None = None
    after: Freeze | None = None
    reason: str | None = None
    detail: str = ""
    note: str = ""


def owner_step(cue: str, steps: Steps) -> str | None:
    """The step that owns a cue, by the runtime's rules: the same id or a listed cue first, else the longest prefix."""
    best: str | None = None
    for sid, cues in steps.items():
        if sid == cue or cue in cues:
            return sid
        if cue.startswith(sid) and (best is None or len(sid) > len(best)):
            best = sid
    return best


def mounts(steps: Steps, beats: dict[str, float]) -> list[tuple[str, float]]:
    """(step, mount time) in mount order: each step at its earliest cue, and the first at 0 at the latest."""
    at: dict[str, float] = {}
    for cue, t in sorted(beats.items(), key=lambda item: item[1]):
        sid = owner_step(cue, steps)
        if sid is not None:
            at[sid] = min(at.get(sid, math.inf), t)
    seq = sorted(at.items(), key=lambda item: item[1])
    if seq:
        seq[0] = (seq[0][0], min(seq[0][1], 0.0))
    return seq


def _fired(steps: Steps, beats: dict[str, float], sid: str, until: float, *, inclusive: bool) -> list[str]:
    """The step's listed cues that have fired by `until`, in autoplay order."""
    return [
        c
        for c in steps[sid]
        if c in beats
        and owner_step(c, steps) == sid
        and (beats[c] <= until + 1e-9 if inclusive else beats[c] < until - 1e-9)
    ]


def plan_frames(steps: Steps, beats: dict[str, float], fps: int) -> list[FramePair]:
    """One FramePair per cue of a section, in cue time order."""
    seq = mounts(steps, beats)
    mount = dict(seq)
    pairs: list[FramePair] = []
    for cue, t in sorted(beats.items(), key=lambda item: item[1]):
        sid = owner_step(cue, steps)
        if sid is None:
            pairs.append(FramePair(cue, t, None, reason=NO_STEP, detail="no step of the scene owns the cue"))
            continue
        order = steps[sid]
        if cue not in order:
            detail = f"step {sid} owns the cue by its id, and its cues list does not name it"
            pairs.append(FramePair(cue, t, sid, reason=NOT_IN_STEP_CUES, detail=detail))
            continue
        if t * fps < 1:
            detail = "the cue fires within the first frame, so no frame comes before it"
            pairs.append(FramePair(cue, t, sid, reason=AT_SECTION_START, detail=detail))
            continue
        k = order.index(cue)
        earlier = [(s, m) for s, m in seq if m < t - 1e-9 and s != sid]
        if mount[sid] < t - 1e-9 or not earlier:
            before = Freeze(sid, cue=order[k - 1]) if k > 0 else Freeze(sid, before=cue)
        else:
            # The cue mounts its step, so the frame before it shows the step that was on screen.
            prev = earlier[-1][0]
            fired = _fired(steps, beats, prev, t, inclusive=False)
            if fired:
                before = Freeze(prev, cue=fired[-1])
            elif steps[prev]:
                before = Freeze(prev, before=steps[prev][0])
            else:
                before = Freeze(prev)
        by_time = set(_fired(steps, beats, sid, t, inclusive=True))
        frozen = {c for c in order[: k + 1] if c in beats and owner_step(c, steps) == sid}
        note = "" if by_time == frozen else ORDER_NOTE
        pairs.append(FramePair(cue, t, sid, before, Freeze(sid, cue=cue), note=note))
    return pairs


def last_state(steps: Steps, beats: dict[str, float]) -> Freeze | None:
    """The frozen state a section ends on: its last mounted step with every listed cue fired."""
    seq = mounts(steps, beats)
    if not seq:
        return None
    sid = seq[-1][0]
    fired = _fired(steps, beats, sid, math.inf, inclusive=True)
    return Freeze(sid, cue=fired[-1]) if fired else Freeze(sid)


def first_state(steps: Steps, beats: dict[str, float], fps: int) -> Freeze | None:
    """The frozen state a section opens on: its first step with the cues of its first frame fired."""
    seq = mounts(steps, beats)
    if not seq:
        return None
    sid = seq[0][0]
    fired = _fired(steps, beats, sid, 1.0 / fps, inclusive=False)
    if fired:
        return Freeze(sid, cue=fired[-1])
    return Freeze(sid, before=steps[sid][0]) if steps[sid] else Freeze(sid)


# ---- results ------------------------------------------------------------------------------


def _rel(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def cue_verdict(share: float, cfg: VerifyConfig) -> str:
    """NO CHANGE, THIN CHANGE?, or changed for a frozen share, whose control is 0 so its margin is the share."""
    if share < cfg.min_changed_percent or share < cfg.min_margin_percent:
        return NO_CHANGE
    return THIN_CHANGE if thin_change(share, share, cfg) else CHANGED


@dataclass
class CueEstimate:
    check: str  # SECTION:CUE
    cue_seconds: float
    step: str | None
    changed_percent: float | None
    verdict: str
    reason: str | None = None
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
            "step": self.step,
            "changed_percent": None if self.changed_percent is None else round(self.changed_percent, 2),
            "verdict": self.verdict,
            "reason": self.reason,
            "detail": self.detail or None,
            "note": self.note or None,
            "before": _rel(self.before, root),
            "after": _rel(self.after, root),
        }


@dataclass
class CarryEstimate:
    key: str
    changed_percent: float | None
    verdict: str
    reason: str | None = None
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
    beats: BeatsResult
    estimated: list[str]  # Section keys whose cue times come from estimated words.
    cues: list[CueEstimate] = field(default_factory=list)
    carries: list[CarryEstimate] = field(default_factory=list)
    frames: Path | None = None  # build/preflight, or None when no frame was rendered.
    root: Path | None = None  # The project root, which the table prints paths against.

    @property
    def placeholders(self) -> list[str]:
        return sorted({p for plan in self.takes for p in plan.segment.placeholders})

    @property
    def short(self) -> int:
        return sum(
            1
            for s in self.beats.sections
            if not s.skipped and s.min_seconds is not None and s.speech_end < s.min_seconds
        )

    def findings(self, *, allow_unknown: bool = False) -> Findings:
        """Certain: a placeholder a voiced run refuses, UNRESOLVED, UNKNOWN, NO CHANGE, POP AT CUT.

        Uncertain: speech shorter than min_seconds, and THIN CHANGE?.
        """
        own = Findings(
            certain=len(self.placeholders) + self.beats.unresolved + (0 if allow_unknown else self.beats.unknown),
            uncertain=self.short,
        )
        return own + count(c.verdict for c in self.cues) + count(k.verdict for k in self.carries)

    def to_dict(self, root: Path) -> dict[str, Any]:
        beats = self.beats.to_dict(root)
        beats.pop("beats_file", None)
        return {
            "voice": self.voice,
            "note": self.note,
            "placeholders": self.placeholders,
            "takes": [p.to_dict(self.narration) for p in self.takes],
            "totals": plan_totals(self.takes, self.narration),
            "beats": {**beats, "estimated_sections": self.estimated},
            "cues": [c.to_dict(root) for c in self.cues],
            "carries": [k.to_dict(root) for k in self.carries],
            "frames": _rel(self.frames, root),
        }


# ---- the stage ----------------------------------------------------------------------------


def planned_words(project: Project, plan: TakePlan) -> tuple[list[Word], float, bool]:
    """(words, length, estimated) a section will have after a voiced run, in seconds after the section starts."""
    seg = plan.segment
    key = seg.key
    lead = project.lead_seconds(key)
    manifest = project.manifest()
    entry = manifest.segments.get(key) if manifest is not None and not manifest.estimated else None
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
) -> PreflightResult:
    """Plan the takes, resolve the cues, and estimate every reveal and carried cut from frozen renders.

    `only` keeps these section numbers. With `frames` off, no browser starts and no file is written.
    Otherwise the frozen frames go to build/preflight, which is emptied first. Nothing else is written.
    """
    cfg = project.settings.narration
    _all, spoken = script_segments(project)
    wanted = set(only or ())

    def named(number: int) -> bool:
        return not only or number in wanted

    # A carried cut compares the previous section's last picture with this section's first, so a
    # previous section that `only` leaves out still gets its take and cues resolved. It is not reported.
    behind = {
        prev.number
        for prev, sec in zip(project.sections, project.sections[1:], strict=False)
        if only and sec.carries_previous and sec.number in wanted and prev.number not in wanted
    }
    model = model or project.voice.model or cfg.model
    plans, note = narration_plan(project, [s for s in spoken if named(s.index) or s.index in behind], model=model)
    takes: dict[str, tuple[list[Word], float]] = {}
    estimated: list[str] = []
    for plan in plans:
        words, length, guessed = planned_words(project, plan)
        takes[plan.segment.key] = (words, length)
        if guessed and named(plan.segment.index):
            estimated.append(plan.segment.key)
    all_specs = load_cues(project)
    specs = [s for s in all_specs if named(s.number)]
    unknown_ids = unknown_cue_ids(project, specs)
    beats, _anchors, rows, unresolved = resolve_sections(specs, takes, unknown_ids=unknown_ids, estimated=True)
    result = PreflightResult(
        voice={"provider": project.voice.provider, "model": model, "settings": project.voice.api_settings()},
        narration=cfg,
        takes=[plan for plan in plans if named(plan.segment.index)],
        note=note,
        beats=BeatsResult(
            beats=beats, sections=rows, unresolved=unresolved, estimated=bool(estimated), unknown=len(unknown_ids)
        ),
        estimated=estimated,
        root=project.root,
    )
    if frames:
        carried = beats
        extra = [s for s in all_specs if s.number in behind]
        if extra:
            extra_ids = unknown_cue_ids(project, extra)
            behind_beats, _a, _r, _u = resolve_sections(extra, takes, unknown_ids=extra_ids, estimated=True)
            carried = Beats({**behind_beats.sections, **beats.sections})
        result.cues, result.carries = frame_estimates(project, carried, only=only)
        result.frames = project.build / "preflight"
    return result


def freeze_url(project: Project, section: PageSection, freeze: Freeze) -> str:
    """The page URL of a frozen state, with the section's own params and the words the recorder would pass."""
    params = {k: v for k, v in section.params.items() if k not in ("beats", "t0", "step", "cue", "before")}
    words = words_query(project, section)
    if words and "words" not in params:
        params["words"] = words
    prev = prev_words_query(project, section)
    if prev and "prevwords" not in params:
        params["prevwords"] = prev
    return project.path(section.page).resolve().as_uri() + "?" + urlencode({**freeze.query(), **params})


def frame_estimates(
    project: Project, beats: Beats, *, only: list[int] | None = None
) -> tuple[list[CueEstimate], list[CarryEstimate]]:
    """Render the frozen frames into build/preflight and compare them for every cue and every carried cut."""
    vcfg = project.settings.verify
    video = project.settings.video
    out_dir = project.build / "preflight"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    skip = opted_out(project)
    size = {"level": vcfg.diff_level, "width": vcfg.probe_width, "height": vcfg.probe_height}
    wanted = [s for s in project.sections if not only or s.number in set(only)]
    cues: list[CueEstimate] = []
    carries: list[CarryEstimate] = []
    with chromium() as browser:
        page = browser.new_page(viewport={"width": video.width, "height": video.height})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(page_error_text(e)))
        catalogs: dict[str, list[dict[str, Any]] | None] = {}
        shots: dict[str, Path] = {}

        def scene_steps(sec: PageSection) -> tuple[Steps | None, str | None, str]:
            if sec.page not in catalogs:
                html = project.path(sec.page)
                if not html.exists():
                    catalogs[sec.page] = None
                else:
                    page.goto(html.resolve().as_uri())
                    await_ready(page)
                    catalogs[sec.page] = page.evaluate("() => (window.__decktalk && window.__decktalk.catalog) || null")
            catalog = catalogs[sec.page]
            if not catalog:
                return None, NO_CATALOG, f"{sec.page} is missing or has no runtime catalog"
            scene = next((c for c in catalog if str(c.get("scene")) == sec.scene), None)
            if scene is None:
                return None, NO_CATALOG, f"{sec.page} registers no scene {sec.scene}"
            if not isinstance(scene.get("cues"), dict):
                return None, RUNTIME_OUTDATED, f"{sec.page} has an older decktalk-runtime.js. Run `decktalk runtime`."
            return {sid: [str(c) for c in scene["cues"].get(sid, [])] for sid in scene["steps"]}, None, ""

        def shoot(sec: PageSection, freeze: Freeze) -> Path:
            url = freeze_url(project, sec, freeze)
            if url not in shots:
                target = out_dir / sec.key / f"{freeze.label}.png"
                screenshot(page, url, target, settle_ms=project.settings.record.shot_settle_ms)
                shots[url] = target
            return shots[url]

        for sec in wanted:
            if not isinstance(sec, PageSection) or not beats.sections.get(sec.key):
                continue
            section_beats = beats.sections[sec.key]
            steps, reason, detail = scene_steps(sec)
            if steps is None:
                for cue, t in sorted(section_beats.items(), key=lambda item: item[1]):
                    cues.append(CueEstimate(f"{sec.number}:{cue}", t, None, None, SKIPPED, reason, detail))
                continue
            for pair in plan_frames(steps, section_beats, video.fps):
                check = f"{sec.number}:{pair.cue}"
                if (sec.key, pair.cue) in skip:
                    detail = 'cues.json sets "verify": false'
                    cues.append(CueEstimate(check, pair.seconds, pair.step, None, SKIPPED, OPTED_OUT, detail))
                    continue
                if pair.reason is not None or pair.before is None or pair.after is None:
                    cues.append(CueEstimate(check, pair.seconds, pair.step, None, SKIPPED, pair.reason, pair.detail))
                    continue
                a, b = shoot(sec, pair.before), shoot(sec, pair.after)
                share = ffmpeg.changed_images_percent(a, b, **size)
                verdict = cue_verdict(share, vcfg)
                cues.append(
                    CueEstimate(check, pair.seconds, pair.step, share, verdict, note=pair.note, before=a, after=b)
                )

        for prev, sec in zip(project.sections, project.sections[1:], strict=False):
            if not sec.carries_previous or sec not in wanted:
                continue
            if not isinstance(sec, PageSection) or not isinstance(prev, PageSection):
                detail = "a clip has no frozen frame. `decktalk verify` checks this cut after assemble"
                carries.append(CarryEstimate(sec.key, None, SKIPPED, CLIP, detail))
                continue
            prev_steps, _r1, _d1 = scene_steps(prev)
            steps, _r2, _d2 = scene_steps(sec)
            last = last_state(prev_steps, beats.sections.get(prev.key, {})) if prev_steps else None
            first = first_state(steps, beats.sections.get(sec.key, {}), video.fps) if steps else None
            if last is None or first is None:
                detail = "a side of the cut has no resolved cue, or its page has no catalog"
                carries.append(CarryEstimate(sec.key, None, SKIPPED, NO_CUES, detail))
                continue
            a, b = shoot(prev, last), shoot(sec, first)
            share = ffmpeg.changed_images_percent(a, b, **size)
            verdict = OK if share <= vcfg.max_pop_percent else POP_AT_CUT
            carries.append(CarryEstimate(sec.key, share, verdict, last=a, first=b))
        for e in sorted(set(errors)):
            log.warning("[page] preflight  page error: %s", e)
    log.info(
        "[pre ] %d frozen frame(s) in %s",
        len(shots),
        out_dir.relative_to(project.root) if out_dir.is_relative_to(project.root) else out_dir,
    )
    return cues, carries
