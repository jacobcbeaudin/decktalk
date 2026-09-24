"""The three checks that read the shape of the film: the section starts, the cuts and the seams."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.media import audio, frames
from decktalk.stages.verify.seams import SEAM_SEARCH_FRAMES, cut_checks, seam_checks, start_checks

from .conftest import PAGES_TOML, SECTION_SECONDS, Measurements, opened

SEAMLESS_TOML = PAGES_TOML + "seamless = true\n"
"""The same project, with its second section declaring that it carries the first one's picture."""

STARTS = {1: 0.0, 2: SECTION_SECONDS}
"""Where the two sections of the test film sit, which the cut list also says."""


# ---- the section starts ------------------------------------------------------------------------


def test_every_section_start_reports_the_brightest_luma_of_its_own_frame(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled()
    measured.luma = 180.0
    with opened(inputs.root) as run:
        rows = start_checks(inputs, run, inputs.workspace.film, STARTS)
    assert [row.section for row in rows] == [1, 2]
    assert rows[0].luma == pytest.approx(180.0)
    assert rows[0].at == pytest.approx(inputs.settings.verify.after_dip_seconds)


def test_a_section_that_opens_on_black_is_a_certain_finding(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled()
    measured.luma = 10.0
    with opened(inputs.root) as run:
        start_checks(inputs, run, inputs.workspace.film, STARTS)
        found = [row for row in run.findings if row.code is Code.PAGE_BLACK]
    assert len(found) == 2
    assert "10.0" in found[0].message
    assert f"{inputs.settings.verify.black_max_luma:.0f}" in found[0].message


def test_a_section_that_opens_on_a_picture_says_nothing(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled()
    measured.luma = 200.0
    with opened(inputs.root) as run:
        start_checks(inputs, run, inputs.workspace.film, STARTS)
        assert run.findings == []


# ---- the cuts -----------------------------------------------------------------------------------


def test_a_cut_that_lands_on_speech_is_a_certain_finding(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled()
    measured.rms_dbfs = -10.0
    with opened(inputs.root) as run:
        rows = cut_checks(inputs, run, inputs.workspace.film, inputs.takes(), STARTS)
        found = [row for row in run.findings if row.code is Code.CUT_SPEECH]
    assert rows and rows[0].speech_dbfs == pytest.approx(-10.0)
    assert found and "-10.0 dBFS" in found[0].message
    assert f"{inputs.settings.verify.cut_max_dbfs:.1f} dBFS" in found[0].message


def test_a_quiet_cut_says_nothing_and_still_reports_its_level(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled()
    measured.rms_dbfs = -90.0
    with opened(inputs.root) as run:
        rows = cut_checks(inputs, run, inputs.workspace.film, inputs.takes(), STARTS)
        assert run.findings == []
    assert [row.speech_dbfs for row in rows] == [pytest.approx(-90.0), pytest.approx(-90.0)]


def test_the_row_reports_the_step_the_waveform_takes_across_the_cut(
    assembled: Callable[..., Inputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step is the level after the cut less the level before it, which is what a viewer hears."""
    inputs = assembled()
    film = inputs.workspace.film
    cut = SECTION_SECONDS

    def level(path: object, start: float, _seconds: float) -> float:
        if path != film:
            return -90.0
        return -20.0 if start >= cut else -60.0

    monkeypatch.setattr(audio, "rms_db", level)
    with opened(inputs.root) as run:
        rows = cut_checks(inputs, run, film, inputs.takes(), STARTS)
    assert rows[0].step_dbfs == pytest.approx(40.0)


def test_a_film_with_no_narration_track_has_no_cut_rows(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled()
    inputs.workspace.narration_path.unlink()
    with opened(inputs.root) as run:
        assert cut_checks(inputs, run, inputs.workspace.film, inputs.takes(), STARTS) == ()


# ---- the seams ------------------------------------------------------------------------------------


def test_a_section_that_declares_nothing_is_never_checked_for_a_seam(assembled: Callable[..., Inputs]) -> None:
    inputs = assembled()
    with opened(inputs.root) as run:
        assert seam_checks(inputs, run, inputs.workspace.film, STARTS) == ()


def test_a_seam_that_matches_at_once_has_drifted_by_nothing(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(toml=SEAMLESS_TOML)
    measured.changed = 0.0
    with opened(inputs.root) as run:
        rows = seam_checks(inputs, run, inputs.workspace.film, STARTS)
        assert run.findings == []
    assert [(row.section, row.drift) for row in rows] == [(2, 0.0)]


def test_a_seam_whose_picture_arrives_late_reports_the_drift_in_seconds(
    assembled: Callable[..., Inputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A picture a frame late is a section whose clock slipped, which the row says in seconds."""
    inputs = assembled(toml=SEAMLESS_TOML)
    shares = iter([9.0, 0.0])
    monkeypatch.setattr(frames, "changed_pixels_percent", lambda _p, _a, _b, **_kw: next(shares))
    with opened(inputs.root) as run:
        rows = seam_checks(inputs, run, inputs.workspace.film, STARTS)
    assert rows[0].drift == pytest.approx(1 / inputs.settings.video.output_fps, abs=1e-3)


def test_a_seam_that_never_matches_is_a_pop_naming_the_share_and_the_limit(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(toml=SEAMLESS_TOML)
    measured.changed = 42.0
    with opened(inputs.root) as run:
        rows = seam_checks(inputs, run, inputs.workspace.film, STARTS)
        found = [row for row in run.findings if row.code is Code.CUT_POP]
    assert found and "42.00 percent" in found[0].message
    assert f"{inputs.settings.verify.cut_change_max_percent:.2f} percent" in found[0].message
    assert rows[0].drift == pytest.approx(SEAM_SEARCH_FRAMES / inputs.settings.video.output_fps, abs=1e-3)
