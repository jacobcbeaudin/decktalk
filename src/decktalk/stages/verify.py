"""Stage 6: checks on the assembled mp4.

starts   every section opens on a real frame: past the dip-to-black, YMAX above
         visible_ymax means content is on screen.
cues     for each SECTION:CUE, the picture changes across the cue: the share of pixels
         that change by more than diff_level between (t - window) and t, where t is the
         section start plus the cue plus after_cue_seconds, compared with the same
         measure over a control window just before. Both windows are clamped inside
         the section. "changed" when the share is at least min_changed_percent above
         the control.

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
        tf = sec_start + cue_t + cfg.after_cue_seconds
        before = max(sec_start + cfg.after_dip_seconds, tf - cfg.window_seconds)
        chg = ffmpeg.changed_pixels_percent(final, before, tf, level=cfg.diff_level, width=probe_w, height=probe_h)
        ctl_a = max(sec_start + cfg.after_dip_seconds, before - cfg.window_seconds)
        ctl = (
            ffmpeg.changed_pixels_percent(final, ctl_a, before, level=cfg.diff_level, width=probe_w, height=probe_h)
            if before - ctl_a >= 0.3
            else 0.0
        )
        landed = chg >= cfg.min_changed_percent and chg - ctl >= cfg.min_changed_percent
        result.cues.append(CueCheck(check, cue_t, tf, chg, ctl, landed))
    return result
