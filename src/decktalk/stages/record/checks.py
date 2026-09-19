"""What one finished recording is judged on, before anything is assembled from it.

The frames say how long the recording ran and how bright it is, so a black or truncated section is
caught while the page is still on screen rather than in the final mp4. The recording log says what
the page itself reported, which is an uncaught exception, a missing runtime catalog, a frozen
reveal, or equations that were never typeset.

    certain     PAGE ERROR         the page threw, or never exposed the runtime catalog
                STALLED            page frames froze for longer than stall_ms
                TRUNCATED          the recording is shorter than requested
                NO COVER           no magenta cover was found, so narration t=0 is a guess
    uncertain   BLACK?             the middle frame is dark, which a dark slide can be on purpose
                KATEX?             the page's equations may never have been typeset

A plain `decktalk build` stops only on PAGE ERROR, because a page that threw recorded nothing worth
assembling. Every other verdict is a finding the tables and `--json` carry.
"""

from __future__ import annotations

from pathlib import Path

from ...artifacts import Luma, RecordingChecks, RecordingLog
from ...media import ffmpeg, frames
from ...settings import RecordConfig
from ...verdicts import Verdict


def katex_verdicts(warnings: list[str]) -> list[Verdict]:
    """The KaTeX verdicts a page's own warnings carry, at most one of each."""
    lowered = [w.lower() for w in warnings]
    return [Verdict.KATEX_UNSURE] if any("katex" in w or "data-tex" in w for w in lowered) else []


def log_verdicts(recording_log: RecordingLog, cfg: RecordConfig) -> list[Verdict]:
    """The verdicts that come from what the recorder and the page saw, rather than from the frames."""
    out: list[Verdict] = []
    if recording_log.t0_guessed:
        out.append(Verdict.NO_COVER)
    if recording_log.page_errors:
        out.append(Verdict.PAGE_ERROR)
    out += katex_verdicts(recording_log.warnings)
    if recording_log.worst_stall_ms > cfg.stall_ms:
        out.append(Verdict.STALLED)
    return out


def measure_luma(webm: Path, duration: float) -> Luma:
    """The recording's brightness at a tenth, a half and nine tenths of its length."""
    y10, y50, y90 = (frames.luma_at(webm, duration * k)[0] for k in (0.10, 0.50, 0.90))
    return Luma(y10=y10, y50=y50, y90=y90, max50=frames.luma_at(webm, duration * 0.5)[1])


def check_recording(webm: Path, recording_log: RecordingLog, cfg: RecordConfig) -> RecordingChecks:
    """Judge one finished recording against its own log, for the log to carry."""
    duration = ffmpeg.probe_duration(webm)
    luma = measure_luma(webm, duration)
    wanted = recording_log.requested_seconds
    verdicts: list[Verdict] = []
    if luma.max50 < cfg.black_ymax:
        verdicts.append(Verdict.BLACK_UNSURE)
    if wanted and duration < wanted - cfg.truncated_slack_seconds:
        verdicts.append(Verdict.TRUNCATED)
    verdicts += log_verdicts(recording_log, cfg)
    return RecordingChecks(duration_seconds=duration, wanted_seconds=wanted, luma=luma, verdicts=tuple(verdicts))


def label(checks: RecordingChecks | None, stall_ms: int) -> str:
    """Every verdict as one line, with the stall length beside STALLED, as the tables print it."""
    verdicts = checks.verdicts if checks else ()
    parts = [f"{v} {stall_ms}ms" if v is Verdict.STALLED and stall_ms else str(v) for v in verdicts]
    return " ".join(parts) or str(Verdict.OK)
