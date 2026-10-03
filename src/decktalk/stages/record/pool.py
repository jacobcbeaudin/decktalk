"""How many page sections record at once, which is the size of the pool `record` hands them to.

A recording waits for its section's whole span in real time, so a film's recording took as long as
the film, and the sections of one film share nothing but the project they read. Several of them are
therefore recorded at once, each by a worker of `decktalk.stages.pool` with a Chromium of its own.

A recording is also the one stage that needs its CPU on time. A reveal that lands a frame late is a
finding, so the number at once is chosen from the CPU this process may really use, which inside a
container is its quota and not the host's core count. `[record] concurrency` names the number, and
zero chooses it here.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

CPUS_PER_RECORDING = 2
"""Calibration: the CPUs one recording keeps busy while it presents its frames on time.

The page, the compositor and the encoder of one Chromium kept about two cores of a Linux container
busy, and three recordings at once in four CPUs still landed every reveal inside the offset limit.
"""

MOST_AT_ONCE = 4
"""Calibration: the most sections an automatic choice records at once, because each one is a Chromium in memory.

Four recordings held about 900 MB beside each other, which is what a small render worker can spare,
and a machine with more to give says so through `[record] concurrency`.
"""

WINDOWS_AT_ONCE = 1
"""Calibration: Windows records one section at a time until its capture is measured under load."""

CGROUP_V2_MAX = Path("/sys/fs/cgroup/cpu.max")
"""Truth: where a cgroup v2 container states its CPU quota and period, or `max` for none."""

CGROUP_V1_QUOTA = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CGROUP_V1_PERIOD = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
"""Truth: where a cgroup v1 container states its CPU quota, which is -1 for none, and its period."""


def _quota(text: str) -> float | None:
    """CPUs from one `quota period` pair, or None when the pair states no limit."""
    parts = text.split()
    if len(parts) != 2 or parts[0] in ("max", "-1"):
        return None
    try:
        quota, period = float(parts[0]), float(parts[1])
    except ValueError:
        # silent: a quota that is not two numbers states no limit.
        return None
    return quota / period if quota > 0 and period > 0 else None


def _read(path: Path) -> str | None:
    """The text of one kernel file, or None where this machine has no such file."""
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        # silent: a machine without the kernel file has no quota.
        return None


def available_cpus() -> float:
    """The CPUs this process may use: the fewer of its container's quota and the cores it may run on.

    A container that names no quota, and every machine that is not a Linux container, is limited by
    its cores alone.
    """
    affinity = getattr(os, "sched_getaffinity", None)
    cores = float(len(affinity(0))) if affinity is not None else float(os.cpu_count() or 1)
    v2 = _read(CGROUP_V2_MAX)
    v1 = _read(CGROUP_V1_QUOTA)
    period = _read(CGROUP_V1_PERIOD)
    stated = _quota(v2) if v2 is not None else _quota(f"{v1} {period}") if v1 and period else None
    return min(cores, stated) if stated is not None else cores


def at_once(requested: int, jobs: int, *, cpus: float, windows: bool) -> int:
    """How many sections to record at once: what the machine asked for, or one per two CPUs, never more than the jobs.

    `requested` is `[record] concurrency`, and zero asks this function to choose.
    """
    if requested:
        chosen = requested
    elif windows:
        chosen = WINDOWS_AT_ONCE
    else:
        chosen = min(MOST_AT_ONCE, int(cpus // CPUS_PER_RECORDING))
    return max(1, min(chosen, jobs))


def automatic(requested: int, jobs: int) -> int:
    """`at_once` for this machine, which reads its own CPUs and platform, and records what it chose.

    How many recorders ran is the first thing an operator asks about a slow container, so the choice
    is recorded beside the CPUs it was made from.
    """
    cpus = available_cpus()
    chosen = at_once(requested, jobs, cpus=cpus, windows=sys.platform == "win32")
    log.debug(
        "%d sections are recorded on %d workers.",
        jobs,
        chosen,
        extra={"data": {"workers": chosen, "jobs": jobs, "cpus": cpus, "requested": requested}},
    )
    return chosen


__all__ = ["at_once", "automatic", "available_cpus"]
