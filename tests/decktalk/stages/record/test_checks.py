"""What one finished recording is judged on, and that every judgement arrives as a code.

The channel these tests hold open is this one: the page reports a
code, the recorder dispatches on that code, and no sentence is matched against a substring anywhere
between them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Luma, RecordingChecks
from decktalk.findings import Code
from decktalk.media import ffmpeg, frames
from decktalk.media.pagereport import PageWarningRow
from decktalk.settings import Settings
from decktalk.stages.record.checks import (
    check_recording,
    frame_findings,
    measure_luma,
    recording_findings,
)
from support.pages import a_recording, a_report

PAGE = "deck/index.html"
"""The page every row here is about, as `decktalk.toml` spells it."""

WHERE = Path("build/recordings/01.mp4")
"""The recording every frame judgement here is about, project-relative."""

SECTION = 1
"""The section every row here belongs to."""


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
    measured = measure_luma(tmp_path / "01.mp4", 10.0)
    # The middle frame gives its mean and its peak in one read, so it is decoded once.
    assert read == [1.0, 5.0, 9.0]
    assert measured.peak_at_half == 210.0


def test_the_checks_measure_the_file_against_what_the_recorder_asked_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ffmpeg, "probe_duration", lambda _path: 9.5)
    monkeypatch.setattr(frames, "luma_at", lambda _path, _at, **_kwargs: (90.0, 210.0))
    measured = check_recording(tmp_path / "01.mp4", a_recording(report=a_report(), requested_seconds=10.0))
    assert (measured.duration_seconds, measured.wanted_seconds) == (9.5, 10.0)


def test_a_dark_frame_half_way_through_is_black(settings: Settings) -> None:
    peak = settings.verify.black_max_luma
    found = frame_findings(checks_of(peak=peak), where=WHERE, section=SECTION, settings=settings)
    assert [row.code for row in found] == [Code.RECORD_BLACK]
    assert f"{peak:.1f}" in found[0].message


def test_a_bright_frame_half_way_through_is_not_black(settings: Settings) -> None:
    peak = settings.verify.black_max_luma + 1
    assert frame_findings(checks_of(peak=peak), where=WHERE, section=SECTION, settings=settings) == []


def test_a_recording_two_frames_short_of_its_own_length_is_truncated(settings: Settings) -> None:
    """The recorder writes the span's own frames, so two frames missing is a recording that stopped early."""
    short = 10.0 - 2 / settings.video.fps
    found = frame_findings(checks_of(duration=short, wanted=10.0), where=WHERE, section=SECTION, settings=settings)
    assert [row.code for row in found] == [Code.RECORD_TRUNCATED]
    assert "10.00s" in found[0].message


def test_a_recording_one_frame_short_is_its_span_rounded_to_a_frame(settings: Settings) -> None:
    """A span rounds to the nearest whole frame, so a whole recording may sit up to one frame under it."""
    inside = 10.0 - 1 / settings.video.fps
    assert (
        frame_findings(checks_of(duration=inside, wanted=10.0), where=WHERE, section=SECTION, settings=settings) == []
    )


def test_every_judgement_of_one_recording_arrives_in_one_list(settings: Settings) -> None:
    report = a_report(
        warnings=[{"code": "PAGE_KATEX_MISSING", "message": "KaTeX never arrived.", "slide": "1.1"}],
    )
    found = recording_findings(
        a_recording(report=report, external=("https://cdn.example.com",), requested_seconds=10.0),
        checks_of(peak=1.0),
        page=PAGE,
        where=WHERE,
        section=SECTION,
        settings=settings,
    )
    assert {row.code for row in found} == {
        Code.PAGE_KATEX_MISSING,
        Code.RECORD_BLACK,
        Code.PAGE_CDN_ASSET,
    }


def test_a_change_no_declaration_explains_is_judged_once_per_element_with_its_second(settings: Settings) -> None:
    undeclared = [{"what": "<div#late>", "at": 0.4, "frames": 3}, {"what": "<p.count>", "at": 1.2, "frames": 1}]
    found = recording_findings(
        a_recording(undeclared=undeclared), checks_of(), page=PAGE, where=WHERE, section=SECTION, settings=settings
    )
    assert [(row.code, row.location.section) for row in found] == [(Code.PAGE_CHANGE_UNDECLARED, SECTION)] * 2
    assert "<div#late>" in found[0].message and "0.40s" in found[0].message and "3 frames" in found[0].message


def test_a_warning_the_page_has_no_business_raising_is_refused_before_it_reaches_a_finding() -> None:
    """The media layer refuses a code DeckTalk measures itself, so no deck can decide its own verdict."""
    with pytest.raises(ValueError, match="RECORD_BLACK"):
        PageWarningRow(code=Code.RECORD_BLACK, message="the deck says it is fine")
