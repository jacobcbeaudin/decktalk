"""EBU R128 loudness: measure, apply one gain, limit the true peaks, and measure again.

A plain gain keeps the mix's dynamics intact and the limiter only touches the peaks that would cross
the ceiling. The limiter runs oversampled, so inter-sample peaks are caught, which is what a
true-peak ceiling promises. The result is measured again, and a miss is a row of its own with the film it is about.
"""

from __future__ import annotations

from pathlib import Path

from ...jsonio import relative
from ...media import audio, ffmpeg
from ...media.encode import Encoder
from ...model import Project
from ...verdicts import Finding, Verdict
from .mix import db

LIMITER_HEADROOM_DB = 0.3  # The limiter works on oversampled samples, so it sits a little under the ceiling.
LIMITER_OVERSAMPLE_RATE = 192000  # The true-peak limiter runs at this rate and resamples back afterwards.
LOUDNESS_TOLERANCE_LU = 1.0  # The measured result may sit this far from the integrated target.


def normalize_loudness(project: Project, src: Path, dst: Path) -> tuple[audio.Loudness, audio.Loudness]:
    """Gain to the integrated target, then a true-peak limiter at the ceiling. Returns (before, after).

    A plain gain keeps the mix's dynamics intact, and the limiter only touches peaks that
    would cross the ceiling. It runs oversampled so inter-sample peaks are caught, which
    is what a true-peak ceiling promises.
    """
    ln = project.mix.loudness
    enc = Encoder(project.settings.video)
    before = audio.measure_loudness(src, i=ln.target_lufs, tp=ln.true_peak_db, lra=ln.range_lu)
    gain = ln.target_lufs - before.i
    ceiling = db(ln.true_peak_db - LIMITER_HEADROOM_DB)
    ffmpeg.run(
        "-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
        "-af",
        f"volume={gain:.2f}dB,aresample={LIMITER_OVERSAMPLE_RATE},"
        f"alimiter=limit={ceiling:.4f}:attack=5:release=50:level=false,aresample={enc.v.sample_rate}",
        *enc.aenc, "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    after = audio.measure_loudness(dst, i=ln.target_lufs, tp=ln.true_peak_db, lra=ln.range_lu)
    return before, after


def loudness_problems(project: Project, after: audio.Loudness) -> list[Finding]:
    """What is wrong with the normalized result, if anything: a peak over the ceiling or a missed target.

    Each one is a row rather than a sentence, because the pass reports a miss it cannot fix and a
    reader decides what to do about it. The row is about the finished film, which is the file whose
    loudness was measured.
    """
    ln = project.mix.loudness
    film = relative(project.final, project.root)
    rows: list[Finding] = []
    if after.tp > ln.true_peak_db:
        rows.append(
            Finding(
                detail=f"true peak {after.tp:.1f} dBTP is above the {ln.true_peak_db:.1f} dBTP ceiling",
                verdict=Verdict.LOUDNESS_MISS,
                where=film,
            )
        )
    if abs(after.i - ln.target_lufs) > LOUDNESS_TOLERANCE_LU:
        rows.append(
            Finding(
                detail=(
                    f"integrated loudness {after.i:.1f} LUFS is {abs(after.i - ln.target_lufs):.1f} LU "
                    f"from the {ln.target_lufs:.1f} LUFS target"
                ),
                verdict=Verdict.LOUDNESS_MISS,
                where=film,
            )
        )
    return rows
