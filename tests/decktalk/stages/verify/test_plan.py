"""The arithmetic a cue is judged by, read without rendering anything.

The asymmetric neighbour allowance is held here in two halves, because that is what it is: backward
it is the reference lead, which every cue shares, and forward it is the neighbour's own declared
span, which no two effects share.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes
from decktalk.inputs import Inputs
from decktalk.page import CAPTURE_FPS, ENTRANCES, MEASURABLE_SPAN_SECONDS
from decktalk.settings import GUARD_FRAMES, Settings, VerifyConfig
from decktalk.stages.verify.plan import (
    EPSILON,
    MILLISECONDS,
    PROBE_TAIL_SECONDS,
    Neighbour,
    apart,
    block_size,
    control_spans,
    default_checks,
    frame_size,
    onset_offset_seconds,
    opted_out,
    probe_plan,
    reference_lead,
    reference_time,
    thin_change,
)

FPS = 25
"""The rate every case here is written at, which is the rate the recorder captures at."""


def settings_with(**verify: object) -> Settings:
    """The default tree with one or two verify keys moved, which is what a case names."""
    return Settings(verify=VerifyConfig(**verify))  # type: ignore[arg-type]


# ---- the published numbers this module reads rather than restates -----------------------------


def test_the_reference_lead_is_the_published_formula_and_not_a_second_copy() -> None:
    """The lead is the offset limit plus the grid guard plus whatever extra lead was asked for."""
    settings = settings_with(cue_offset_max_ms=80.0, reference_lead_extra_ms=40.0)
    expected = 80.0 / MILLISECONDS + GUARD_FRAMES / CAPTURE_FPS + 40.0 / MILLISECONDS
    assert reference_lead(settings) == pytest.approx(expected)


def test_a_wider_offset_limit_reaches_the_reference_frame_further_back() -> None:
    assert reference_lead(settings_with(cue_offset_max_ms=200.0)) > reference_lead(
        settings_with(cue_offset_max_ms=80.0)
    )


def test_a_frame_is_compared_at_a_quarter_of_its_own_size() -> None:
    settings = Settings()
    assert frame_size(settings) == {"width": settings.video.width // 4, "height": settings.video.height // 4}


def test_a_block_copy_is_one_pixel_per_transform_block() -> None:
    settings = Settings()
    assert block_size(settings)["width"] == settings.video.width // 8


# ---- the reference frame -----------------------------------------------------------------------


def test_the_reference_frame_sits_the_whole_lead_before_its_cue() -> None:
    settings = settings_with(cue_offset_max_ms=80.0)
    at = reference_time(10.0, 2.0, fade_in=False, dip=0.0, settings=settings, fps=FPS)
    assert at == pytest.approx(12.0 - reference_lead(settings), abs=1e-4)


def test_a_cue_at_the_very_start_of_a_section_leaves_no_frame_before_it() -> None:
    assert reference_time(10.0, 0.0, fade_in=False, dip=0.0, settings=Settings(), fps=FPS) is None


def test_the_reference_frame_never_sits_inside_the_fade_in() -> None:
    """A frame still coming up from black is not a picture the reveal can be measured against."""
    settings = Settings()
    at = reference_time(10.0, 0.2, fade_in=True, dip=0.15, settings=settings, fps=FPS)
    assert at is not None
    assert at >= 10.15


# ---- the neighbour allowance, backward ---------------------------------------------------------


def test_backward_a_neighbour_reaches_by_the_reference_lead() -> None:
    """A reveal may land early by the whole lead, so a neighbour spoils a span that far ahead of it."""
    lead = 0.14
    neighbour = Neighbour(at=10.0, span=0.0)
    assert neighbour.reaches(9.9, 9.95, lead)
    assert not neighbour.reaches(9.5, 9.8, lead)


def test_a_neighbour_closer_than_the_lead_is_part_of_the_same_reveal() -> None:
    lead = 0.14
    near, far = Neighbour(at=10.05, span=0.0), Neighbour(at=11.0, span=0.0)
    assert apart([near, far], 10.0, lead) == [far]


# ---- the neighbour allowance, forward ----------------------------------------------------------


def test_forward_a_neighbour_reaches_exactly_its_own_declared_span() -> None:
    """The forward half is the effect's own animation, so it ends where the animation ends."""
    neighbour = Neighbour(at=10.0, span=0.2)
    assert neighbour.reaches(10.1, 10.15, 0.0)
    assert not neighbour.reaches(10.25, 10.4, 0.0)


def test_a_draw_reaches_further_forward_than_a_cut() -> None:
    """The two halves are not one number, which is exactly what the single reach constant got wrong."""
    draw, cut = ENTRANCES["draw"].seconds, ENTRANCES["cut"].seconds
    assert Neighbour(at=10.0, span=draw).reaches(10.3, 10.4, 0.0)
    assert not Neighbour(at=10.0, span=cut).reaches(10.3, 10.4, 0.0)


def test_no_declared_span_reaches_forward_by_nothing_at_all() -> None:
    assert not Neighbour(at=10.0, span=0.0).reaches(10.0 + EPSILON * 2, 10.5, 0.0)


def test_every_entrance_span_stays_under_the_measurable_ceiling() -> None:
    """A forward reach past the ceiling would be an effect that covers its own cue."""
    assert all(effect.seconds <= MEASURABLE_SPAN_SECONDS for effect in ENTRANCES.values())


# ---- the control spans and the probe plan -------------------------------------------------------


def test_two_control_spans_end_at_the_reference_and_stay_past_the_floor() -> None:
    assert control_spans(10.0, 1.0, 0.0) == [(9.0, 10.0), (8.0, 9.0)]
    assert control_spans(10.0, 1.0, 9.5) == []


def test_a_cue_with_room_around_it_is_probed_at_the_delays_the_settings_name() -> None:
    settings = VerifyConfig(probe_delays_seconds=(0.7, 1.5))
    delays, fitted = probe_plan(10.0, 9.8, 0.0, 30.0, [], settings, FPS, lead=0.14)
    assert (delays, fitted) == ([0.7, 1.5], False)


def test_a_probe_past_the_end_of_its_section_is_dropped() -> None:
    settings = VerifyConfig(probe_delays_seconds=(0.7, 1.5))
    delays, _fitted = probe_plan(10.0, 9.8, 0.0, 11.0 + PROBE_TAIL_SECONDS, [], settings, FPS, lead=0.14)
    assert delays == [0.7]


def test_a_close_neighbour_fits_the_probe_into_the_gap_it_leaves() -> None:
    """A probe that would read the neighbour's own reveal is shortened rather than trusted."""
    settings = VerifyConfig(probe_delays_seconds=(0.7,), cue_offset_max_ms=80.0)
    delays, fitted = probe_plan(10.0, 9.8, 0.0, 30.0, [Neighbour(at=10.5, span=0.32)], settings, FPS, lead=0.14)
    assert fitted
    assert delays and max(delays) < 0.7


def test_a_neighbour_whose_effect_is_instant_spoils_less_than_one_that_draws() -> None:
    settings = VerifyConfig(probe_delays_seconds=(0.7,), cue_offset_max_ms=80.0)
    instant, _ = probe_plan(10.0, 9.8, 0.0, 30.0, [Neighbour(at=10.9, span=0.0)], settings, FPS, lead=0.05)
    drawing, _ = probe_plan(10.0, 9.8, 0.0, 30.0, [Neighbour(at=10.9, span=0.48)], settings, FPS, lead=0.05)
    assert max(instant) >= max(drawing)


# ---- the thin change second opinion ---------------------------------------------------------------


def test_a_share_under_the_factor_times_its_floor_reads_thin() -> None:
    settings = VerifyConfig(changed_share_min_percent=0.1, margin_min_points=0.1, thin_change_factor=3.0)
    assert thin_change(0.2, 0.2, settings)
    assert not thin_change(0.4, 0.4, settings)


def test_a_factor_of_one_turns_the_second_opinion_off() -> None:
    settings = VerifyConfig(changed_share_min_percent=0.1, margin_min_points=0.1, thin_change_factor=1.0)
    assert not thin_change(0.1, 0.1, settings)


# ---- what is measured, and in what order -----------------------------------------------------------


def test_every_resolved_cue_is_checked_in_section_order_and_then_cue_time(tmp_path: Path) -> None:
    document = {
        "sections": [
            {"section": 2, "key": "02", "estimated": True, "cues": [{"cue": "b", "phrase": "x", "seconds": 0.5}]},
            {
                "section": 1,
                "key": "01",
                "estimated": True,
                "cues": [
                    {"cue": "late", "phrase": "x", "seconds": 2.0},
                    {"cue": "early", "phrase": "x", "seconds": 0.5},
                ],
            },
        ]
    }
    path = tmp_path / "cue-times.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert default_checks(CueTimes.parse(path)) == [(1, "early"), (1, "late"), (2, "b")]


def test_a_run_that_names_sections_checks_only_their_cues(tmp_path: Path) -> None:
    document = {
        "sections": [
            {"section": 1, "key": "01", "estimated": True, "cues": [{"cue": "a", "phrase": "x", "seconds": 0.5}]},
            {"section": 2, "key": "02", "estimated": True, "cues": [{"cue": "b", "phrase": "x", "seconds": 0.5}]},
        ]
    }
    path = tmp_path / "cue-times.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert default_checks(CueTimes.parse(path), [2]) == [(2, "b")]


def test_a_film_with_no_cue_times_checks_nothing() -> None:
    assert default_checks(None) == []


def test_a_cue_the_author_opted_out_of_is_never_measured(tmp_path: Path) -> None:
    (tmp_path / "decktalk.toml").write_text(
        '[project]\nname = "t"\n\n[[section]]\nnumber = 1\npage = "deck/index.html"\nscene = "1"\n', encoding="utf-8"
    )
    (tmp_path / "cues.json").write_text(
        json.dumps(
            {"sections": {"1": {"cues": [{"cue": "1:a", "on": "x", "verify": False}, {"cue": "1:b", "on": "y"}]}}}
        ),
        encoding="utf-8",
    )
    inputs = Inputs.load(tmp_path, environ={})
    assert opted_out(inputs) == {(1, "1:a")}


# ---- the onset scan -------------------------------------------------------------------------------


def test_a_reveal_is_the_first_frame_whose_share_jumps() -> None:
    series = [(1.0, 0.0), (1.04, 0.01), (1.08, 5.0), (1.12, 6.0)]
    assert onset_offset_seconds(series, 1.0, 1.1, 0.5) == pytest.approx(-0.02)


def test_motion_that_is_always_there_is_a_slope_and_never_an_onset() -> None:
    """A curve still drawing grows a little every frame, which is not a reveal starting."""
    series = [(1.0, 0.0), (1.04, 0.1), (1.08, 0.2), (1.12, 0.3)]
    assert onset_offset_seconds(series, 1.0, 1.0, 5.0) is None


def test_a_slow_growth_still_reports_the_frame_that_passed_the_floor() -> None:
    series = [(1.0, 0.0), (1.04, 0.0), (1.08, 0.4), (1.12, 0.9)]
    assert onset_offset_seconds(series, 1.0, 1.0, 0.3) == pytest.approx(0.08)


def test_a_frame_whose_blocks_did_not_change_is_the_encoders_own_ringing() -> None:
    series = [(1.0, 0.0), (1.04, 5.0), (1.08, 5.2)]
    blocks = {1.0: 0.0, 1.04: 0.0, 1.08: 4.0}
    assert onset_offset_seconds(series, 1.0, 1.0, 1.0, blocks=blocks) == pytest.approx(0.08)


def test_a_series_with_nothing_in_it_reports_no_onset() -> None:
    assert onset_offset_seconds([], 1.0, 1.0, 0.1) is None
