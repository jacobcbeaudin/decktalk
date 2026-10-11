"""How many recorders work at once, which is the size of the pool `record` hands chunks to, and the chunks.

Frame `k` of a section is the page at second `k / fps` however it was reached, so a section's frames
split into chunks that several recorders record at once, each a worker of `decktalk.stages.pool`
with a Chromium of its own. A recorder keeps several cores busy, so the number at once is chosen from
the CPU this process may really use, which inside a container is its quota and not the host's core
count. A recorder that gets fewer cores than it would keep busy records slower and never differently.
`[record] concurrency` names the number, and zero chooses it here.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from decktalk.media.stepping import expected_seconds
from decktalk.settings import RecordConfig

log = logging.getLogger(__name__)

CPUS_PER_RECORDING = 3
"""Calibration: the CPUs one recorder keeps busy, rounded up so recorders at once do not starve each other.

With the capture switches every Chromium is launched with, no frame waits for a display refresh, so
the page, the compositor and the encoder of one recording rarely rest. Recording the lesson's 114
second section on an 18 core Mac took 124 CPU seconds in 53 seconds, about 2.3 cores, where the same
recording without the switches took 79 CPU seconds in 97 seconds. A recording that has fewer cores
than it would keep busy records slower and never differently, because the recorder steps the clock.
"""

MOST_AT_ONCE = 4
"""Calibration: the most recorders an automatic choice runs at once, because each one is a Chromium in memory.

Four recordings held about 900 MB beside each other, which is what a small render worker can spare,
and a machine with more to give says so through `[record] concurrency`.
"""


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


def at_once(requested: int, *, cpus: float) -> int:
    """How many recorders to run at once: what the machine asked for, or one per `CPUS_PER_RECORDING`, at least one.

    `requested` is `[record] concurrency`, and zero asks this function to choose.
    """
    return max(1, requested or min(MOST_AT_ONCE, int(cpus // CPUS_PER_RECORDING)))


def automatic(requested: int) -> int:
    """`at_once` for this machine, which reads its own CPUs, and records what it chose.

    How many recorders ran is the first thing an operator asks about a slow container, so the choice
    is recorded beside the CPUs it was made from.
    """
    cpus = available_cpus()
    chosen = at_once(requested, cpus=cpus)
    log.debug(
        "%d recorders run at once.",
        chosen,
        extra={"data": {"workers": chosen, "cpus": cpus, "requested": requested}},
    )
    return chosen


@dataclass(frozen=True)
class Span:
    """One section a run records: its frames, the steps recording it is expected to take, and whether it splits.

    A page steps once a frame, except across a still stretch, which it crosses in one step, so a
    slide that mostly holds still costs far fewer steps than frames. A page section may be split
    across recorders. A section a command draws is one run of that command, so it is one chunk.
    """

    section: int
    frames: int
    steps: int
    splits: bool

    @property
    def steps_a_frame(self) -> float:
        """How many steps each frame is expected to cost, which is one where every frame is a step."""
        return self.steps / self.frames if self.frames else 1.0


@dataclass(frozen=True)
class Chunk:
    """Frames `first` up to `stop` of one section, which one recorder records into one piece."""

    section: int
    first: int
    stop: int
    steps: float
    """The steps this chunk is expected to take, which is its share of its section's."""

    @property
    def frames(self) -> range:
        """The frames of the section this chunk records, in order."""
        return range(self.first, self.stop)


def chunk_count(span: Span, workers: int, record: RecordConfig) -> int:
    """How many chunks a section is split into on `workers` recorders.

    `sqrt(c*W*N/s)` rounded, where `W` is the steps the section is expected to take and `c` and `s`
    are the machine's `[record] step_seconds` and `chunk_startup_seconds`, never more than the
    recorders, never so many that a chunk holds fewer than `chunk_frames_min` frames, and never fewer
    than one.
    """
    best = round(math.sqrt(record.step_seconds * span.steps * workers / record.chunk_startup_seconds))
    return max(1, min(best, workers, span.frames // record.chunk_frames_min))


def chunked(spans: Sequence[Span], workers: int, record: RecordConfig) -> list[Chunk]:
    """Every chunk of every section, in the order recorders are handed them: the dearest first.

    A section is cut into nearly equal ranges of frames, each expected to cost its share of the
    section's steps. Chunks are handed out by that cost, the longest processing time first, and
    chunks of equal cost go in section order and then frame order.
    """
    chunks: list[Chunk] = []
    for span in spans:
        count = chunk_count(span, workers, record) if span.splits else 1
        bounds = [round(span.frames * at / count) for at in range(count + 1)]
        chunks += [
            Chunk(span.section, first, stop, (stop - first) * span.steps_a_frame)
            for first, stop in zip(bounds, bounds[1:], strict=False)
        ]
    return sorted(chunks, key=lambda chunk: (-chunk.steps, chunk.section, chunk.first))


def run_deadline(chunks: Sequence[Chunk], workers: int, record: RecordConfig) -> float:
    """The longest a whole `record` run of `chunks` on `workers` recorders may take before it is stopped.

    Handed out longest first, the chunks finish within their expected time spread over the recorders
    plus the longest of them, and the run may take `[record] deadline_slowdown` times that, plus one
    page deadline for the call in flight when it runs out.
    """
    costs = [expected_seconds(record, chunk.stop - chunk.first) for chunk in chunks]
    spread = sum(costs) / max(1, workers) + max(costs, default=0.0)
    return record.page_deadline_seconds + record.deadline_slowdown * spread


__all__ = [
    "Chunk",
    "Span",
    "at_once",
    "automatic",
    "available_cpus",
    "chunk_count",
    "chunked",
    "run_deadline",
]
