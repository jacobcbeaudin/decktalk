"""Stage 6: checks on the assembled mp4.

starts   every section opens on a real frame: past the dip-to-black, YMAX above
         visible_ymax means content is on screen.
cuts     the audio in the last cut_window_seconds before every cut is quieter than
         cut_max_db, so no cut lands on speech.
cues     for each SECTION:CUE, the picture changes across the cue. With no list, every cue
         in beats.json is checked, in section order and then cue time, except the cues
         that cues.json marks "verify": false. The reference frame is the first frame at
         or after lead_seconds before the cue, but never inside the section's fade-in and
         always at least one frame before the cue. For each delay in probe_delays, the
         share of pixels that change by more than diff_level between the reference and the
         probe is compared with a control. The control is the smaller share of two spans
         that each last as long as the probe's span and end at the reference, one after
         the other, and each span compares only its first and its last frame. The control
         captures anything else in motion, such as a camera push. A cue lands when the
         best probe changes at least min_changed_percent of the pixels and exceeds its
         control by min_margin_percent. Everything stays inside the section.
offset   once a cue lands, every frame from the reference to the passing probe is
         compared with the reference at onset_diff_level, which gives each frame's changed
         share. A reveal is abrupt, so the first frame whose share rises by at least
         onset_percent over the frame before it marks where the visual began to appear,
         even when that frame sits before the cue. Motion that is always there, such as a
         camera push or a curve still drawing, grows a little every frame and does not
         jump. When no frame jumps, the first frame whose share exceeds both onset_percent
         and every share more than max_offset_frames and a half frames before the cue is
         used instead, which catches a reveal that grows slowly, such as text typing in.
         The frame's distance from the cue is reported in milliseconds, and a cue fails
         when the distance exceeds max_offset_frames.
a/v      after a silent build, the loudest sample within click_search_seconds of the
         cued word's start, and inside the cue's section, is taken as the click. The a/v
         value is the offset minus the click's distance from the cued word's start, and a
         cue also fails when that value exceeds max_av_frames.

Cue verdicts are changed, OFF CUE, NO CHANGE, UNRESOLVED, and skipped. A skipped row is
never a failure, and its reason says why nothing was measured:

    REFERENCE_CLAMPED      no reference frame fits after the fade-in and before the cue
    SECTION_NOT_ASSEMBLED  the section has no NN-section.mp4
    TOO_CLOSE_TO_END       every probe would fall past the end of the section
    OPTED_OUT              cues.json sets "verify": false on the cue

A measured row after a silent build carries the reason NO_CLICK when no click was found
near the cued word, so its a/v value is empty.

Section starts are the cumulative lengths of build/out/NN-section.mp4, the same
arithmetic the assembler uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import Beats
from ..config import VerifyConfig
from ..errors import ConfigError, MissingInputError
from ..media import ffmpeg
from ..project import Project

log = logging.getLogger(__name__)

REFERENCE_CLAMPED = "REFERENCE_CLAMPED"
SECTION_NOT_ASSEMBLED = "SECTION_NOT_ASSEMBLED"
TOO_CLOSE_TO_END = "TOO_CLOSE_TO_END"
OPTED_OUT = "OPTED_OUT"
NO_CLICK = "NO_CLICK"


def section_starts(project: Project) -> tuple[dict[str, float], float]:
    starts: dict[str, float] = {}
    t = 0.0
    for f in sorted(project.out_dir.glob("[0-9][0-9]-section.mp4")):
        starts[f.name[:2]] = t
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
    def verdict(self) -> str:
        return "ok" if self.ok else "BLACK"

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
    def verdict(self) -> str:
        return "quiet" if self.ok else "SPEECH AT CUT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "cut_at": round(self.cut_at, 3),
            "rms_db": round(self.rms_db, 2),
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
    av_ms: int | None = None  # The offset minus the click's distance from the cued word's start, in a silent build.
    verdict: str = ""  # changed, OFF CUE, NO CHANGE, UNRESOLVED, or skipped. Derived from ok when left empty.
    reason: str | None = None  # Why a row was skipped, or NO_CLICK on a measured row with no a/v value.

    def __post_init__(self) -> None:
        if not self.verdict:
            self.verdict = "changed" if self.ok else ("OFF CUE" if self.offset_ms is not None else "NO CHANGE")

    @property
    def section(self) -> int:
        return int(self.check.split(":", 1)[0])

    @property
    def cue(self) -> str:
        return self.check.split(":", 1)[1]

    @property
    def skipped(self) -> bool:
        return self.verdict == "skipped"

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


def skipped(check: str, reason: str, detail: str) -> CueCheck:
    """A row that measured nothing. The table prints its note, so the note leads with the verdict and reason."""
    return CueCheck(
        check, None, None, None, None, True, f"skipped {reason}: {detail}", verdict="skipped", reason=reason
    )


@dataclass
class VerifyResult:
    total_seconds: float
    starts: list[StartCheck] = field(default_factory=list)
    cuts: list[CutCheck] = field(default_factory=list)
    cues: list[CueCheck] = field(default_factory=list)
    final: Path | None = None
    silent: bool = False  # The build carries placeholder narration, so the a/v column is measured.

    @property
    def ok(self) -> bool:
        return (
            all(s.ok for s in self.starts)
            and all(c.ok for c in self.cuts)
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
            "cues": [c.to_dict() for c in self.cues],
        }


def reference_time(
    sec_start: float, cue_t: float, fade_in: bool, dip: float, cfg: VerifyConfig, fps: int
) -> float | None:
    """Where the reference frame for a cue sits in the final file, or None when no frame fits.

    The reference wants to sit lead_seconds before the cue, or earlier when a reveal that lands
    max_offset_frames early could otherwise already show in it, and the frame it names is the
    first frame at or after that time. It may not sit inside the
    section's fade-in, where the picture is still coming up from black, and it must sit at
    least one frame before the cue. A cue at the very start of a section, or inside its
    fade-in, leaves no such frame.
    """
    floor = sec_start + (dip if fade_in else 0.0)
    latest = sec_start + cue_t - 1.0 / fps
    # A reveal may land up to max_offset_frames early and still pass, so the reference must sit
    # before that whole window. Otherwise the early reveal is already in the reference frame,
    # and the scan measures the change only when the reveal settles, frames too late.
    lead = max(cfg.lead_seconds, (cfg.max_offset_frames + 1.5) / fps)
    ref = max(floor, sec_start + cue_t - lead)
    if ref > latest + 1e-6:
        return None
    return round(ref, 4)


def default_checks(beats: Beats, only: list[int] | None = None) -> list[str]:
    """Every resolved cue as SECTION:CUE, in section order and then cue time."""
    checks: list[str] = []
    for key in sorted(beats.sections):
        if only and int(key) not in only:
            continue
        for cue, _t in sorted(beats.sections[key].items(), key=lambda item: item[1]):
            checks.append(f"{int(key)}:{cue}")
    return checks


def opted_out(project: Project) -> set[tuple[str, str]]:
    """(section key, cue id) for every cue that cues.json marks "verify": false."""
    from .beats import load_cues

    return {(f"{spec.number:02d}", cue.step) for spec in load_cues(project) for cue in spec.cues if not cue.verify}


def verify(project: Project, checks: list[str] | None = None, only: list[int] | None = None) -> VerifyResult:
    """Check section starts, cuts, and cues on the final mp4.

    `checks` names cues as SECTION:CUE. None checks every cue in beats.json except those
    opted out in cues.json, and an empty list checks no cues. `only` keeps the cue checks
    of those section numbers. A cue named explicitly is measured even when it is opted out.
    """
    cfg = project.settings.verify
    final = project.final
    starts, total = section_starts(project)
    if not starts or not final.exists():
        raise MissingInputError("need build/out/NN-section.mp4 files and the final mp4; run `decktalk assemble` first")
    manifest = project.manifest()
    clicks = bool(manifest and manifest.estimated)
    result = VerifyResult(total_seconds=total, final=final, silent=clicks)
    for key, t in starts.items():
        probe = t + cfg.after_dip_seconds
        yavg, ymax = ffmpeg.luma_at(final, probe)
        result.starts.append(
            StartCheck(key=key, start=t, probe_at=probe, yavg=yavg, ymax=ymax, ok=ymax > cfg.visible_ymax)
        )
    # The cut check listens to the narration track alone, so an underscore or an effect
    # at a boundary does not count as speech. A clip carries its own audio and is exempt.
    timeline = project.timeline()
    narration = project.audio_dir / (timeline.narration if timeline else "narration.mp3")
    if timeline and narration.exists():
        for key, sec in timeline.sections.items():
            if key not in starts:
                continue
            window = min(cfg.cut_window_seconds, sec.duration)
            level = ffmpeg.rms_db(narration, max(0.0, sec.end - window), window)
            cut = starts[key] + sec.duration
            result.cuts.append(CutCheck(key=key, cut_at=round(cut, 3), rms_db=level, ok=level <= cfg.cut_max_db))
    beats = project.beats()
    opt_out: set[tuple[str, str]] = set()
    if checks is None:
        checks = default_checks(beats, only)
        opt_out = opted_out(project) if checks else set()
    else:
        for check in checks:
            if ":" not in check or not check.split(":", 1)[0].strip().isdigit():
                raise ConfigError(f"malformed check {check!r}; want SECTION:CUE, e.g. 3:3.1draw")
        checks = [c for c in checks if not only or int(c.split(":", 1)[0]) in only]
    if not checks:
        return result
    from .assemble import fade_flags, frame_dip
    from .beats import read_anchors

    probe_w, probe_h = cfg.probe_width, cfg.probe_height
    fps = project.settings.video.fps
    flags = fade_flags(project)
    dip = frame_dip(project.transition.dip_seconds, fps)
    anchors = read_anchors(project.beats_path.with_name("beats.anchors.json")) if clicks else {}
    for check in checks:
        sec, cue = check.split(":", 1)
        key = f"{int(sec):02d}"
        cue_t = beats.get(key, cue)
        if (key, cue) in opt_out:
            result.cues.append(skipped(check, OPTED_OUT, 'cues.json sets "verify": false'))
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
                    "UNRESOLVED: the cue is not in beats.json",
                    verdict="UNRESOLVED",
                )
            )
            continue
        if key not in starts:
            result.cues.append(skipped(check, SECTION_NOT_ASSEMBLED, f"no {key}-section.mp4"))
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
                skipped(check, REFERENCE_CLAMPED, f"the cue at {cue_t:.2f}s leaves no frame before it past the fade-in")
            )
            continue
        best: tuple[float, float, float, float] | None = None  # (margin, changed, control, probe time)
        for delay in cfg.probe_delays:
            after = sec_start + cue_t + delay
            if after > sec_end - 0.05:
                continue
            span = after - before
            chg = ffmpeg.changed_pixels_percent(
                final, before, after, level=cfg.diff_level, width=probe_w, height=probe_h
            )
            # The control is the quieter of two equal spans before the reference. Motion that
            # is always there shows in both, while an earlier reveal still settling shows in one.
            controls = []
            for n in (1, 2):
                ctl_b = before - (n - 1) * span
                ctl_a = ctl_b - span
                if ctl_a >= floor:
                    controls.append(
                        ffmpeg.changed_pixels_percent(
                            final, ctl_a, ctl_b, level=cfg.diff_level, width=probe_w, height=probe_h
                        )
                    )
            ctl = min(controls) if controls else 0.0
            margin = chg - ctl
            if best is None or margin > best[0]:
                best = (margin, chg, ctl, after)
        if best is None:
            result.cues.append(skipped(check, TOO_CLOSE_TO_END, "every probe falls past the section end"))
            continue
        margin, chg, ctl, after = best
        landed = chg >= cfg.min_changed_percent and margin >= cfg.min_margin_percent
        if not landed:
            result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, False, verdict="NO CHANGE"))
            continue
        offset_ms = first_change_offset(final, before, after, sec_start + cue_t, cfg, fps)
        if offset_ms is None:
            result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, True, verdict="changed"))
            continue
        limit_ms = cfg.max_offset_frames * 1000 / fps
        on_time = abs(offset_ms) <= limit_ms + 0.5
        note = "" if on_time else f"first change {offset_ms:+d} ms from the cue, limit {limit_ms:.0f} ms"
        av_ms = None
        reason = None
        if clicks:
            # A silent build carries a click at every word start, so the finished file's audio
            # can be measured against its picture: the click nearest the cue is the word.
            word_t = anchors.get(key, {}).get(cue, cue_t)
            click_ms = click_offset_ms(
                final, sec_start + word_t, cfg.click_search_seconds, floor=sec_start, ceiling=sec_end
            )
            if click_ms is None:
                reason = NO_CLICK
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
                verdict="changed" if on_time else "OFF CUE",
                reason=reason,
            )
        )
    return result


def first_change_offset(
    final: Path, before: float, after: float, cue_at: float, cfg: VerifyConfig, fps: int
) -> int | None:
    """Milliseconds from the cue to the first frame past the reference where the reveal begins.

    The frames up to the cue set a noise floor, so a camera push or an earlier reveal still
    settling does not count as the onset.
    """
    series = ffmpeg.changed_series(
        final,
        before,
        before,
        after,
        fps=fps,
        level=cfg.onset_diff_level,
        width=cfg.probe_width,
        height=cfg.probe_height,
    )
    return onset_offset_ms(series, before, cue_at, cfg.onset_percent, tolerance=(cfg.max_offset_frames + 0.5) / fps)


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
    series: list[tuple[float, float]], before: float, cue_at: float, onset: float, tolerance: float = 0.0
) -> int | None:
    """Where the reveal begins, in milliseconds from the cue, from the changed share per frame.

    A reveal is a step: between two consecutive frames the changed share jumps by at least
    `onset`. Motion that is always there, such as a camera push or a curve still drawing,
    is a slope that grows a little every frame and never jumps. The first jump after the
    reference frame is the onset, and it may sit before the cue. The series starts on the
    reference itself, so the first frame after it is judged against a share of zero. When
    nothing jumps, the first frame whose share exceeds the pre-cue floor is used instead,
    which catches a reveal that grows slowly, such as text typing in.
    """
    prev: float | None = None
    for t, pct in series:
        if prev is not None and t > before and pct - prev >= onset:
            return int(round((t - cue_at) * 1000))
        prev = pct
    floor = max((pct for t, pct in series if t <= cue_at - tolerance), default=0.0)
    threshold = max(onset, floor)
    for t, pct in series:
        if t > before and pct > threshold:
            return int(round((t - cue_at) * 1000))
    return None
