"""The three checks that read the shape of the film rather than one cue: starts, cuts and seams.

A section start must show a real picture past the dip to black, the narration must be quiet in the
window before every cut, and a section that sets `seamless` must open on the picture the section
before it ended on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...artifacts import Timeline
from ...media import audio, frames
from ...model import Project
from ...model.document import frame_dip
from ...verdicts import Verdict


@dataclass
class StartCheck:
    key: str
    start: float
    probe_at: float
    yavg: float
    ymax: float
    ok: bool

    @property
    def verdict(self) -> Verdict:
        return Verdict.OK if self.ok else Verdict.BLACK

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "start": round(self.start, 3),
            "probe_at": round(self.probe_at, 3),
            "yavg": round(self.yavg, 2),
            "ymax": round(self.ymax, 2),
            "verdict": self.verdict,
        }


@dataclass
class CutCheck:
    """The audio just before a cut. Speech still sounding there means the cut is early."""

    key: str
    cut_at: float
    rms_db: float
    ok: bool

    @property
    def verdict(self) -> Verdict:
        return Verdict.QUIET if self.ok else Verdict.SPEECH_AT_CUT

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "cut_at": round(self.cut_at, 3),
            "rms_db": round(self.rms_db, 2),
            "verdict": self.verdict,
        }


@dataclass
class SeamCheck:
    """The cut into a section that sets seamless. A picture that jumps there is a pop."""

    key: str
    cut_at: float
    last_at: float  # The previous section's last frame before any dip, in the final mp4.
    first_at: float  # This section's first frame after any dip, in the final mp4.
    changed_percent: float
    ok: bool

    @property
    def verdict(self) -> Verdict:
        return Verdict.OK if self.ok else Verdict.POP_AT_CUT

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "cut_at": round(self.cut_at, 3),
            "last_at": round(self.last_at, 3),
            "first_at": round(self.first_at, 3),
            "changed_percent": round(self.changed_percent, 2),
            "verdict": self.verdict,
        }


def seam_checks(project: Project, final: Path, starts: dict[str, float]) -> list[SeamCheck]:
    """One row per assembled section that sets seamless and follows an assembled section.

    The frames compared sit outside any dip, so a fade to black is never taken for a pop. Each
    time sits half a frame before the frame it names, because a frame is the first at or after it.
    """
    cfg = project.settings.verify
    fps = project.settings.video.fps
    flags = project.document.fade_flags
    dip = frame_dip(project.transition.dip_seconds, fps)
    rows: list[SeamCheck] = []
    for prev, sec in zip(project.sections, project.sections[1:], strict=False):
        if not sec.seamless or sec.key not in starts or prev.key not in starts:
            continue
        cut = starts[sec.key]
        last = cut - (dip if flags.get(prev.key, (False, False))[1] else 0.0) - 1.5 / fps
        first = cut + (dip if flags.get(sec.key, (False, False))[0] else 0.0) - 0.5 / fps
        first = max(first, cut)
        share = frames.changed_pixels_percent(
            final, last, first, level=cfg.diff_level, width=cfg.probe_width, height=cfg.probe_height
        )
        rows.append(SeamCheck(sec.key, cut, last, first, share, share <= cfg.max_pop_percent))
    return rows


def start_checks(project: Project, final: Path, starts: dict[str, float]) -> list[StartCheck]:
    """One row per assembled section: the frame past the dip shows a real picture, not black."""
    cfg = project.settings.verify
    rows: list[StartCheck] = []
    for key, t in starts.items():
        probe = t + cfg.after_dip_seconds
        yavg, ymax = frames.luma_at(final, probe)
        rows.append(StartCheck(key=key, start=t, probe_at=probe, yavg=yavg, ymax=ymax, ok=ymax > cfg.visible_ymax))
    return rows


def cut_checks(project: Project, timeline: Timeline | None, starts: dict[str, float]) -> list[CutCheck]:
    """One row per spoken section: the narration is quiet in the window before the cut out of it.

    The check listens to the narration track alone, so music or an effect at a boundary does not
    count as speech, and a clip, which carries its own audio, is exempt.
    """
    cfg = project.settings.verify
    narration = project.narration_dir / (timeline.narration if timeline else "narration.mp3")
    if timeline is None or not narration.exists():
        return []
    rows: list[CutCheck] = []
    for key, sec in timeline.sections.items():
        if key not in starts:
            continue
        window = min(cfg.cut_window_seconds, sec.duration)
        level = audio.rms_db(narration, max(0.0, sec.end - window), window)
        cut = starts[key] + sec.duration
        rows.append(CutCheck(key=key, cut_at=round(cut, 3), rms_db=level, ok=level <= cfg.cut_max_db))
    return rows
