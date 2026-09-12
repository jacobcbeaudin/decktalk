"""Measure each recording's lead-in: where the page's t=0 sits in the webm.

The recorder flashes the whole frame magenta for ~120 ms exactly when the narration
clock starts. The last magenta frame plus one frame is where audio t=0 belongs, so
the assembler trims that much off the head of the video (lead_in_seconds in the
sidecar build/rec/NN-scene.json). Without a marker the fallback is the first painted
frame plus the recorded settle; failing that 1.1 s plus settle.

Also the recording sanity check (`cuecut check`): duration against what was asked
for, and luma at 10/50/90 % so a black or truncated recording is caught before
assembly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .project import Project
from .tools import ff_stderr, ffprobe_duration

SCAN_SECONDS = 4.0
FALLBACK_FIRST_PAINT = 1.1

Frame = dict[str, float]


def frame_stats(path: Path, seconds: float = SCAN_SECONDS) -> list[Frame]:
    out = ff_stderr("-t", str(seconds), "-i", str(path), "-vf", "signalstats,metadata=print", "-f", "null", "-")
    frames: list[Frame] = []
    cur: Frame | None = None
    for line in out.splitlines():
        m = re.search(r"pts_time:([0-9.]+)", line)
        if m:
            cur = {"pts": float(m.group(1))}
            frames.append(cur)
            continue
        mm = re.search(r"lavfi\.signalstats\.(YAVG|UAVG|VAVG|YMAX)=([0-9.]+)", line)
        if mm and cur is not None:
            cur[mm.group(1)] = float(mm.group(2))
    return [f for f in frames if "YAVG" in f]


def is_magenta(y: float, u: float, v: float) -> bool:
    return 70 < y < 140 and u > 165 and v > 165


def measure_one(webm: Path, settle: float) -> tuple[float, str]:
    rows = frame_stats(webm)
    if not rows:
        return round(FALLBACK_FIRST_PAINT + settle, 3), "no frames read; fallback"
    frame_dt = 0.04
    if len(rows) > 1:
        frame_dt = max(0.02, (rows[-1]["pts"] - rows[0]["pts"]) / (len(rows) - 1))
    magenta = [f["pts"] for f in rows if is_magenta(f["YAVG"], f.get("UAVG", 128), f.get("VAVG", 128))]
    if magenta:
        return round(magenta[-1] + frame_dt, 3), f"marker ({len(magenta)} magenta frames)"
    painted = [f["pts"] for f in rows if f.get("YMAX", 0) > 60 and f["YAVG"] < 120]
    if painted:
        return round(painted[0] + settle, 3), "first paint + settle (no marker)"
    return round(FALLBACK_FIRST_PAINT + settle, 3), "fallback 1.1 s + settle"


def recordings(project: Project, only: list[int] | None = None) -> list[Path]:
    keys = {f"{k:02d}" for k in only} if only else None
    files = sorted(project.rec_dir.glob("[0-9][0-9]-scene.webm"))
    return [f for f in files if keys is None or f.name[:2] in keys]


def measure(project: Project, only: list[int] | None = None) -> int:
    files = recordings(project, only)
    if not files:
        raise SystemExit(f"error: no recordings in {project.rec_dir} (run `cuecut record`)")
    print(f"{'sec':>3} {'lead_in':>8} {'wallclock':>9}  method")
    for webm in files:
        sidecar = webm.with_suffix(".json")
        side = json.loads(sidecar.read_text()) if sidecar.exists() else {}
        settle = float(side.get("settle_seconds", 0.5))
        lead_in, method = measure_one(webm, settle)
        side["lead_in_seconds"] = lead_in
        side["lead_method"] = method
        sidecar.write_text(json.dumps(side, indent=2) + "\n")
        print(f"{webm.name[:2]:>3} {lead_in:>8.3f} {side.get('lead_seconds', 0):>9.3f}  {method}")
    return 0


def luma_at(path: Path, t: float, key: str) -> float:
    out = ff_stderr(
        "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
        "-vf", f"signalstats,metadata=print:key=lavfi.signalstats.{key}", "-f", "null", "-",
    )  # fmt: skip
    m = re.search(rf"{key}=([0-9.]+)", out)
    return float(m.group(1)) if m else 0.0


def check(project: Project, only: list[int] | None = None) -> int:
    """Duration and luma sanity per recording. Returns the number of suspect recordings."""
    files = recordings(project, only)
    if not files:
        raise SystemExit(f"error: no recordings in {project.rec_dir} (run `cuecut record`)")
    print(f"{'sec':<4} {'webm_s':<8} {'want_s':<8} {'Y10':<6} {'Y50':<6} {'Y90':<6} {'MAX50':<6}  verdict")
    bad = 0
    for f in files:
        side = json.loads(f.with_suffix(".json").read_text()) if f.with_suffix(".json").exists() else {}
        want = float(side.get("requested_seconds", 0))
        dur = ffprobe_duration(f)
        y10, y50, y90 = (luma_at(f, dur * k, "YAVG") for k in (0.10, 0.50, 0.90))
        m50 = luma_at(f, dur * 0.5, "YMAX")
        verdict = "ok"
        if m50 < 40:
            verdict = "BLACK?"
        if want and dur < want - 0.5:
            verdict = ("" if verdict == "ok" else verdict + " ") + "TRUNCATED"
        bad += verdict != "ok"
        print(f"{f.name[:2]:<4} {dur:<8.1f} {want:<8.1f} {y10:<6.0f} {y50:<6.0f} {y90:<6.0f} {m50:<6.0f}  {verdict}")
    return bad
