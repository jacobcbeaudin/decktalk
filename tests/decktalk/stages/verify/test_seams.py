"""The three checks that read the shape of the film: the section starts, the cuts and the seams."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.machine.run import Run
from decktalk.media import audio, frames
from decktalk.stages.verify.plan import EPSILON
from decktalk.stages.verify.seams import SEAM_SEARCH_FRAMES, cut_checks, planned_seams, seam_checks, start_checks

from .conftest import PAGES_TOML, SECTION_SECONDS, Measurements, opened

SEAMLESS_TOML = PAGES_TOML + "seamless = true\n"
"""The same project, with its second section declaring that it carries the first one's picture."""

STARTS = {1: 0.0, 2: SECTION_SECONDS}
"""Where the two sections of the test film sit, which the placements also say."""


def seams_of(inputs: Inputs, run: Run) -> tuple:
    """Both passes of the seam check over the test film, decoded as the autouse fixture answers."""
    return seam_checks(
        inputs,
        run,
        inputs.workspace.film,
        planned_seams(inputs, STARTS),
        frames.decode(inputs.workspace.film, frames.Wanted()),
    )


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
    assert rows[0].at_seconds == pytest.approx(inputs.settings.verify.after_dip_seconds)


def test_a_section_that_opens_on_black_is_an_error(assembled: Callable[..., Inputs], measured: Measurements) -> None:
    inputs = assembled()
    measured.luma = 10.0
    with opened(inputs.root) as run:
        start_checks(inputs, run, inputs.workspace.film, STARTS)
        found = [row for row in run.findings if row.code is Code.RECORD_BLACK]
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


@pytest.mark.parametrize(
    ("above", "dark"), [pytest.param(0.0, True, id="on the limit"), pytest.param(0.1, False, id="over it")]
)
def test_a_start_whose_brightest_luma_is_on_the_black_limit_is_black(
    assembled: Callable[..., Inputs], measured: Measurements, above: float, dark: bool
) -> None:
    """The limit is the brightest a frame may be and still be black, so a frame on it is black."""
    inputs = assembled()
    measured.luma = inputs.settings.verify.black_max_luma + above
    with opened(inputs.root) as run:
        start_checks(inputs, run, inputs.workspace.film, STARTS)
        found = [row for row in run.findings if row.code is Code.RECORD_BLACK]
    assert len(found) == (len(STARTS) if dark else 0)


# ---- the cuts -----------------------------------------------------------------------------------


def test_a_cut_that_lands_on_speech_is_an_error(assembled: Callable[..., Inputs], measured: Measurements) -> None:
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


@pytest.mark.parametrize(
    ("above", "speech"), [pytest.param(0.0, False, id="on the limit"), pytest.param(0.1, True, id="over it")]
)
def test_a_cut_whose_level_is_on_the_limit_is_quiet_and_one_over_it_lands_on_speech(
    assembled: Callable[..., Inputs], measured: Measurements, above: float, speech: bool
) -> None:
    """The limit is the loudest a silent cut may be, so a window that reaches it exactly is still silent."""
    inputs = assembled()
    measured.rms_dbfs = inputs.settings.verify.cut_max_dbfs + above
    with opened(inputs.root) as run:
        cut_checks(inputs, run, inputs.workspace.film, inputs.takes(), STARTS)
        found = [row for row in run.findings if row.code is Code.CUT_SPEECH]
    assert bool(found) is speech


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
        assert seams_of(inputs, run) == ()


def test_a_seam_that_matches_at_once_has_drifted_by_nothing(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(toml=SEAMLESS_TOML)
    measured.changed = 0.0
    with opened(inputs.root) as run:
        rows = seams_of(inputs, run)
        assert run.findings == []
    assert [(row.section, row.drift_seconds) for row in rows] == [(2, 0.0)]


def test_a_seam_whose_picture_arrives_late_reports_the_drift_in_seconds(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    """A picture a frame late is a section whose clock slipped, which the row says in seconds."""
    inputs = assembled(toml=SEAMLESS_TOML)
    measured.shares = iter([9.0, 0.0])
    with opened(inputs.root) as run:
        rows = seams_of(inputs, run)
    assert rows[0].drift_seconds == pytest.approx(1 / inputs.settings.video.fps, abs=1e-3)


def test_a_seam_that_never_matches_is_a_pop_naming_the_share_and_the_limit(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(toml=SEAMLESS_TOML)
    measured.changed = 42.0
    with opened(inputs.root) as run:
        rows = seams_of(inputs, run)
        found = [row for row in run.findings if row.code is Code.CUT_POP]
    assert found and "42.00 percent" in found[0].message
    assert f"{inputs.settings.verify.cut_change_max_percent:.2f} percent" in found[0].message
    assert rows[0].drift_seconds == pytest.approx(SEAM_SEARCH_FRAMES / inputs.settings.video.fps, abs=1e-3)


def test_a_seam_whose_share_is_on_the_limit_matches_at_once_and_is_no_pop(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    """The limit is the most a join may change, so a share exactly on it is a match rather than a drift or a pop."""
    inputs = assembled(toml=SEAMLESS_TOML)
    measured.changed = inputs.settings.verify.cut_change_max_percent
    with opened(inputs.root) as run:
        rows = seams_of(inputs, run)
        assert run.findings == []
    assert [(row.section, row.drift_seconds) for row in rows] == [(2, 0.0)]


def test_a_seam_whose_share_is_just_over_the_limit_is_searched_and_then_a_pop(
    assembled: Callable[..., Inputs], measured: Measurements
) -> None:
    inputs = assembled(toml=SEAMLESS_TOML)
    measured.changed = inputs.settings.verify.cut_change_max_percent + 0.01
    with opened(inputs.root) as run:
        rows = seams_of(inputs, run)
        assert [row.code for row in run.findings] == [Code.CUT_POP]
    assert rows[0].drift_seconds == pytest.approx(SEAM_SEARCH_FRAMES / inputs.settings.video.fps, abs=1e-3)


@pytest.mark.parametrize(
    ("over", "matched"),
    [
        pytest.param(0.0, True, id="on the limit"),
        pytest.param(EPSILON / 2, True, id="inside the rounding allowance"),
        pytest.param(0.01, False, id="over it"),
    ],
)
def test_a_late_frame_whose_share_is_on_the_limit_is_where_the_picture_arrived(
    assembled: Callable[..., Inputs], measured: Measurements, over: float, matched: bool
) -> None:
    """A frame after the first that reaches the limit is the late picture, and the drift is one frame."""
    inputs = assembled(toml=SEAMLESS_TOML)
    limit = inputs.settings.verify.cut_change_max_percent
    measured.shares = iter([9.0, limit + over, 9.0, 9.0])
    with opened(inputs.root) as run:
        rows = seams_of(inputs, run)
    frames_late = 1 if matched else SEAM_SEARCH_FRAMES
    assert rows[0].drift_seconds == pytest.approx(frames_late / inputs.settings.video.fps, abs=1e-3)
