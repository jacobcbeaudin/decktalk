"""What one finished recording is judged on, and that every judgement arrives as a code.

The channel these tests hold open is the one the code review's must 7 named: the page reports a
code, the recorder dispatches on that code, and no sentence is matched against a substring anywhere
between them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Luma, RecordingChecks
from decktalk.findings import Code
from decktalk.media import ffmpeg, frames
from decktalk.media.browser import Recording
from decktalk.media.pagereport import FrameGap, PageReport, PageWarningRow
from decktalk.settings import Settings
from decktalk.stages.record.checks import (
    check_recording,
    frame_findings,
    measure_luma,
    page_findings,
    recording_findings,
    stall_finding,
)

PAGE = "deck/index.html"
"""The page every row here is about, as `decktalk.toml` spells it."""

WHERE = Path("build/recordings/01.webm")
"""The recording every frame judgement here is about, project-relative."""

SECTION = 1
"""The section every row here belongs to."""


def a_report(**fields: object) -> PageReport:
    return PageReport.model_validate({"version": "0.5.0", "mode": "cue", "scene": "1", "slide": "1.1", **fields})


def a_recording(report: PageReport, *, external: tuple[str, ...] = (), wanted: float = 10.0) -> Recording:
    return Recording(
        url="http://project.localhost/deck/index.html?scene=1",
        assets=(PAGE,),
        external=external,
        requested_seconds=wanted,
        load_seconds=0.2,
        settle_seconds=0.5,
        clock_start_seconds=1.5,
        page_errors=(),
        report=report,
    )


def checks_of(*, duration: float = 10.0, wanted: float = 10.0, peak: float = 200.0) -> RecordingChecks:
    return RecordingChecks(
        duration_seconds=duration,
        wanted_seconds=wanted,
        luma=Luma(at_tenth=90.0, at_half=90.0, at_nine_tenths=90.0, peak_at_half=peak),
    )


@pytest.fixture
def settings() -> Settings:
    return Settings()


def test_the_luma_is_read_at_a_tenth_a_half_and_nine_tenths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    read: list[float] = []

    def luma_at(_path: Path, at: float, **_kwargs: object) -> tuple[float, float]:
        read.append(round(at, 3))
        return 90.0, 210.0

    monkeypatch.setattr(frames, "luma_at", luma_at)
    measured = measure_luma(tmp_path / "01.webm", 10.0)
    assert read[:3] == [1.0, 5.0, 9.0]
    assert measured.peak_at_half == 210.0


def test_the_checks_measure_the_file_against_what_the_recorder_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: 9.5)
    monkeypatch.setattr(frames, "luma_at", lambda _path, _at, **_kwargs: (90.0, 210.0))
    measured = check_recording(tmp_path / "01.webm", a_recording(a_report(), wanted=10.0))
    assert (measured.duration_seconds, measured.wanted_seconds) == (9.5, 10.0)


def test_every_page_warning_becomes_the_finding_of_the_code_the_page_carried() -> None:
    report = a_report(
        warnings=[
            {"code": "PAGE_KATEX_ERROR", "message": "KaTeX refused $x$.", "slide": "1.1", "cue": None, "attr": None},
            {
                "code": "PAGE_UNKNOWN_ATTR",
                "message": "data-nope is not a knob.",
                "slide": None,
                "cue": "1.1:open",
                "attr": "data-nope",
            },
        ]
    )
    found = page_findings(report, page=PAGE, section=SECTION)
    assert [row.code for row in found] == [Code.PAGE_KATEX_ERROR, Code.PAGE_UNKNOWN_ATTR]
    assert [row.location.where for row in found] == ["1.1", "1.1:open"]
    assert found[0].location.file == Path(PAGE)


def test_a_page_that_reported_nothing_is_judged_on_nothing() -> None:
    assert page_findings(a_report(), page=PAGE, section=SECTION) == []


def test_a_dark_frame_half_way_through_is_black(settings: Settings) -> None:
    peak = settings.verify.black_max_luma
    found = frame_findings(checks_of(peak=peak), where=WHERE, section=SECTION, settings=settings)
    assert [row.code for row in found] == [Code.PAGE_BLACK]
    assert f"{peak:.1f}" in found[0].message


def test_a_bright_frame_half_way_through_is_not_black(settings: Settings) -> None:
    peak = settings.verify.black_max_luma + 1
    assert frame_findings(checks_of(peak=peak), where=WHERE, section=SECTION, settings=settings) == []


def test_a_recording_short_of_its_own_length_is_truncated(settings: Settings) -> None:
    short = 10.0 - settings.record.truncated_slack_seconds - 0.5
    found = frame_findings(checks_of(duration=short, wanted=10.0), where=WHERE, section=SECTION, settings=settings)
    assert [row.code for row in found] == [Code.PAGE_TRUNCATED]
    assert "10.00s" in found[0].message


def test_a_recording_inside_its_own_slack_is_not_truncated(settings: Settings) -> None:
    inside = 10.0 - settings.record.truncated_slack_seconds
    assert (
        frame_findings(checks_of(duration=inside, wanted=10.0), where=WHERE, section=SECTION, settings=settings) == []
    )


def test_a_stall_over_the_limit_carries_the_measured_gap_and_the_limit(settings: Settings) -> None:
    limit = settings.record.frame_gap_max_ms
    found = stall_finding(limit + 40, where=WHERE, section=SECTION, settings=settings)
    assert found is not None
    assert found.code is Code.PAGE_STALLED
    assert f"{limit + 40} ms" in found.message
    assert f"{limit} ms" in found.message


def test_a_gap_inside_the_limit_is_no_stall(settings: Settings) -> None:
    assert stall_finding(settings.record.frame_gap_max_ms, where=WHERE, section=SECTION, settings=settings) is None


def test_every_judgement_of_one_recording_arrives_in_one_list(settings: Settings) -> None:
    report = a_report(
        warnings=[{"code": "PAGE_KATEX_MISSING", "message": "KaTeX never arrived.", "slide": "1.1"}],
        frameGaps=[FrameGap(at=2.0, ms=settings.record.frame_gap_max_ms + 100).model_dump()],
    )
    found = recording_findings(
        a_recording(report, external=("https://cdn.example.com",)),
        checks_of(peak=1.0),
        page=PAGE,
        where=WHERE,
        section=SECTION,
        settings=settings,
    )
    assert {row.code for row in found} == {
        Code.PAGE_KATEX_MISSING,
        Code.PAGE_BLACK,
        Code.PAGE_CDN_ASSET,
        Code.PAGE_STALLED,
    }


def test_a_warning_the_page_has_no_business_raising_is_refused_before_it_reaches_a_finding() -> None:
    """The media layer refuses a code DeckTalk measures itself, so no deck can decide its own verdict."""
    with pytest.raises(ValueError, match="PAGE_BLACK"):
        PageWarningRow(code=Code.PAGE_BLACK, message="the deck says it is fine")
