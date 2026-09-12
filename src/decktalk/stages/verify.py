"""Stage 6: checks on the assembled mp4.

starts   every section opens on a real frame: past the dip-to-black, YMAX above
         visible_ymax means content is on screen.
cuts     the audio in the last cut_window_seconds before every cut is quieter than
         cut_max_db, so no cut lands on speech.
cues     for each SECTION:CUE, the picture changes across the cue. The reference frame
         sits lead_seconds before the cue. For each delay in probe_delays, the share of
         pixels that change by more than diff_level between the reference and the probe
         is compared with the same measure over an equal span that ends at the reference,
         which captures anything else in motion, such as a camera push. A cue lands when
         the best probe changes at least min_changed_percent of the pixels and exceeds
         its control by min_margin_percent. Everything stays inside the section.
offset   once a cue lands, the frames between the reference and the passing probe are
         compared with the reference one by one. The first frame that changes at least
         onset_percent of the pixels, and more than any frame before the cue did, is where
         the visual began to appear, and its distance from the cue is reported in
         milliseconds. The onset threshold is far below min_changed_percent on purpose: a
         fade or a stroke that draws itself starts on its cue but takes many frames to
         change a tenth of the picture. A cue fails when the distance exceeds
         max_offset_frames.

Section starts are the cumulative lengths of build/out/NN-section.mp4, the same
arithmetic the assembler uses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import VerifyConfig
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
class CutCheck:
    """The audio just before a cut. Speech still sounding there means the cut is early."""

    key: str
    cut_at: float
    rms_db: float
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
    offset_ms: int | None = None  # Where the first changed frame sits relative to the cue.
    av_ms: int | None = None  # Picture onset minus the placeholder click, in a silent build.


@dataclass
class VerifyResult:
    total_seconds: float
    starts: list[StartCheck] = field(default_factory=list)
    cuts: list[CutCheck] = field(default_factory=list)
    cues: list[CueCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.starts) and all(c.ok for c in self.cuts) and all(c.ok for c in self.cues)

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
    if not checks:
        return result
    beats = project.beats()
    probe_w, probe_h = cfg.probe_width, cfg.probe_height
    fps = project.settings.video.fps
    manifest = project.manifest()
    clicks = bool(manifest and manifest.estimated)
    from .beats import read_anchors

    anchors = read_anchors(project.beats_path.with_name("beats.anchors.json")) if clicks else {}
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
            result.cues.append(
                CueCheck(check, cue_t, None, None, None, False, "cue too close to the section end to probe")
            )
            continue
        margin, chg, ctl, after = best
        landed = chg >= cfg.min_changed_percent and margin >= cfg.min_margin_percent
        if not landed:
            result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, False))
            continue
        offset_ms = first_change_offset(final, before, after, sec_start + cue_t, cfg, fps)
        if offset_ms is None:
            result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, True))
            continue
        limit_ms = cfg.max_offset_frames * 1000 / fps
        on_time = abs(offset_ms) <= limit_ms + 0.5
        note = "" if on_time else f"first change {offset_ms:+d} ms from the cue, limit {limit_ms:.0f} ms"
        av_ms = None
        if clicks:
            # A silent build carries a click at every word start, so the finished file's audio
            # can be measured against its picture: the click nearest the cue is the word.
            word_t = anchors.get(key, {}).get(cue, cue_t)
            click_ms = click_offset_ms(final, sec_start + word_t, cfg.click_search_seconds)
            if click_ms is not None:
                # The picture's offset is measured from the cue and the click's from the cued
                # word, so the difference already allows for the cue's own offset.
                av_ms = offset_ms - click_ms
                av_limit_ms = cfg.max_av_frames * 1000 / fps
                if abs(av_ms) > av_limit_ms + 0.5:
                    on_time = False
                    note = f"picture {av_ms:+d} ms from the click, limit {av_limit_ms:.0f} ms"
        result.cues.append(CueCheck(check, cue_t, sec_start + cue_t, chg, ctl, on_time, note, offset_ms, av_ms))
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


def click_offset_ms(final: Path, expected: float, search: float) -> int | None:
    """Milliseconds from `expected` to the loudest sample within ±search seconds, or None when nothing is there."""
    rate = 48000
    start = max(0.0, expected - search)
    samples = ffmpeg.pcm_span(final, start, 2 * search, sample_rate=rate)
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
    reference frame is the onset, and it may sit before the cue. When nothing jumps, the
    first frame whose share exceeds the pre-cue floor is used instead, which catches a
    reveal that grows slowly, such as text typing in.
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
