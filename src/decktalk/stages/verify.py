"""Stage 6: checks on the assembled mp4.

starts   every section opens on a real frame: past the dip-to-black, YMAX above
         visible_ymax means content is on screen.
cuts     the audio in the last cut_window_seconds before every cut is quieter than
         cut_max_db, so no cut lands on speech.
seams    for each section that sets seamless, the last frame of the previous section
         before any dip and the first frame of this section after any dip are compared, at
         diff_level and probe_width by probe_height. A changed share above max_pop_percent is a
         visible pop, and the row reads POP AT CUT.
cues     for each SECTION:CUE, the picture changes across the cue. With no list, every cue
         in cue-times.json is checked, in section order and then cue time, except the cues
         that cues.json marks "verify": false. The reference frame is the first frame at
         or after reference_lead_seconds before the cue, but never inside the section's fade-in and
         always at least one frame before the cue. For each delay in probe_delays, the
         share of pixels that change by more than diff_level between the reference and the
         probe is compared with a control. The control is the smaller share of two spans
         that each last as long as the probe's span and end at the reference, one after
         the other, and each span compares only its first and its last frame. The control
         captures anything else in motion, such as a camera push. A cue lands when the
         best probe changes at least min_changed_percent of the pixels and exceeds its
         control by min_margin_percent. Everything stays inside the section. A cue that lands
         with a changed share or a margin below thin_change_factor times its floor reads
         THIN CHANGE?, an uncertain finding: a slightly smaller reveal would fail.
close    another cue of the section more than the reference lead from this one is a
         neighbor, and its reveal can show anywhere within the reference lead of its time. A
         probe is spoiled when a neighbor's reveal can fall inside its span from the reference,
         or inside every control span that is measured. A probe that nothing spoils is used as
         it is, so a well-spaced cue is measured exactly as above. A spoiled probe is replaced
         by the longest shorter delay that nothing spoils, down to max_offset_frames + 1 frames,
         and its control spans shrink with it. When no probe fits, probe_delays are used as they
         are.
offset   once a cue lands, every frame from the reference to the passing probe is
         compared with the reference at onset_diff_level, which gives each frame's changed
         share. A frame can be the onset only when a copy scaled to block_width by
         block_height also changed there, so the encoder's ringing just before a change,
         which averages out over a block, is never taken for the reveal. A reveal is
         abrupt, so the first frame whose share rises by at least onset_percent over the
         frame before it marks where the visual began to appear,
         even when that frame sits before the cue. Motion that is always there, such as a
         camera push or a curve still drawing, grows a little every frame and does not
         jump. When no frame jumps, the first frame whose share exceeds both onset_percent
         and every share more than max_offset_frames and a half frames before the cue is
         used instead, which catches a reveal that grows slowly, such as text typing in.
         The frame's distance from the cue is reported in milliseconds, and a cue fails
         when the distance exceeds max_offset_frames.
a/v      after a build without voice, the loudest sample within click_search_seconds of the
         cued word's start, and inside the cue's section, is taken as the click. The a/v
         value is the offset minus the click's distance from the cued word's start, and a
         cue also fails when that value exceeds max_av_frames.

Cue verdicts are changed, THIN CHANGE?, OFF CUE, NO CHANGE, UNRESOLVED, and skipped. A skipped row is
never a failure, and its reason says why nothing was measured:

    REFERENCE_CLAMPED      no reference frame fits after the fade-in and before the cue
    SECTION_NOT_ASSEMBLED  the section has no sections/NN.mp4
    TOO_CLOSE_TO_END       every probe would fall past the end of the section
    OPTED_OUT              cues.json sets "verify": false on the cue

A measured row after a build without voice carries the reason NO_CLICK when no click was found
near the cued word, so its a/v value is empty.

Section starts are the cumulative lengths of build/sections/NN.mp4 for the sections in
decktalk.toml, in order, the same arithmetic the assembler uses. A leftover sections/NN.mp4
of a section that is not in decktalk.toml is ignored with a warning.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import CueTimes
from ..errors import ConfigError, MissingInputError
from ..media import ffmpeg
from ..project import Project
from ..settings import VerifyConfig
from ..verdicts import SkipReason, Verdict

log = logging.getLogger(__name__)


def section_starts(project: Project) -> tuple[dict[str, float], float]:
    """(where each assembled section of decktalk.toml starts, the total length). Other files are never counted."""
    starts: dict[str, float] = {}
    t = 0.0
    for sec in project.sections:
        f = project.section_video(sec)
        if not f.exists():
            continue
        starts[sec.key] = t
        t += ffmpeg.probe_duration(f)
    return starts, t


def _rel(path: Path, root: Path) -> str:
    """A path relative to the project root with forward slashes, or the absolute path when it lies outside."""
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()


def _num(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


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
        return self.verdict == Verdict.SKIPPED

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "cue": self.cue,
            "cue_seconds": _num(self.cue_seconds, 3),
            "final_seconds": _num(self.final_seconds, 3),
            "changed_percent": _num(self.changed_percent, 2),
            "control_percent": _num(self.control_percent, 2),
            "offset_ms": self.offset_ms,
            "av_ms": self.av_ms,
            "verdict": self.verdict,
            "reason": self.reason,
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
        f"{Verdict.SKIPPED} {reason}: {detail}",
        verdict=Verdict.SKIPPED,
        reason=reason,
    )


@dataclass
class VerifyResult:
    total_seconds: float
    starts: list[StartCheck] = field(default_factory=list)
    cuts: list[CutCheck] = field(default_factory=list)
    cues: list[CueCheck] = field(default_factory=list)
    seams: list[SeamCheck] = field(default_factory=list)
    final: Path | None = None
    silent: bool = False  # The build carries placeholder narration, so the a/v column is measured.

    @property
    def ok(self) -> bool:
        return (
            all(s.ok for s in self.starts)
            and all(c.ok for c in self.cuts)
            and all(c.ok for c in self.seams)
            and all(c.ok for c in self.cues if not c.skipped)
        )

    @property
    def black_starts(self) -> int:
        return sum(not s.ok for s in self.starts)

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The result as JSON-ready data: numbers as numbers, and paths relative to the project root."""
        return {
            "final": None if self.final is None else _rel(self.final, root),
            "total_seconds": round(self.total_seconds, 3),
            "silent": self.silent,
            "starts": [s.to_dict() for s in self.starts],
            "cuts": [c.to_dict() for c in self.cuts],
            "seams": [c.to_dict() for c in self.seams],
            "cues": [c.to_dict() for c in self.cues],
        }


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
    is kept exactly, so a well-spaced cue is measured as it always was. A spoiled probe becomes
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


def best_probe(
    final: Path, before: float, floor: float, cue_at: float, delays: list[float], cfg: VerifyConfig
) -> tuple[float, float, float, float] | None:
    """(margin, changed, control, probe time) of the probe with the largest margin, the earlier on a tie.

    Each probe after the cue is compared with the reference, and the control is the quieter of
    two spans of the same length that end at the reference. Motion that is always there shows in
    both, while an earlier reveal still settling shows in one.
    """
    size = {"level": cfg.diff_level, "width": cfg.probe_width, "height": cfg.probe_height}
    best: tuple[float, float, float, float] | None = None
    for delay in delays:
        after = cue_at + delay
        chg = ffmpeg.changed_pixels_percent(final, before, after, **size)
        controls = [
            ffmpeg.changed_pixels_percent(final, a, b, **size) for a, b in control_spans(before, after - before, floor)
        ]
        ctl = min(controls) if controls else 0.0
        margin = chg - ctl
        if best is None or margin > best[0]:
            best = (margin, chg, ctl, after)
    return best


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


def opted_out(project: Project) -> set[tuple[str, str]]:
    """(section key, cue id) for every cue that cues.json marks "verify": false."""
    from .align import load_cues

    return {(f"{spec.number:02d}", cue.cue) for spec in load_cues(project) for cue in spec.cues if not cue.verify}


def verify(project: Project, checks: list[str] | None = None, only: list[int] | None = None) -> VerifyResult:
    """Check section starts, cuts, and cues on the final mp4.

    `checks` names cues as SECTION:CUE. None checks every cue in cue-times.json except those
    opted out in cues.json, and an empty list checks no cues. `only` keeps the cue checks
    of those section numbers. A cue named explicitly is measured even when it is opted out.
    """
    from .assemble import fade_flags, frame_dip, stray_warnings

    cfg = project.settings.verify
    final = project.final
    stray_warnings(project, "verify")
    starts, total = section_starts(project)
    if not starts or not final.exists():
        raise MissingInputError("need build/sections/NN.mp4 files and the final mp4; run `decktalk assemble` first")
    takes = project.takes()
    clicks = bool(takes and takes.estimated)
    result = VerifyResult(total_seconds=total, final=final, silent=clicks)
    for key, t in starts.items():
        probe = t + cfg.after_dip_seconds
        yavg, ymax = ffmpeg.luma_at(final, probe)
        result.starts.append(
            StartCheck(key=key, start=t, probe_at=probe, yavg=yavg, ymax=ymax, ok=ymax > cfg.visible_ymax)
        )
    # The cut check listens to the narration track alone, so the music or an effect
    # at a boundary does not count as speech. A clip carries its own audio and is exempt.
    timeline = project.timeline()
    narration = project.narration_dir / (timeline.narration if timeline else "narration.mp3")
    if timeline and narration.exists():
        for key, sec in timeline.sections.items():
            if key not in starts:
                continue
            window = min(cfg.cut_window_seconds, sec.duration)
            level = ffmpeg.rms_db(narration, max(0.0, sec.end - window), window)
            cut = starts[key] + sec.duration
            result.cuts.append(CutCheck(key=key, cut_at=round(cut, 3), rms_db=level, ok=level <= cfg.cut_max_db))
    fps = project.settings.video.fps
    flags = fade_flags(project)
    dip = frame_dip(project.transition.dip_seconds, fps)
    result.seams = seam_checks(project, final, starts, flags, dip, fps)
    cue_times = project.cue_times()
    opt_out: set[tuple[str, str]] = set()
    if checks is None:
        checks = default_checks(cue_times, only)
        opt_out = opted_out(project) if checks else set()
    else:
        for check in checks:
            if ":" not in check or not check.split(":", 1)[0].strip().isdigit():
                raise ConfigError(f"malformed check {check!r}; want SECTION:CUE, e.g. 3:3.1draw")
        checks = [c for c in checks if not only or int(c.split(":", 1)[0]) in only]
    if not checks:
        return result
    for check in checks:
        sec, cue = check.split(":", 1)
        key = f"{int(sec):02d}"
        cue_t = cue_times.get(key, cue)
        if (key, cue) in opt_out:
            result.cues.append(skipped(check, SkipReason.OPTED_OUT, 'cues.json sets "verify": false'))
            continue
        if cue_t is None:
            result.cues.append(
                CueCheck(
                    check,
                    None,
                    None,
                    None,
                    None,
                    False,
                    "UNRESOLVED: the cue is not in cue-times.json",
                    verdict=Verdict.UNRESOLVED,
                )
            )
            continue
        if key not in starts:
            result.cues.append(skipped(check, SkipReason.SECTION_NOT_ASSEMBLED, f"no sections/{key}.mp4"))
            continue
        sec_start = starts[key]
        sec_end = next((t for k, t in starts.items() if k > key), total)
        fade_in = flags.get(key, (False, False))[0]
        floor = sec_start + (dip if fade_in else 0.0)
        # The reference frame sits just before the cue fires. Each probe after the cue is
        # compared with it, and a control span of the same length that ends at the reference
        # measures whatever else is moving (a camera push, an earlier reveal still settling).
        before = reference_time(sec_start, cue_t, fade_in, dip, cfg, fps)
        if before is None:
            result.cues.append(
                skipped(
                    check,
                    SkipReason.REFERENCE_CLAMPED,
                    f"the cue at {cue_t:.2f}s leaves no frame before it past the fade-in",
                )
            )
            continue
        # Another cue close by would spoil a probe or its control, so the probes fit the gap instead.
        neighbors = [sec_start + t for c, t in cue_times.times(key).items() if c != cue]
        delays, fitted = probe_plan(sec_start + cue_t, before, floor, sec_end, neighbors, cfg, fps)
        if fitted:
            log.info(
                "%s: another cue is close, so the probes are fitted to %s s after the cue",
                check,
                ", ".join(f"{d:g}" for d in delays),
            )
        best = best_probe(final, before, floor, sec_start + cue_t, delays, cfg)
        if best is None:
            result.cues.append(skipped(check, SkipReason.TOO_CLOSE_TO_END, "every probe falls past the section end"))
            continue
        margin, chg, ctl, after = best
        landed = chg >= cfg.min_changed_percent and margin >= cfg.min_margin_percent
        if not landed:
            result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, False, verdict=Verdict.NO_CHANGE))
            continue
        # A pass by a thin margin is still a pass, but a slightly smaller reveal would fail.
        passed = Verdict.THIN_CHANGE if thin_change(chg, margin, cfg) else Verdict.CHANGED
        offset_ms = first_change_offset(final, before, after, sec_start + cue_t, cfg, fps)
        if offset_ms is None:
            result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, True, verdict=passed))
            continue
        limit_ms = cfg.max_offset_frames * 1000 / fps
        on_time = abs(offset_ms) <= limit_ms + 0.5
        note = "" if on_time else f"first change {offset_ms:+d} ms from the cue, limit {limit_ms:.0f} ms"
        av_ms = None
        reason = None
        if clicks:
            # A build without voice carries a click at every word start, so the finished file's audio
            # can be measured against its picture: the click nearest the cue is the word.
            word_t = cue_times.word_at(key, cue) or cue_t
            click_ms = click_offset_ms(
                final, sec_start + word_t, cfg.click_search_seconds, floor=sec_start, ceiling=sec_end
            )
            if click_ms is None:
                reason = SkipReason.NO_CLICK
            else:
                # The picture's offset is measured from the cue and the click's from the cued
                # word, so the difference already allows for the cue's own offset.
                av_ms = offset_ms - click_ms
                av_limit_ms = cfg.max_av_frames * 1000 / fps
                if abs(av_ms) > av_limit_ms + 0.5:
                    on_time = False
                    note = f"a/v {av_ms:+d} ms, limit {av_limit_ms:.0f} ms"
        result.cues.append(
            CueCheck(
                check,
                cue_t,
                sec_start + cue_t,
                chg,
                ctl,
                on_time,
                note,
                offset_ms,
                av_ms,
                verdict=passed if on_time else Verdict.OFF_CUE,
                reason=reason,
            )
        )
    return result


def seam_checks(
    project: Project,
    final: Path,
    starts: dict[str, float],
    flags: dict[str, tuple[bool, bool]],
    dip: float,
    fps: int,
) -> list[SeamCheck]:
    """One row per assembled section that sets seamless and follows an assembled section.

    The frames compared sit outside any dip, so a fade to black is never taken for a pop. Each
    time sits half a frame before the frame it names, because a frame is the first at or after it.
    """
    cfg = project.settings.verify
    rows: list[SeamCheck] = []
    for prev, sec in zip(project.sections, project.sections[1:], strict=False):
        if not sec.seamless or sec.key not in starts or prev.key not in starts:
            continue
        cut = starts[sec.key]
        last = cut - (dip if flags.get(prev.key, (False, False))[1] else 0.0) - 1.5 / fps
        first = cut + (dip if flags.get(sec.key, (False, False))[0] else 0.0) - 0.5 / fps
        first = max(first, cut)
        share = ffmpeg.changed_pixels_percent(
            final, last, first, level=cfg.diff_level, width=cfg.probe_width, height=cfg.probe_height
        )
        rows.append(SeamCheck(sec.key, cut, last, first, share, share <= cfg.max_pop_percent))
    return rows


def first_change_offset(
    final: Path, before: float, after: float, cue_at: float, cfg: VerifyConfig, fps: int
) -> int | None:
    """Milliseconds from the cue to the first frame past the reference where the reveal begins.

    The frames up to the cue set a noise floor, so a camera push or an earlier reveal still
    settling does not count as the onset.

    x264 codes a still picture with a little ringing in the one or two frames before a change:
    a few pixels, up to about 15 levels apart at probe_width, which at onset_diff_level read as
    a reveal 40 to 100 ms early while the recording showed it on time. A second series at
    block_width by block_height, where each pixel averages an 8 by 8 block of a 1080p frame,
    cancels that zero-mean ringing, so a frame counts only when a block changed there too.
    """
    series, blocks = (
        ffmpeg.changed_series(final, before, before, after, fps=fps, level=cfg.onset_diff_level, width=w, height=h)
        for w, h in ((cfg.probe_width, cfg.probe_height), (cfg.block_width, cfg.block_height))
    )
    return onset_offset_ms(
        series,
        before,
        cue_at,
        cfg.onset_percent,
        tolerance=(cfg.max_offset_frames + 0.5) / fps,
        blocks=dict(blocks),
    )


def click_offset_ms(
    final: Path, expected: float, search: float, *, floor: float = 0.0, ceiling: float | None = None
) -> int | None:
    """Milliseconds from `expected` to the loudest sample within ±search seconds, or None when nothing is there.

    The window never reaches before `floor` or past `ceiling`, so the sound of a neighboring
    section, such as the audio of a clip right before or after the cue's section, is never
    taken for the click.
    """
    rate = 48000
    start = max(floor, expected - search)
    stop = expected + search if ceiling is None else min(ceiling, expected + search)
    if stop <= start:
        return None
    samples = ffmpeg.pcm_span(final, start, stop - start, sample_rate=rate)
    if not samples:
        return None
    peak = max(range(len(samples)), key=lambda i: abs(samples[i]))
    if abs(samples[peak]) < 400:  # about -38 dBFS: no click in the window
        return None
    return int(round((start + peak / rate - expected) * 1000))


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
