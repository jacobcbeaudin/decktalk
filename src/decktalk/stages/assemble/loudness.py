"""EBU R128 loudness: measure, apply one gain, limit the true peaks, and measure again.

A plain gain keeps the mix's dynamics intact and the limiter only touches the peaks that would cross
the ceiling. The limiter runs oversampled, so inter-sample peaks are caught, which is what a
true-peak ceiling promises. The result is measured again, and a miss is a judgement of its own about
the film it was measured on, because the pass reports what it cannot fix and a reader decides.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.findings import Code, Finding, Location
from decktalk.inputs import Inputs
from decktalk.machine import Run
from decktalk.media import audio, ffmpeg
from decktalk.pipeline import Stage
from decktalk.results import Loudness
from decktalk.stages import judge
from decktalk.stages.assemble.cut import encoder
from decktalk.stages.assemble.mix import gain

LIMITER_HEADROOM_DB = 0.3
"""Calibration: the limiter works on oversampled samples, so it sits this far under the ceiling."""

LIMITER_OVERSAMPLE_RATE = 192000
"""Truth: the rate the true-peak limiter runs at, which is where an inter-sample peak becomes a sample."""

LIMITER_ATTACK_MS = 5
"""Calibration: how quickly the limiter takes hold, which is short enough to catch one transient."""

LIMITER_RELEASE_MS = 50
"""Calibration: how quickly the limiter lets go, which is long enough not to pump on speech."""

LOUDNESS_TOLERANCE_LU = 1.0
"""Calibration: how far the measured result may sit from the integrated target and still be on target."""


def normalize_loudness(inputs: Inputs, src: Path, dst: Path) -> tuple[audio.Loudness, audio.Loudness]:
    """Gain to the integrated target, then a true-peak limiter at the ceiling, measured either side.

    A plain gain keeps the mix's dynamics intact, and the limiter only touches peaks that would cross
    the ceiling. It runs oversampled so inter-sample peaks are caught, which is what a true-peak
    ceiling promises.
    """
    loudness = inputs.settings.mix.loudness
    enc = encoder(inputs)
    before = audio.measure_loudness(
        src, i=loudness.target_lufs, tp=loudness.true_peak_max_dbtp, lra=loudness.range_max_lu
    )
    lift = loudness.target_lufs - before.i
    ceiling = gain(loudness.true_peak_max_dbtp - LIMITER_HEADROOM_DB)
    ffmpeg.run(
        "-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
        "-af",
        f"volume={lift:.2f}dB,aresample={LIMITER_OVERSAMPLE_RATE},"
        f"alimiter=limit={ceiling:.4f}:attack={LIMITER_ATTACK_MS}:release={LIMITER_RELEASE_MS}:level=false,"
        f"aresample={enc.v.sample_rate}",
        *enc.aenc, "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    after = audio.measure_loudness(
        dst, i=loudness.target_lufs, tp=loudness.true_peak_max_dbtp, lra=loudness.range_max_lu
    )
    return before, after


def measured(inputs: Inputs, after: audio.Loudness) -> Loudness:
    """What the mix measures, as the result model publishes it.

    The media layer reports what one loudnorm pass read and the result carries what a reader
    receives, so the two names of each number meet here once rather than at every reader.
    """
    return Loudness(
        integrated_lufs=round(after.i, 1),
        true_peak_dbtp=round(after.tp, 1),
        range_lu=round(max(after.lra, 0.0), 1),
        target_lufs=inputs.settings.mix.loudness.target_lufs,
    )


def loudness_findings(inputs: Inputs, run: Run, after: audio.Loudness) -> list[Finding]:
    """What the normalized result missed: a peak over the ceiling, or an integrated loudness off target.

    Each one is a judgement rather than a sentence, because the pass reports a miss it cannot fix and
    a reader decides what to do about it. The subject is the finished film, which is the file whose
    loudness was measured.
    """
    loudness = inputs.settings.mix.loudness
    film = inputs.relative(inputs.workspace.film)
    where = Location(where=film.as_posix(), file=film)
    found: list[Finding] = []
    if after.tp > loudness.true_peak_max_dbtp:
        found.append(
            run.found(
                judge(
                    Code.MIX_LOUDNESS,
                    f"the true peak is {after.tp:.1f} dBTP, which is above the "
                    f"{loudness.true_peak_max_dbtp:.1f} dBTP ceiling the mix was mastered to.",
                    where,
                    stage=Stage.ASSEMBLE,
                )
            )
        )
    off = abs(after.i - loudness.target_lufs)
    if off > LOUDNESS_TOLERANCE_LU:
        found.append(
            run.found(
                judge(
                    Code.MIX_LOUDNESS,
                    f"the integrated loudness is {after.i:.1f} LUFS, which is {off:.1f} LU from the "
                    f"{loudness.target_lufs:.1f} LUFS target and over the {LOUDNESS_TOLERANCE_LU:.1f} LU "
                    "a mix may sit from it.",
                    where,
                    stage=Stage.ASSEMBLE,
                )
            )
        )
    return found


__all__ = [
    "LIMITER_HEADROOM_DB",
    "LIMITER_OVERSAMPLE_RATE",
    "LOUDNESS_TOLERANCE_LU",
    "loudness_findings",
    "measured",
    "normalize_loudness",
]
