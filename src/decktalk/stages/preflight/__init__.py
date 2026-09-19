"""Preflight: what a voiced build would spend and show, with no credits and no recording.

    freeze.py   which frozen states each cue is measured between
    scan.py     rendering those states and comparing them

takes    what `narrate` would voice and what it already holds, as `narrate --dry-run` plans it, with
         the characters each section sends and what they cost.
cues     every cue of cues.json resolved against the words each section will have: a cached take's
         own words, or estimated words at silent_words_per_minute for a section that would be
         voiced. The notes and findings are those of `decktalk align`, the repeated-phrase warning
         included. cue-times.json is not written.
frames   for each cue, the page frozen just before the cue fires and frozen at the cue, in
         build/preflight. They are compared as `verify` compares frames, so the share estimates what
         verify's probe reads once the reveal has settled. The share reads NO CHANGE below
         min_changed_percent or min_margin_percent, THIN CHANGE? below thin_change_factor times
         either floor, and changed otherwise.
seams    for each page section that sets seamless after a page section, the previous section's last
         frozen state against this section's first one. A share above max_pop_percent reads POP AT CUT.

A skipped cue row carries one of these reasons:

    SkipReason.AT_SECTION_START    the cue fires within the first frame, so no frame comes before it
    SkipReason.NO_SLIDE            no slide of the scene owns the cue, or the slide that owns it by
                                   its id prefix declares no element that waits for it
    SkipReason.OPTED_OUT           cues.json sets "verify": false on the cue

A seam row is skipped as SkipReason.NO_CUES when a side has no resolved cue, and as CLIP when a side is a clip.

A page that exposes no runtime catalog is a certain finding rather than a skipped row, because a page
the recorder cannot drive plays nothing at all, and the page's own error is printed beside it. The
warnings the runtime records while the frozen frames are drawn are findings too, so a KaTeX value
that cannot be parsed, or KaTeX that never arrives, is caught before a single second is voiced.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...artifacts import CueTimes, Word
from ...jsonio import relative
from ...media import audio
from ...model import Project
from ...settings import NarrationConfig
from ...verdicts import Finding, Findings, Verdict
from ..align import AlignResult, resolve_sections, uncued_elements, unknown_cue_ids
from ..narrate import (
    TakePlan,
    estimated_words,
    is_cached,
    placed,
    plan_totals,
    take_name,
    voiced_plan,
    words_name,
)
from ..record.checks import katex_verdicts
from .freeze import FramePair, Freeze, Slides, plan_frames
from .scan import CueEstimate, SeamEstimate, frame_estimates, freeze_url, rel_to

log = logging.getLogger(__name__)

__all__ = [
    "CueEstimate",
    "FramePair",
    "Freeze",
    "PreflightResult",
    "SeamEstimate",
    "Slides",
    "frame_estimates",
    "freeze_url",
    "plan_frames",
    "preflight",
]


@dataclass
class PreflightResult:
    voice: dict[str, Any]
    narration: NarrationConfig
    takes: list[TakePlan]
    note: str | None
    align: AlignResult
    estimated: list[str]  # Section keys whose cue times come from estimated words.
    rate: float  # [voice] price_per_1000_characters, which prices the take plan.
    cues: list[CueEstimate] = field(default_factory=list)
    seams: list[SeamEstimate] = field(default_factory=list)
    frames: Path | None = None  # build/preflight, or None when no frame was rendered.
    page_warnings: list[str] = field(default_factory=list)  # what the runtime could not honour while freezing
    page_errors: list[tuple[str, str]] = field(default_factory=list)  # (page, message) for each throw
    static: list[Finding] = field(default_factory=list)  # what the measured catalog says without a picture
    root: Path | None = None  # The project root, which the table prints paths against.
    script: str = ""  # The script every placeholder row is about, relative to the project root.
    allow_unknown_cues: bool = False  # The run was told to carry on past an unknown cue id.

    @property
    def placeholders(self) -> list[Finding]:
        """One row per open placeholder in a section, because a voiced run refuses each of them.

        `narrate` stops on the first script that still holds one, and the rehearsal reports them all
        instead, so an author fills every one before a single second is paid for. The row names the
        script rather than the take, because the script is where the placeholder is written.
        """
        return [
            Finding(
                detail=f"[{name}] is still open, so a voiced run would read it out",
                verdict=Verdict.PLACEHOLDER,
                section=plan.segment.index,
                where=self.script,
            )
            for plan in self.takes
            for name in plan.segment.placeholders
        ]

    @property
    def short(self) -> int:
        return sum(
            1
            for s in self.align.sections
            if not s.skipped and s.min_seconds is not None and s.speech_end_seconds < s.min_seconds
        )

    @property
    def findings(self) -> Findings:
        """Certain: a placeholder a voiced run refuses, UNRESOLVED, UNKNOWN CUE, NO CHANGE, POP AT CUT.

        Uncertain: speech shorter than min_seconds, and THIN CHANGE?. A page that exposes no runtime
        catalog, a page that threw, a KaTeX value that cannot be parsed and KaTeX that never loads
        are certain too.
        """
        unknown = 0 if self.allow_unknown_cues else self.align.unknown
        own = Findings(certain=self.align.unresolved + unknown, uncertain=self.short)
        return (
            own
            + Findings.of(row.verdict for row in self.placeholders)
            + Findings.of(c.verdict for c in self.cues)
            + Findings.of(k.verdict for k in self.seams)
            + Findings.of(katex_verdicts(self.page_warnings))
            + Findings.of(Verdict.PAGE_ERROR for _ in self.page_errors)
            + Findings.of(row.verdict for row in self.static)
        )

    @property
    def error_rows(self) -> list[Finding]:
        """One judged row per page error, so a reader that dispatches on findings.items[] sees it."""
        return [Finding(verdict=Verdict.PAGE_ERROR, where=where, detail=message) for where, message in self.page_errors]

    def to_dict(self, root: Path) -> dict[str, Any]:
        cue_times = self.align.to_dict(root)
        cue_times.pop("cue_times_file", None)
        return {
            "voice": self.voice,
            "note": self.note,
            "placeholders": [row.to_dict() for row in self.placeholders],
            "takes": [p.to_dict(self.narration) for p in self.takes],
            "totals": plan_totals(self.takes, self.narration, self.rate),
            "cue_times": {**cue_times, "estimated_sections": self.estimated},
            "cues": [c.to_dict(root) for c in self.cues],
            "seams": [k.to_dict(root) for k in self.seams],
            "warnings": list(self.page_warnings),
            "page_errors": [row.to_dict() for row in self.error_rows],
            "page_scan": [row.to_dict() for row in self.static],
            "frames": rel_to(self.frames, root),
        }


def planned_words(project: Project, plan: TakePlan) -> tuple[list[Word], float, bool]:
    """(words, length, estimated) a section will have after a voiced run, in seconds after the section starts.

    The length is placed by the one rule every take is placed by: the section's lead, the take to
    where its sound ends, and the section's tail.
    """
    seg = plan.segment
    key = seg.key
    lead, tail = project.lead_seconds(key), project.tail_seconds(key)
    takes = project.takes()
    row = takes.sections.get(key) if takes is not None else None
    paid = row if row is not None and row.voiced else None
    if plan.cached and plan.digest is not None and is_cached(plan.digest, project.takes_dir):
        # The take of this exact text is on disk, so the cues land on the words it already carries.
        # A plan can read cached because an earlier section of the same run writes that take, and
        # nothing has written it yet, so the file itself is what is asked rather than the status.
        words = project.section_words(key, words_name(plan.digest))
        if paid is not None and paid.hash == plan.digest:
            return words, placed(project, key, paid).span_seconds, False
        end = audio.sound_end(project.takes_dir / take_name(plan.digest))
        return words, round(lead + end + tail, 3), False
    if plan.unchecked and paid is not None and paid.spoken == seg.spoken:
        # The voice is not set up, but the take was voiced from this exact text.
        return project.section_words(key, paid.words_file), placed(project, key, paid).span_seconds, False
    length = seg.silent_seconds(project.settings.narration)
    words = [Word(w.word, round(w.start + lead, 3), round(w.end + lead, 3)) for w in estimated_words(seg, length)]
    return words, round(lead + length + tail, 3), True


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
    planned = [s for s in spoken if named(s.index) or s.index in behind]
    plans, note = voiced_plan(project, planned, model=model)
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
    uncued_ids = [row for row in uncued_elements(project, all_specs) if named(int(row[0]))]
    clips = {f"{number:02d}" for number in project.clip_numbers}
    cues_file = relative(project.cues, project.root)
    cue_times, rows, unresolved = resolve_sections(
        specs,
        take_words,
        unknown_ids=unknown_ids,
        uncued_ids=uncued_ids,
        clips=clips,
        estimated=True,
        cues_file=cues_file,
    )
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
            uncued=len(uncued_ids),
        ),
        estimated=estimated,
        rate=project.voice.price_per_1000_characters,
        root=project.root,
        script=relative(project.script, project.root),
        allow_unknown_cues=allow_unknown_cues,
    )
    if frames:
        carried = cue_times
        extra = [s for s in all_specs if s.number in behind]
        if extra:
            extra_ids = unknown_cue_ids(project, extra)
            behind_cue_times, _rows, _unresolved = resolve_sections(
                extra, take_words, unknown_ids=extra_ids, uncued_ids=[], clips=clips, estimated=True,
                cues_file=cues_file,
            )  # fmt: skip
            carried = CueTimes({**behind_cue_times.sections, **cue_times.sections})
        (
            result.cues,
            result.seams,
            result.page_warnings,
            result.page_errors,
            result.static,
        ) = frame_estimates(project, carried, only=only)
        result.frames = project.preflight_dir
    return result
