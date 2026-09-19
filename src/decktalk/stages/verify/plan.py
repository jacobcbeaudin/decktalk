"""The arithmetic behind a cue check, with no ffmpeg, no file and no project.

Where the reference frame sits, which probes after the cue are worth measuring, which of them a
neighbouring cue would spoil, where the control spans fall, and how a series of changed shares names
the frame a reveal began on. Every function here takes numbers and gives numbers, so the rules a
reveal is judged by can be read and tested without rendering anything.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...artifacts import CueTimes
from ...settings import VerifyConfig
from ...verdicts import SkipReason, Verdict


def round_or_none(value: float | None, digits: int) -> float | None:
    """A measured number rounded for JSON, or None when the row measured nothing."""
    return None if value is None else round(value, digits)


@dataclass
class CueCheck:
    check: str
    cue_seconds: float | None
    final_seconds: float | None
    changed_percent: float | None
    control_percent: float | None
    ok: bool
    note: str = ""
    offset_ms: int | None = None  # Where the first changed frame sits relative to the cue.
    av_ms: int | None = None  # The offset minus the click's distance from the cued word's start, with no voice.
    verdict: Verdict | None = None  # Derived from ok when it is not given.
    reason: SkipReason | None = None  # Why a row was skipped, or NO_CLICK on a measured row with no a/v value.

    def __post_init__(self) -> None:
        if self.verdict is None:
            missed = Verdict.OFF_CUE if self.offset_ms is not None else Verdict.NO_CHANGE
            self.verdict = Verdict.CHANGED if self.ok else missed

    @property
    def section(self) -> int:
        return int(self.check.split(":", 1)[0])

    @property
    def cue(self) -> str:
        return self.check.split(":", 1)[1]

    @property
    def skipped(self) -> bool:
        return self.verdict is Verdict.SKIPPED

    def to_dict(self, where: str | None = None) -> dict[str, Any]:
        assert self.verdict is not None  # __post_init__ derives it from ok when it is not given.
        return {
            "section": self.section,
            "cue": self.cue,
            "where": where,
            "cue_seconds": round_or_none(self.cue_seconds, 3),
            "final_seconds": round_or_none(self.final_seconds, 3),
            "changed_percent": round_or_none(self.changed_percent, 2),
            "control_percent": round_or_none(self.control_percent, 2),
            "offset_ms": self.offset_ms,
            "av_ms": self.av_ms,
            "verdict": self.verdict.to_dict(),
            "reason": None if self.reason is None else self.reason.value,
            "detail": self.note or None,
        }


def skipped(check: str, reason: SkipReason, detail: str) -> CueCheck:
    """A row that measured nothing. The table prints its note, so the note leads with the verdict and reason."""
    return CueCheck(
        check,
        None,
        None,
        None,
        None,
        True,
        f"{Verdict.SKIPPED.label} {reason.value}: {detail}",
        verdict=Verdict.SKIPPED,
        reason=reason,
    )


def reference_time(
    sec_start: float, cue_t: float, fade_in: bool, dip: float, cfg: VerifyConfig, fps: int
) -> float | None:
    """Where the reference frame for a cue sits in the final file, or None when no frame fits.

    The reference wants to sit reference_lead_seconds before the cue, or earlier when a reveal that lands
    max_offset_frames early could otherwise already show in it, and the frame it names is the
    first frame at or after that time. It may not sit inside the
    section's fade-in, where the picture is still coming up from black, and it must sit at
    least one frame before the cue. A cue at the very start of a section, or inside its
    fade-in, leaves no such frame.
    """
    floor = sec_start + (dip if fade_in else 0.0)
    latest = sec_start + cue_t - 1.0 / fps
    ref = max(floor, sec_start + cue_t - cue_reach(cfg, fps))
    if ref > latest + 1e-6:
        return None
    return round(ref, 4)


def cue_reach(cfg: VerifyConfig, fps: int) -> float:
    """The reference lead: how far from its cue time a reveal can show, 0.14 s at the defaults.

    A reveal may land up to max_offset_frames early and still pass, so the reference must sit
    before that whole window. Otherwise the early reveal is already in the reference frame, and
    the scan measures the change only when the reveal settles, frames too late.
    """
    return max(cfg.reference_lead_seconds, (cfg.max_offset_frames + 1.5) / fps)


def control_spans(before: float, span: float, floor: float) -> list[tuple[float, float]]:
    """The measured control spans: two back-to-back spans of `span` that end at the reference, past the floor."""
    spans = []
    for n in (1, 2):
        ctl_b = before - (n - 1) * span
        ctl_a = ctl_b - span
        if ctl_a >= floor:
            spans.append((ctl_a, ctl_b))
    return spans


def probe_plan(
    cue_at: float,
    before: float,
    floor: float,
    sec_end: float,
    neighbors: Iterable[float],
    cfg: VerifyConfig,
    fps: int,
) -> tuple[list[float], bool]:
    """The probe delays for the cue at `cue_at`, and whether any was fitted between neighboring cues.

    Every time is in the final file. A neighbor is another cue of the section more than the
    reference lead away, and its reveal can show anywhere within the reference lead of its time.
    A closer cue is part of the same reveal. A probe is spoiled when a neighbor's reveal can
    fall inside its span, where it would count as this cue's change, or inside every measured
    control span, where it would count as motion. A probe of probe_delays that nothing spoils
    is kept exactly, so a cue with room around it is measured at the delay the settings name. A
    spoiled probe becomes
    the longest shorter delay that nothing spoils, down to max_offset_frames + 1 frames, so a
    reveal at the late limit still shows. When no probe can be fitted, probe_delays are used.
    """
    eps = 1e-6
    reach = cue_reach(cfg, fps)
    others = [n for n in neighbors if abs(n - cue_at) > reach + eps]

    def holds(a: float, b: float) -> bool:
        return any(n + reach > a + eps and n - reach < b - eps for n in others)

    def spoiled(delay: float) -> bool:
        after = cue_at + delay
        spans = control_spans(before, after - before, floor)
        return holds(before, after) or (bool(spans) and all(holds(a, b) for a, b in spans))

    configured = [d for d in cfg.probe_delays if cue_at + d <= sec_end - 0.05]
    if not any(spoiled(d) for d in configured):
        return configured, False
    shortest = (cfg.max_offset_frames + 1) / fps
    delays: list[float] = []
    for d in configured:
        if spoiled(d):
            # Where a later reveal can begin, where the nearer control span clears an earlier
            # reveal, and every frame in between.
            bounds = {d - k / fps for k in range(1, int(d * fps) + 1)}
            bounds |= {n - reach - cue_at for n in others} | {2 * before - n - reach - cue_at for n in others}
            fit = next((x for x in sorted(bounds, reverse=True) if shortest - eps <= x < d and not spoiled(x)), None)
        else:
            fit = d
        if fit is not None and round(fit, 4) not in delays:
            delays.append(round(fit, 4))
    return (sorted(delays), True) if delays else (configured, False)


def thin_change(changed: float, margin: float, cfg: VerifyConfig) -> bool:
    """Whether a cue that passed the change test passed by less than thin_change_factor times either floor."""
    factor = cfg.thin_change_factor
    # The small allowance keeps a share of exactly 3 times a floor of 0.1 from reading thin by rounding.
    return changed < factor * cfg.min_changed_percent - 1e-9 or margin < factor * cfg.min_margin_percent - 1e-9


def default_checks(cue_times: CueTimes, only: list[int] | None = None) -> list[str]:
    """Every resolved cue as SECTION:CUE, in section order and then cue time."""
    checks: list[str] = []
    for key in sorted(cue_times.sections):
        if only and int(key) not in only:
            continue
        for cue, _t in sorted(cue_times.times(key).items(), key=lambda item: item[1]):
            checks.append(f"{int(key)}:{cue}")
    return checks


def onset_offset_ms(
    series: list[tuple[float, float]],
    before: float,
    cue_at: float,
    onset: float,
    tolerance: float = 0.0,
    blocks: dict[float, float] | None = None,
) -> int | None:
    """Where the reveal begins, in milliseconds from the cue, from the changed share per frame.

    A reveal is a step: between two consecutive frames the changed share jumps by at least
    `onset`. Motion that is always there, such as a camera push or a curve still drawing,
    is a slope that grows a little every frame and never jumps. The first jump after the
    reference frame is the onset, and it may sit before the cue. The series starts on the
    reference itself, so the first frame after it is judged against a share of zero. When
    nothing jumps, the first frame whose share exceeds the pre-cue floor is used instead,
    which catches a reveal that grows slowly, such as text typing in.

    `blocks`, when given, is the changed share of the same frames scaled down to whole blocks,
    by time. A frame whose blocks did not change is encoder ringing, and it is never the onset.
    """

    def changed(t: float) -> bool:
        return blocks is None or blocks.get(t, 0.0) > 0.0

    prev: float | None = None
    for t, pct in series:
        if prev is not None and t > before and pct - prev >= onset and changed(t):
            return int(round((t - cue_at) * 1000))
        prev = pct
    floor = max((pct for t, pct in series if t <= cue_at - tolerance), default=0.0)
    threshold = max(onset, floor)
    for t, pct in series:
        if t > before and pct > threshold and changed(t):
            return int(round((t - cue_at) * 1000))
    return None
