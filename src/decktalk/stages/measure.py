"""Stage 4: find narration t=0 in each recording, and sanity-check the recordings.

measure: the last magenta frame plus one frame is where audio t=0 belongs; the
assembler trims that much off the head of the video. Without a marker the fallback is
the first painted frame plus the settle; failing that a fixed guess.

check: duration against what was requested, and luma at 10/50/90 %, so a black or
truncated recording is caught before assembly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from ..artifacts import Sidecar
from ..config import AlignConfig
from ..errors import MissingInputError
from ..media import ffmpeg
from ..project import Project

log = logging.getLogger(__name__)


def recordings(project: Project, only: list[int] | None = None) -> list[Path]:
    keys = {f"{k:02d}" for k in only} if only else None
    files = sorted(project.rec_dir.glob("[0-9][0-9]-scene.webm"))
    files = [f for f in files if keys is None or f.name[:2] in keys]
    if not files:
        raise MissingInputError(f"no recordings in {project.rec_dir}; run `decktalk record` first")
    return files


def is_magenta(f: ffmpeg.FrameStats, cfg: AlignConfig) -> bool:
    return (
        cfg.magenta_luma_min < f.yavg < cfg.magenta_luma_max
        and f.uavg > cfg.magenta_chroma_min
        and f.vavg > cfg.magenta_chroma_min
    )


def measure_lead(webm: Path, settle: float, cfg: AlignConfig) -> tuple[float, float | None, str]:
    """(trim point, marker start, method). The trim point is just past the magenta flash; the
    marker start is where narration t=0 truly sits, so the assembler can hold the first clean
    frame for the difference and keep the picture on the words."""
    rows = ffmpeg.frame_stats(webm, cfg.scan_seconds)
    if not rows:
        return round(cfg.fallback_first_paint_seconds + settle, 3), None, "no frames read; fallback"
    frame_dt = 0.04
    if len(rows) > 1:
        frame_dt = max(0.02, (rows[-1].pts - rows[0].pts) / (len(rows) - 1))
    magenta = [f.pts for f in rows if is_magenta(f, cfg)]
    if magenta:
        return round(magenta[-1] + frame_dt, 3), round(magenta[0], 3), f"marker ({len(magenta)} magenta frames)"
    painted = [f.pts for f in rows if f.ymax > cfg.painted_ymax and f.yavg < cfg.painted_yavg_max]
    if painted:
        return round(painted[0] + settle, 3), None, "first paint + settle (no marker)"
    return round(cfg.fallback_first_paint_seconds + settle, 3), None, "fallback guess + settle"


@dataclass
class LeadMeasurement:
    key: str
    lead_in_seconds: float
    wallclock_seconds: float
    method: str
    flash_seconds: float = 0.0


def measure(project: Project, only: list[int] | None = None) -> list[LeadMeasurement]:
    cfg = project.settings.align
    out: list[LeadMeasurement] = []
    for webm in recordings(project, only):
        sidecar_path = webm.with_suffix(".json")
        side = Sidecar.load(sidecar_path) or Sidecar(
            url="",
            requested_seconds=0,
            settle_seconds=project.settings.record.settle_seconds,
            load_seconds=0,
            lead_seconds=0,
        )
        lead_in, marker_start, method = measure_lead(webm, side.settle_seconds, cfg)
        side.lead_in_seconds = lead_in
        side.marker_start_seconds = marker_start
        side.lead_method = method
        side.save(sidecar_path)
        out.append(
            LeadMeasurement(
                key=webm.name[:2],
                lead_in_seconds=lead_in,
                wallclock_seconds=side.lead_seconds,
                method=method,
                flash_seconds=side.flash_seconds,
            )
        )
        log.info("[lead] %s  trim %.3fs, flash %.3fs  (%s)", webm.name[:2], lead_in, side.flash_seconds, method)
    return out


@dataclass
class RecordingCheck:
    key: str
    duration: float
    wanted: float
    y10: float
    y50: float
    y90: float
    max50: float
    verdict: str  # "ok", "BLACK?", "TRUNCATED", or both

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"


def check(project: Project, only: list[int] | None = None) -> list[RecordingCheck]:
    cfg = project.settings.align
    out: list[RecordingCheck] = []
    for f in recordings(project, only):
        side = Sidecar.load(f.with_suffix(".json"))
        wanted = side.requested_seconds if side else 0.0
        dur = ffmpeg.probe_duration(f)
        y10, y50, y90 = (ffmpeg.luma_at(f, dur * k)[0] for k in (0.10, 0.50, 0.90))
        max50 = ffmpeg.luma_at(f, dur * 0.5)[1]
        verdicts = []
        if max50 < cfg.black_ymax:
            verdicts.append("BLACK?")
        if wanted and dur < wanted - cfg.truncated_slack_seconds:
            verdicts.append("TRUNCATED")
        row = RecordingCheck(f.name[:2], dur, wanted, y10, y50, y90, max50, " ".join(verdicts) or "ok")
        if not row.ok:
            log.warning("[chk ] %s  %s", row.key, row.verdict)
        out.append(row)
    return out
