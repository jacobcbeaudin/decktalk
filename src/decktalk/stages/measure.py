"""Stage 4: find narration t=0 in each recording, and sanity-check the recordings.

measure: the last magenta frame plus one frame is where audio t=0 belongs; the
assembler trims that much off the head of the video. Without a marker the fallback is
the first painted frame plus the settle; failing that a fixed guess.

check: duration against what was requested, and luma at 10/50/90 %, so a black or
truncated recording is caught before assembly, plus what the recorder saw in the recording log.

    certain     PAGE ERROR  the page threw, or never exposed the runtime catalog
                STALLED     page frames froze for longer than stall_ms
                TRUNCATED   the recording is shorter than requested
                NO COVER    no magenta cover was found, so the alignment is a guess
    uncertain   BLACK?      the middle frame is dark, which a dark slide can be on purpose
                KATEX?      the page's equations may never have been typeset

A plain `decktalk build` stops only on PAGE ERROR, because a page that threw recorded
nothing worth assembling.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..artifacts import RecordingLog
from ..config import RecordConfig
from ..errors import MissingInputError
from ..media import ffmpeg
from ..project import Project

log = logging.getLogger(__name__)


def recordings(project: Project, only: list[int] | None = None) -> list[Path]:
    keys = {f"{k:02d}" for k in only} if only else None
    files = sorted(project.recordings_dir.glob("[0-9][0-9].webm"))
    files = [f for f in files if keys is None or f.name[:2] in keys]
    if not files:
        raise MissingInputError(f"no recordings in {project.recordings_dir}; run `decktalk record` first")
    return files


def is_magenta(f: ffmpeg.FrameStats, cfg: RecordConfig) -> bool:
    return (
        cfg.cover_luma_min < f.yavg < cfg.cover_luma_max
        and f.uavg > cfg.cover_chroma_min
        and f.vavg > cfg.cover_chroma_min
    )


def measure_lead(webm: Path, settle: float, cfg: RecordConfig) -> tuple[float, str]:
    """(trim point, method). The page is magenta until the narration clock starts, so the
    first clean frame after the magenta run is narration t=0."""
    rows = ffmpeg.frame_stats(webm, cfg.cover_scan_seconds)
    if not rows:
        return round(cfg.fallback_first_paint_seconds + settle, 3), "no frames read; fallback"
    frame_dt = 0.04
    if len(rows) > 1:
        frame_dt = max(0.02, (rows[-1].pts - rows[0].pts) / (len(rows) - 1))
    magenta = [f.pts for f in rows if is_magenta(f, cfg)]
    if magenta:
        return round(magenta[-1] + frame_dt, 3), f"cover ({len(magenta)} magenta frames)"
    painted = [f.pts for f in rows if f.ymax > cfg.painted_ymax and f.yavg < cfg.painted_yavg_max]
    if painted:
        return round(painted[0] + settle, 3), "NO COVER: first paint + settle"
    return round(cfg.fallback_first_paint_seconds + settle, 3), "NO COVER: fallback guess + settle"


@dataclass
class LeadMeasurement:
    key: str
    t0_seconds: float
    wallclock_seconds: float
    method: str


def recording_hash(path: Path) -> str:
    """The first 16 hex digits of the file's sha256, which ties a measurement to one recording."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def stale_measure(webm: Path, recording_log: RecordingLog | None, root: Path | None = None) -> str | None:
    """Why the recording log's narration t=0 does not belong to this recording, or None when it does.

    `record` writes a recording log with no measurement, and `measure` fills it in with the hash of
    the webm it read.
    """
    name = webm.relative_to(root).as_posix() if root is not None and webm.is_relative_to(root) else webm.name
    if recording_log is None:
        return f"{name} has no recording log, so `measure` never found its narration t=0"
    if recording_log.t0_seconds is None:
        return (
            f"{name} was never measured, so the cut would trim the recorder's wall-clock estimate "
            f"of {recording_log.clock_start_seconds:g}s"
        )
    if recording_log.t0_hash != recording_hash(webm):
        return f"{name} changed after `measure` read it"
    return None


def measure(project: Project, only: list[int] | None = None) -> list[LeadMeasurement]:
    cfg = project.settings.record
    out: list[LeadMeasurement] = []
    for webm in recordings(project, only):
        log_path = webm.with_suffix(".json")
        recording_log = RecordingLog.load(log_path) or RecordingLog(
            url="",
            requested_seconds=0,
            settle_seconds=cfg.settle_seconds,
            load_seconds=0,
            clock_start_seconds=0,
        )
        lead_in, method = measure_lead(webm, recording_log.settle_seconds, cfg)
        recording_log.t0_seconds = lead_in
        recording_log.t0_method = method
        recording_log.t0_hash = recording_hash(webm)
        recording_log.save(log_path)
        out.append(
            LeadMeasurement(
                key=webm.name[:2],
                t0_seconds=lead_in,
                wallclock_seconds=recording_log.clock_start_seconds,
                method=method,
            )
        )
        if method.startswith("NO COVER"):
            log.warning("[lead] %s  no magenta cover found; alignment is a guess (%s)", webm.name[:2], method)
        else:
            log.info("[lead] %s  trim %.3fs  (%s)", webm.name[:2], lead_in, method)
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
    verdict: (
        str  # "ok", or any of "NO COVER", "BLACK?", "TRUNCATED", "KATEX?", "STALLED", "PAGE ERROR" joined by spaces
    )
    page_errors: list[str] = field(default_factory=list)  # from the recording log, one line each
    file: Path | None = None  # The recording that was checked.

    @property
    def ok(self) -> bool:
        return self.verdict == "ok"

    def to_dict(self, root: Path) -> dict[str, Any]:
        """The row as JSON-ready data, with the verdict codes as a list and the stall length as its own number."""
        codes, stall_ms = split_verdicts(self.verdict)
        file = None
        if self.file is not None:
            file = self.file.relative_to(root).as_posix() if self.file.is_relative_to(root) else self.file.as_posix()
        return {
            "key": self.key,
            "file": file,
            "duration": round(self.duration, 3),
            "wanted": round(self.wanted, 3),
            "y10": round(self.y10, 2),
            "y50": round(self.y50, 2),
            "y90": round(self.y90, 2),
            "max50": round(self.max50, 2),
            "verdicts": codes,
            "stall_ms": stall_ms,
            "page_errors": list(self.page_errors),
        }


_VERDICT_RE = re.compile(r"PAGE ERROR|NO COVER|TRUNCATED|STALLED(?: (?P<ms>\d+)ms)?|BLACK\?|KATEX\?")


def split_verdicts(verdict: str) -> tuple[list[str], int | None]:
    """The joined verdict as codes with the stall split out: "NO COVER STALLED 140ms" gives two codes and 140."""
    codes: list[str] = []
    stall_ms: int | None = None
    for m in _VERDICT_RE.finditer(verdict):
        code = m.group(0)
        if code.startswith("STALLED"):
            code = "STALLED"
            stall_ms = int(m.group("ms")) if m.group("ms") else None
        codes.append(code)
    return codes, stall_ms


def log_verdicts(recording_log: RecordingLog | None, cfg: RecordConfig) -> list[str]:
    """The verdicts that come from what the recorder saw rather than from the frames."""
    if recording_log is None:
        return []
    out: list[str] = []
    if recording_log.t0_method and recording_log.t0_method.startswith("NO COVER"):
        out.append("NO COVER")
    if recording_log.page_errors:
        out.append("PAGE ERROR")
    if any("katex" in w.lower() or "data-tex" in w.lower() for w in recording_log.warnings):
        out.append("KATEX?")
    if recording_log.worst_stall_ms > cfg.stall_ms:
        out.append(f"STALLED {recording_log.worst_stall_ms}ms")
    return out


def check(project: Project, only: list[int] | None = None) -> list[RecordingCheck]:
    cfg = project.settings.record
    out: list[RecordingCheck] = []
    for f in recordings(project, only):
        recording_log = RecordingLog.load(f.with_suffix(".json"))
        wanted = recording_log.requested_seconds if recording_log else 0.0
        dur = ffmpeg.probe_duration(f)
        y10, y50, y90 = (ffmpeg.luma_at(f, dur * k)[0] for k in (0.10, 0.50, 0.90))
        max50 = ffmpeg.luma_at(f, dur * 0.5)[1]
        verdicts = []
        if max50 < cfg.black_ymax:
            verdicts.append("BLACK?")
        if wanted and dur < wanted - cfg.truncated_slack_seconds:
            verdicts.append("TRUNCATED")
        verdicts += log_verdicts(recording_log, cfg)
        errors = list(recording_log.page_errors) if recording_log else []
        row = RecordingCheck(f.name[:2], dur, wanted, y10, y50, y90, max50, " ".join(verdicts) or "ok", errors, file=f)
        if not row.ok:
            log.warning("[chk ] %s  %s", row.key, row.verdict)
        for e in errors:
            log.warning("[chk ] %s  page error: %s", row.key, e)
        out.append(row)
    return out
