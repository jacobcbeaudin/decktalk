"""What one finished recording is judged on, before anything is assembled from it.

The frames say how long the recording ran and how bright it is, so a black or truncated section is
caught while the page is still on screen rather than in the final mp4. The steps say where a script
changed the picture with nothing declared to explain it. The page says what it could not honour,
and every one of those arrives as a code the media layer already validated, so nothing here reads a
sentence to work out what happened.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import Luma, RecordingChecks
from decktalk.findings import Code, Finding, Location, judge
from decktalk.media import ffmpeg, frames
from decktalk.media.pagereport import Recording
from decktalk.pagescan import asset_findings, page_findings
from decktalk.pipeline import Stage
from decktalk.settings import Settings
from decktalk.settings.numbers import truncated_slack_seconds

LUMA_POINTS = (0.1, 0.5, 0.9)
"""Derived: a tenth, a half and nine tenths of a recording, which is where its brightness is read."""


def measure_luma(video: Path, duration: float) -> Luma:
    """The recording's brightness at a tenth, a half and nine tenths of its length.

    One read of each frame gives both its mean and its peak, so the middle frame is decoded once.
    """
    (tenth, _), (half, peak), (nine_tenths, _) = (frames.luma_at(video, duration * point) for point in LUMA_POINTS)
    return Luma(at_tenth=tenth, at_half=half, at_nine_tenths=nine_tenths, peak_at_half=peak)


def check_recording(video: Path, recording: Recording) -> RecordingChecks:
    """What the frames of one finished recording measure, against what the recorder asked for."""
    duration = ffmpeg.probe_duration(video)
    return RecordingChecks(
        duration_seconds=duration,
        wanted_seconds=recording.requested_seconds,
        luma=measure_luma(video, duration),
    )


def frame_findings(checks: RecordingChecks, *, where: Path, section: int, settings: Settings) -> list[Finding]:
    """One judgement per way the frames of a recording fall short of what the recorder asked for."""
    black = settings.verify.black_max_luma
    slack = truncated_slack_seconds(settings)
    found: list[Finding] = []
    if checks.luma.peak_at_half <= black:
        found.append(
            judge(
                Code.RECORD_BLACK,
                f"the frame half way through has a brightest luma of {checks.luma.peak_at_half:.1f}, "
                f"which is at or under the {black:.1f} a black frame is.",
                Location(where=where.as_posix(), file=where, section=section),
                stage=Stage.RECORD,
            )
        )
    short = checks.wanted_seconds - checks.duration_seconds
    if checks.wanted_seconds and short > slack:
        found.append(
            judge(
                Code.RECORD_TRUNCATED,
                f"the recording runs {checks.duration_seconds:.2f}s of the "
                f"{checks.wanted_seconds:.2f}s it asked for, which is "
                f"{short:.2f}s short against the {slack:.2f}s allowed.",
                Location(where=where.as_posix(), file=where, section=section),
                stage=Stage.RECORD,
            )
        )
    return found


def change_findings(recording: Recording, *, page: str, section: int) -> list[Finding]:
    """One judgement per element a script changed the picture in with nothing declared to explain it.

    Verify masks what the page declared and reads every other change as a reveal, so a change the
    page made outside every cue, motion, loop and frame hook is either hidden by a mask or read as a
    reveal nobody cued.
    """
    return [
        judge(
            Code.PAGE_CHANGE_UNDECLARED,
            f"{row.what} changed the picture at {row.at:.2f}s, on {row.frames} "
            f"{'frame' if row.frames == 1 else 'frames'} in all, with no cue, motion, loop or frame hook to "
            "explain it.",
            Location(where=page, file=Path(page), section=section),
            stage=Stage.RECORD,
        )
        for row in recording.undeclared
    ]


def recording_findings(
    recording: Recording,
    checks: RecordingChecks,
    *,
    page: str,
    where: Path,
    section: int,
    settings: Settings,
) -> tuple[Finding, ...]:
    """Every judgement one finished recording carries, from the page, the frames, the steps and the network.

    They are one list rather than three, so a reader of the log dispatches on a code and never on
    which of three places a sentence came from.
    """
    return (
        *page_findings(recording.report, page=page, section=section, stage=Stage.RECORD),
        *frame_findings(checks, where=where, section=section, settings=settings),
        *change_findings(recording, page=page, section=section),
        *asset_findings(recording.external, where=page, section=section),
    )


__all__ = [
    "LUMA_POINTS",
    "change_findings",
    "check_recording",
    "frame_findings",
    "measure_luma",
    "recording_findings",
]
