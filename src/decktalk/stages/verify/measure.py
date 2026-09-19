"""The ffmpeg calls behind the cue plan: the probes, the onset scan and the click search.

`plan.py` decides what to measure and this module measures it, so every frame comparison and every
sample read in `verify` goes through one file. The cue loop lives here too, because it is the one
place where the arithmetic and the measurements meet.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...media import audio, ffmpeg, frames
from ...model import Project
from ...model.document import frame_dip
from ...settings import VerifyConfig
from ...verdicts import SkipReason, Verdict
from .plan import (
    CueCheck,
    control_spans,
    onset_offset_ms,
    probe_plan,
    reference_time,
    skipped,
    thin_change,
)

log = logging.getLogger(__name__)


def assembled_starts(project: Project) -> tuple[dict[str, float], float]:
    """(where each section of decktalk.toml starts in the final file, the total length), probed from disk.

    The lengths are read from `build/sections/NN.mp4`, so this is the same arithmetic the cut did,
    measured again on the files it wrote. A section video of a section that is not in `decktalk.toml`
    is never counted.
    """
    starts: dict[str, float] = {}
    t = 0.0
    for sec in project.sections:
        f = project.section_video(sec)
        if not f.exists():
            continue
        starts[sec.key] = t
        t += ffmpeg.probe_duration(f)
    return starts, t


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
        chg = frames.changed_pixels_percent(final, before, after, **size)
        controls = [
            frames.changed_pixels_percent(final, a, b, **size) for a, b in control_spans(before, after - before, floor)
        ]
        ctl = min(controls) if controls else 0.0
        margin = chg - ctl
        if best is None or margin > best[0]:
            best = (margin, chg, ctl, after)
    return best


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
        frames.changed_series(final, before, before, after, fps=fps, level=cfg.onset_diff_level, width=w, height=h)
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
    samples = audio.pcm_span(final, start, stop - start, sample_rate=rate)
    if not samples:
        return None
    peak = max(range(len(samples)), key=lambda i: abs(samples[i]))
    if abs(samples[peak]) < 400:  # about -38 dBFS: no click in the window
        return None
    return int(round((start + peak / rate - expected) * 1000))


def cue_checks(
    project: Project,
    final: Path,
    starts: dict[str, float],
    total: float,
    checks: list[str],
    opt_out: set[tuple[str, str]],
) -> list[CueCheck]:
    """One row per named cue: where the picture changed, how far from the cue, and how far from its word."""
    cfg = project.settings.verify
    fps = project.settings.video.fps
    flags = project.document.fade_flags
    dip = frame_dip(project.transition.dip_seconds, fps)
    cue_times = project.cue_times()
    takes = project.takes()
    clicks = bool(takes and takes.estimated)
    rows: list[CueCheck] = []
    for check in checks:
        sec, cue = check.split(":", 1)
        key = f"{int(sec):02d}"
        cue_t = cue_times.get(key, cue)
        if (key, cue) in opt_out:
            rows.append(skipped(check, SkipReason.OPTED_OUT, 'cues.json sets "verify": false'))
            continue
        if cue_t is None:
            detail = "UNRESOLVED: the cue is not in cue-times.json"
            rows.append(CueCheck(check, None, None, None, None, False, detail, verdict=Verdict.UNRESOLVED))
            continue
        if key not in starts:
            rows.append(skipped(check, SkipReason.SECTION_NOT_ASSEMBLED, f"no sections/{key}.mp4"))
            continue
        sec_start = starts[key]
        sec_end = next((t for k, t in starts.items() if k > key), total)
        fade_in = flags.get(key, (False, False))[0]
        floor = sec_start + (dip if fade_in else 0.0)
        # The reference frame sits just before the cue fires. Each probe after the cue is compared
        # with it, and a control span of the same length that ends at the reference measures
        # whatever else is moving (a camera push, an earlier reveal still settling).
        before = reference_time(sec_start, cue_t, fade_in, dip, cfg, fps)
        if before is None:
            detail = f"the cue at {cue_t:.2f}s leaves no frame before it past the fade-in"
            rows.append(skipped(check, SkipReason.REFERENCE_CLAMPED, detail))
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
            rows.append(skipped(check, SkipReason.TOO_CLOSE_TO_END, "every probe falls past the section end"))
            continue
        rows.append(judge_cue(project, final, check, cue_t, sec_start, sec_end, floor, before, best, clicks=clicks))
    return rows


def judge_cue(
    project: Project,
    final: Path,
    check: str,
    cue_t: float,
    sec_start: float,
    sec_end: float,
    floor: float,
    before: float,
    best: tuple[float, float, float, float],
    *,
    clicks: bool,
) -> CueCheck:
    """The row for one cue whose best probe has been measured: the landing, the onset and the a/v value."""
    cfg = project.settings.verify
    fps = project.settings.video.fps
    key = f"{int(check.split(':', 1)[0]):02d}"
    cue = check.split(":", 1)[1]
    margin, chg, ctl, after = best
    at = sec_start + cue_t
    if not (chg >= cfg.min_changed_percent and margin >= cfg.min_margin_percent):
        note = (
            f"{chg:.2f} percent of the frame changed against a {cfg.min_changed_percent:.2f} percent "
            f"floor, with a margin of {margin:.2f} against {cfg.min_margin_percent:.2f}, so nothing "
            "visibly happened at the cue."
        )
        return CueCheck(check, cue_t, at, chg, ctl, False, note, verdict=Verdict.NO_CHANGE)
    # A pass by a thin margin is still a pass, but a slightly smaller reveal would fail. It is an
    # uncertain finding rather than a passing row, so it carries its own sentence like any other.
    thin = thin_change(chg, margin, cfg)
    passed = Verdict.THIN_CHANGE if thin else Verdict.CHANGED
    thin_note = (
        f"the reveal at cue {cue!r} changed {chg:.2f} percent of the frame with a margin of "
        f"{margin:.2f}, so a slightly smaller reveal would not have been measured at all."
        if thin
        else ""
    )
    offset_ms = first_change_offset(final, before, after, at, cfg, fps)
    if offset_ms is None:
        return CueCheck(check, cue_t, at, chg, ctl, True, thin_note, verdict=passed)
    limit_ms = cfg.max_offset_frames * 1000 / fps
    on_time = abs(offset_ms) <= limit_ms + 0.5
    note = (
        thin_note
        if on_time
        else (
            f"the reveal at cue {cue!r} first changed {offset_ms:+d} ms from its cue, outside the "
            f"{limit_ms:.0f} ms limit."
        )
    )
    av_ms: int | None = None
    reason: SkipReason | None = None
    if clicks:
        # A build without voice carries a click at every word start, so the finished file's audio can
        # be measured against its picture: the click nearest the cue is the word.
        word_t = project.cue_times().word_at(key, cue) or cue_t
        click_ms = click_offset_ms(
            final, sec_start + word_t, cfg.click_search_seconds, floor=sec_start, ceiling=sec_end
        )
        if click_ms is None:
            reason = SkipReason.NO_CLICK
        else:
            # The picture's offset is measured from the cue and the click's from the cued word, so
            # the difference already allows for the cue's own offset.
            av_ms = offset_ms - click_ms
            av_limit_ms = cfg.max_av_frames * 1000 / fps
            if abs(av_ms) > av_limit_ms + 0.5:
                on_time = False
                note = (
                    f"the reveal at cue {cue!r} is {av_ms:+d} ms from the word it lands on, outside "
                    f"the {av_limit_ms:.0f} ms limit."
                )
    return CueCheck(
        check, cue_t, at, chg, ctl, on_time, note, offset_ms, av_ms,
        verdict=passed if on_time else Verdict.OFF_CUE, reason=reason,
    )  # fmt: skip
