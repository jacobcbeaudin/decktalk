"""Stage 6: checks on the assembled mp4.

starts   every section opens on a real frame: past the dip-to-black, YMAX above
         visible_ymax means content is on screen.
cues     for each SECTION:CUE, the picture changes across the cue. The reference frame
         sits lead_seconds before the cue. For each delay in probe_delays, the share of
         pixels that change by more than diff_level between the reference and the probe
         is compared with the same measure over an equal span that ends at the reference,
         which captures anything else in motion, such as a camera push. A cue lands when
         the best probe changes at least min_changed_percent of the pixels and exceeds
         its control by min_margin_percent. Everything stays inside the section.

Section starts are the cumulative lengths of build/out/NN-section.mp4, the same
arithmetic the assembler uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..errors import ConfigError, MissingInputError
from ..media import ffmpeg
from ..project import Project

log = logging.getLogger(__name__)


def section_starts(project: Project) -> tuple[dict[str, float], float]:
    starts: dict[str, float] = {}
    t = 0.0
    for f in sorted(project.out_dir.glob("[0-9][0-9]-section.mp4")):
        starts[f.name[:2]] = t
        t += ffmpeg.probe_duration(f)
    return starts, t


@dataclass
class StartCheck:
    key: str
    start: float
    probe_at: float
    yavg: float
    ymax: float
    ok: bool


@dataclass
class CueCheck:
    check: str
    cue_seconds: float | None
    final_seconds: float | None
    changed_percent: float | None
    control_percent: float | None
    ok: bool
    note: str = ""


@dataclass
class VerifyResult:
    total_seconds: float
    starts: list[StartCheck] = field(default_factory=list)
    cues: list[CueCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.starts) and all(c.ok for c in self.cues)

    @property
    def black_starts(self) -> int:
        return sum(not s.ok for s in self.starts)


def verify(project: Project, checks: list[str] | None = None) -> VerifyResult:
    cfg = project.settings.verify
    final = project.final
    starts, total = section_starts(project)
    if not starts or not final.exists():
        raise MissingInputError("need build/out/NN-section.mp4 files and the final mp4; run `decktalk assemble` first")
    result = VerifyResult(total_seconds=total)
    for key, t in starts.items():
        probe = t + cfg.after_dip_seconds
        yavg, ymax = ffmpeg.luma_at(final, probe)
        result.starts.append(
            StartCheck(key=key, start=t, probe_at=probe, yavg=yavg, ymax=ymax, ok=ymax > cfg.visible_ymax)
        )
    if not checks:
        return result
    beats = project.beats()
    probe_w, probe_h = cfg.probe_width, cfg.probe_height
    for check in checks:
        if ":" not in check:
            raise ConfigError(f"malformed check {check!r}; want SECTION:CUE, e.g. 3:3.1draw")
        sec, cue = check.split(":", 1)
        key = f"{int(sec):02d}"
        cue_t = beats.get(key, cue)
        if cue_t is None or key not in starts:
            result.cues.append(
                CueCheck(check, None, None, None, None, False, "missing: cue unresolved or section not assembled")
            )
            continue
        sec_start = starts[key]
        floor = sec_start + cfg.after_dip_seconds
        sec_end = next((t for k, t in starts.items() if k > key), total)
        # The reference frame sits just before the cue fires. Each probe after the cue is
        # compared with it, and a control span of the same length that ends at the reference
        # measures whatever else is moving (a camera push, an earlier reveal still settling).
        before = max(floor, sec_start + cue_t - cfg.lead_seconds)
        best: tuple[float, float, float] | None = None  # (margin, changed, control)
        for delay in cfg.probe_delays:
            after = sec_start + cue_t + delay
            if after > sec_end - 0.05:
                continue
            span = after - before
            chg = ffmpeg.changed_pixels_percent(
                final, before, after, level=cfg.diff_level, width=probe_w, height=probe_h
            )
            ctl_a = before - span
            ctl = (
                ffmpeg.changed_pixels_percent(final, ctl_a, before, level=cfg.diff_level, width=probe_w, height=probe_h)
                if ctl_a >= floor
                else 0.0
            )
            margin = chg - ctl
            if best is None or margin > best[0]:
                best = (margin, chg, ctl)
        if best is None:
            result.cues.append(
                CueCheck(check, cue_t, None, None, None, False, "cue too close to the section end to probe")
            )
            continue
        margin, chg, ctl = best
        landed = chg >= cfg.min_changed_percent and margin >= cfg.min_margin_percent
        result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, landed))
    return result
