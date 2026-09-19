"""The three checks that read the shape of the film rather than one cue: starts, cuts and seams.

A section start must show a real picture past the dip to black, the narration must be quiet in the
window before each section's narration ends, and a section that sets `seamless` must open on the picture the section
before it ended on.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...artifacts import Takes
from ...media import audio, frames
from ...model import PageSection, Project
from ...model.document import frame_dip
from ...model.timeline import narration_offsets
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

    @property
    def detail(self) -> str | None:
        """The one sentence a judged row carries, with the measured number a reader needs in it."""
        if self.ok:
            return None
        return (
            f"section {self.key} starts at {self.start:.3f}s and the frame read at {self.probe_at:.3f}s "
            f"is dark, with an average luma of {self.yavg:.1f} and a brightest luma of {self.ymax:.1f}."
        )

    def to_dict(self, where: str | None = None) -> dict[str, Any]:
        return {
            "key": self.key,
            "where": where,
            "start": round(self.start, 3),
            "probe_at": round(self.probe_at, 3),
            "yavg": round(self.yavg, 2),
            "ymax": round(self.ymax, 2),
            "verdict": self.verdict.to_dict(),
            "detail": self.detail,
        }


@dataclass
class CutCheck:
    """The narration just before a section's narration ends. Sound still there means a word is cut off."""

    key: str
    cut_at: float  # Where the section's narration ends in the final mp4.
    rms_db: float
    ok: bool
    into: str | None = None  # The section the film cuts to there, or None when the film ends there.
    held: bool = False  # The section holds its last frame after its narration ends, so the film cuts later.

    @property
    def verdict(self) -> Verdict:
        return Verdict.QUIET if self.ok else Verdict.SPEECH_AT_CUT

    @property
    def detail(self) -> str | None:
        """The one sentence a judged row carries, with the measured number a reader needs in it."""
        if self.ok:
            return None
        if self.held:
            boundary = "its hold"
        elif self.into is not None:
            boundary = f"the cut into section {self.into}"
        else:
            boundary = "the end of the film"
        return (
            f"section {self.key}'s narration still sounds at {self.rms_db:.1f} dBFS just before {boundary} "
            f"at {self.cut_at:.3f}s, so a word is cut off."
        )

    def to_dict(self, where: str | None = None) -> dict[str, Any]:
        return {
            "key": self.key,
            "where": where,
            "cut_at": round(self.cut_at, 3),
            "rms_db": round(self.rms_db, 2),
            "verdict": self.verdict.to_dict(),
            "detail": self.detail,
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

    @property
    def detail(self) -> str | None:
        """The one sentence a judged row carries, with the measured number a reader needs in it."""
        if self.ok:
            return None
        return (
            f"section {self.key} declares seamless and {self.changed_percent:.2f} percent of the "
            f"picture changes across its cut at {self.cut_at:.3f}s, so the join shows."
        )

    def to_dict(self, where: str | None = None) -> dict[str, Any]:
        return {
            "key": self.key,
            "where": where,
            "cut_at": round(self.cut_at, 3),
            "last_at": round(self.last_at, 3),
            "first_at": round(self.first_at, 3),
            "changed_percent": round(self.changed_percent, 2),
            "verdict": self.verdict.to_dict(),
            "detail": self.detail,
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


def cut_checks(project: Project, takes: Takes | None, starts: dict[str, float]) -> list[CutCheck]:
    """One row per spoken section: the narration is quiet in the window before the section's narration ends.

    The window is the last `cut_window_seconds` of the section's span in narration.mp3, and those are
    exactly the samples the viewer hears before `cut_at`, because the section's narration run places
    them there in the final mp4 as the mix does. The picture cuts within half a frame of it, or after
    the section's hold.

    The check listens to the narration track alone, so music or an effect at a boundary does not
    count as speech, and a clip, which carries its own audio, is exempt.
    """
    cfg = project.settings.verify
    narration = project.narration_path
    if takes is None or not narration.exists():
        return []
    played = [sec for sec in project.sections if sec.key in starts]
    offsets = narration_offsets(played, takes, starts)
    following = {sec.key: nxt.key for sec, nxt in zip(played, played[1:], strict=False)}
    rows: list[CutCheck] = []
    for sec in played:
        span, end = takes.span(sec.key), takes.end(sec.key)
        if span is None or end is None:
            continue
        window = min(cfg.cut_window_seconds, span)
        level = audio.rms_db(narration, max(0.0, end - window), window)
        rows.append(
            CutCheck(
                key=sec.key,
                cut_at=round(offsets[sec.key] + end, 3),
                rms_db=level,
                ok=level <= cfg.cut_max_db,
                into=following.get(sec.key),
                held=isinstance(sec, PageSection) and sec.hold_seconds > 0,
            )
        )
    return rows
