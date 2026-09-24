"""What one finished recording is judged on, before anything is assembled from it.

The frames say how long the recording ran and how bright it is, so a black or truncated section is
caught while the page is still on screen rather than in the final mp4. The page says what it could
not honour, and every one of those arrives as a code the media layer already validated, so nothing
here reads a sentence to work out what happened. The channel this replaces was prose classified by
matching substrings, with the sentence spelled in the runtime, in the recorder and in a test.
"""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts import Luma, RecordingChecks
from decktalk.findings import Code, Finding, Location
from decktalk.media import ffmpeg, frames
from decktalk.media.browser import Recording
from decktalk.media.pagereport import PageReport
from decktalk.pagescan import asset_findings
from decktalk.pipeline import Stage
from decktalk.settings import Settings
from decktalk.stages import judge

LUMA_POINTS = (0.1, 0.5, 0.9)
"""Derived: a tenth, a half and nine tenths of a recording, which is where its brightness is read."""

LUMA_DIGITS = 1
"""How precisely a luma is written into a sentence, which is finer than a viewer can tell apart."""

SECOND_DIGITS = 2
"""How precisely a length is written into a sentence, which is under half a frame."""


def measure_luma(webm: Path, duration: float) -> Luma:
    """The recording's brightness at a tenth, a half and nine tenths of its length."""
    tenth, half, nine_tenths = (frames.luma_at(webm, duration * point)[0] for point in LUMA_POINTS)
    return Luma(
        at_tenth=tenth,
        at_half=half,
        at_nine_tenths=nine_tenths,
        peak_at_half=frames.luma_at(webm, duration * LUMA_POINTS[1])[1],
    )


def check_recording(webm: Path, recording: Recording) -> RecordingChecks:
    """What the frames of one finished recording measure, against what the recorder asked for."""
    duration = ffmpeg.probe_duration(webm)
    return RecordingChecks(
        duration_seconds=duration,
        wanted_seconds=recording.requested_seconds,
        luma=measure_luma(webm, duration),
    )


def page_findings(report: PageReport, *, page: str, section: int) -> list[Finding]:
    """One judgement per thing the page could not honour, dispatched on the code the page carried.

    The media layer has already refused a code the page has no business raising, so every row here
    is a page code the contract publishes and the sentence is the page's own, written for a person.
    """
    return [
        judge(
            row.code,
            row.message,
            Location(where=row.slide or row.cue or page, file=Path(page), section=section, cue=row.cue),
            stage=Stage.RECORD,
        )
        for row in report.warnings
    ]


def frame_findings(checks: RecordingChecks, *, where: Path, section: int, settings: Settings) -> list[Finding]:
    """One judgement per way the frames of a recording fall short of what the recorder asked for."""
    black = settings.verify.black_max_luma
    slack = settings.record.truncated_slack_seconds
    found: list[Finding] = []
    if checks.luma.peak_at_half <= black:
        found.append(
            judge(
                Code.PAGE_BLACK,
                f"the frame half way through has a brightest luma of {checks.luma.peak_at_half:.{LUMA_DIGITS}f}, "
                f"which is at or under the {black:.{LUMA_DIGITS}f} a black frame is.",
                Location(where=where.as_posix(), file=where, section=section),
                stage=Stage.RECORD,
            )
        )
    short = checks.wanted_seconds - checks.duration_seconds
    if checks.wanted_seconds and short > slack:
        found.append(
            judge(
                Code.PAGE_TRUNCATED,
                f"the recording runs {checks.duration_seconds:.{SECOND_DIGITS}f}s of the "
                f"{checks.wanted_seconds:.{SECOND_DIGITS}f}s it asked for, which is "
                f"{short:.{SECOND_DIGITS}f}s short against the {slack:.{SECOND_DIGITS}f}s allowed.",
                Location(where=where.as_posix(), file=where, section=section),
                stage=Stage.RECORD,
            )
        )
    return found


def stall_finding(gap_ms: int, *, where: Path, section: int, settings: Settings) -> Finding | None:
    """The judgement a recording whose frames froze carries, or None when none of them did."""
    limit = settings.record.frame_gap_max_ms
    if gap_ms <= limit:
        return None
    return judge(
        Code.PAGE_STALLED,
        f"the picture held still for {gap_ms} ms after narration t=0, which is over the {limit} ms "
        "a recorded section may ever stall for.",
        Location(where=where.as_posix(), file=where, section=section),
        stage=Stage.RECORD,
    )


def recording_findings(
    recording: Recording,
    checks: RecordingChecks,
    *,
    page: str,
    where: Path,
    section: int,
    settings: Settings,
) -> tuple[Finding, ...]:
    """Every judgement one finished recording carries, from the page, the frames and the network.

    They are one list rather than three, so a reader of the log dispatches on a code and never on
    which of three places a sentence came from.
    """
    found = [
        *page_findings(recording.report, page=page, section=section),
        *frame_findings(checks, where=where, section=section, settings=settings),
        *asset_findings(recording.external, where=page, section=section),
    ]
    stalled = stall_finding(recording.report.worst_gap_ms, where=where, section=section, settings=settings)
    if stalled is not None:
        found.append(stalled)
    return tuple(found)


__all__ = [
    "LUMA_POINTS",
    "check_recording",
    "frame_findings",
    "measure_luma",
    "page_findings",
    "recording_findings",
    "stall_finding",
]
