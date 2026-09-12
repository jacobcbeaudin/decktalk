"""Checks on the assembled mp4.

starts   every section opens on a real frame: at (section start + 0.2 s), past the
         dip-to-black, YMAX > 60 means content is on screen.
cues     for each SECTION:CUE, the picture changes across the cue: mean |frame(t) -
         frame(t - 1.2 s)| at t = section start + cue + 0.7 s, compared with the same
         measure over a control window just before the cue. "changed" if the
         difference is > max(1.0, 2 x control).

Section starts are the cumulative lengths of build/out/NN-section.mp4, the same
arithmetic the assembler uses.
"""

from __future__ import annotations

import re
from pathlib import Path

from .beats import parse_beats_string
from .project import Project
from .tools import ff_stderr, ffprobe_duration

AFTER_DIP = 0.2
AFTER_CUE = 0.7
BEFORE = 1.2


def section_starts(project: Project) -> tuple[dict[str, float], float]:
    starts: dict[str, float] = {}
    t = 0.0
    for f in sorted(project.out_dir.glob("[0-9][0-9]-section.mp4")):
        starts[f.name[:2]] = t
        t += ffprobe_duration(f)
    return starts, t


def frame_stats(final: Path, t: float) -> tuple[float, float]:
    err = ff_stderr(
        "-ss", f"{t:.3f}", "-i", str(final), "-frames:v", "1", "-vf", "signalstats,metadata=print", "-f", "null", "-"
    )
    yavg = re.search(r"YAVG=([0-9.]+)", err)
    ymax = re.search(r"YMAX=([0-9.]+)", err)
    return (float(yavg.group(1)) if yavg else 0.0, float(ymax.group(1)) if ymax else 0.0)


def frame_change(final: Path, t: float) -> float:
    """Mean absolute luma difference between the frame at t and the frame at t - BEFORE."""
    fc = (
        f"[0:v]trim=start={t - BEFORE:.3f}:duration=0.05,setpts=PTS-STARTPTS,scale=480:270[a];"
        f"[1:v]trim=start={t:.3f}:duration=0.05,setpts=PTS-STARTPTS,scale=480:270[b];"
        "[a][b]blend=all_mode=difference,signalstats,metadata=print"
    )
    err = ff_stderr("-i", str(final), "-i", str(final), "-filter_complex", fc, "-frames:v", "1", "-f", "null", "-")
    m = re.search(r"YAVG=([0-9.]+)", err)
    return float(m.group(1)) if m else 0.0


def verify_starts(project: Project) -> int:
    final = project.final
    starts, total = section_starts(project)
    if not starts or not final.exists():
        raise SystemExit("error: need build/out/NN-section.mp4 files and the final mp4 (run `decktalk assemble`)")
    print(f"{'sec':>3} {'start':>8} {'probe':>8} {'YAVG':>6} {'YMAX':>6}  result")
    bad = 0
    for key, t in starts.items():
        yavg, ymax = frame_stats(final, t + AFTER_DIP)
        ok = ymax > 60
        bad += 0 if ok else 1
        print(f"{key:>3} {t:>8.2f} {t + AFTER_DIP:>8.2f} {yavg:>6.0f} {ymax:>6.0f}  {'ok' if ok else 'BLACK'}")
    print(f"total {total:.2f}s; {bad} black section start(s)")
    return bad


def verify_cues(project: Project, checks: list[str]) -> int:
    final = project.final
    starts, _total = section_starts(project)
    beats = project.beats_data()
    print(f"{'check':<18} {'cue':>6} {'t_final':>8} {'change':>7} {'control':>7}  result")
    bad = 0
    for check in checks:
        if ":" not in check:
            print(f"{check:<18} malformed (want SECTION:CUE)")
            bad += 1
            continue
        sec, step = check.split(":", 1)
        key = f"{int(sec):02d}"
        cues = parse_beats_string(beats.get(key, ""))
        if step not in cues or key not in starts:
            print(f"{check:<18} {'-':>6} {'-':>8} {'-':>7} {'-':>7}  MISSING")
            bad += 1
            continue
        tf = starts[key] + cues[step] + AFTER_CUE
        chg = frame_change(final, tf)
        ctl = frame_change(final, tf - BEFORE - 0.3)
        landed = chg > max(1.0, 2.0 * ctl)
        bad += 0 if landed else 1
        print(
            f"{check:<18} {cues[step]:>6.2f} {tf:>8.2f} {chg:>7.1f} {ctl:>7.1f}  {'changed' if landed else 'NO CHANGE'}"
        )
    return bad


def verify(project: Project, checks: list[str] | None = None) -> int:
    bad = verify_starts(project)
    if checks:
        print()
        bad += verify_cues(project, checks)
    return 1 if bad else 0
