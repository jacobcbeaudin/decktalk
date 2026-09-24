"""The loudness pass: one gain, a true-peak limiter, and the judgement a miss carries."""

from __future__ import annotations

import pytest

from decktalk.media import audio
from decktalk.stages.assemble.loudness import (
    LIMITER_HEADROOM_DB,
    LOUDNESS_TOLERANCE_LU,
    loudness_findings,
    measured,
    normalize_loudness,
)

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")


def a_measurement(*, i: float, tp: float, lra: float = 6.0) -> audio.Loudness:
    return audio.Loudness(i=i, tp=tp, lra=lra, thresh=-30.0, offset=0.0)


def test_the_gain_is_the_distance_to_the_target_and_the_limiter_sits_under_the_ceiling(
    tmp_path, write_project, monkeypatch, fake_ffmpeg
):
    """A plain gain keeps the mix's dynamics, and only the peaks that would cross the ceiling are touched."""
    inputs = write_project(tmp_path)
    monkeypatch.setattr(audio, "measure_loudness", lambda *_a, **_k: a_measurement(i=-20.0, tp=-3.0))
    before, after = normalize_loudness(inputs, tmp_path / "mix.mov", tmp_path / "work.mp4")
    assert before.i == after.i == -20.0
    graph = next(call[call.index("-af") + 1] for call in fake_ffmpeg.calls if "-af" in call)
    target = inputs.settings.mix.loudness.target_lufs
    assert graph.startswith(f"volume={target - -20.0:.2f}dB,")
    assert "alimiter=limit=" in graph
    assert "aresample=192000" in graph


def test_a_peak_over_the_ceiling_names_the_measured_number_and_the_limit(tmp_path, write_project, open_run):
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    ceiling = inputs.settings.mix.loudness.true_peak_max_dbtp
    found = loudness_findings(inputs, opened.run, a_measurement(i=inputs.settings.mix.loudness.target_lufs, tp=0.4))
    assert [row.code.name for row in found] == ["MIX_LOUDNESS"]
    assert f"{0.4:.1f} dBTP" in found[0].message
    assert f"{ceiling:.1f} dBTP" in found[0].message
    assert found[0].location.where.endswith(".mp4")
    assert opened.codes() == ["MIX_LOUDNESS"]


def test_an_integrated_loudness_off_target_names_how_far_off_it_is(tmp_path, write_project, open_run):
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    target = inputs.settings.mix.loudness.target_lufs
    ceiling = inputs.settings.mix.loudness.true_peak_max_dbtp
    found = loudness_findings(inputs, opened.run, a_measurement(i=target - 3.0, tp=ceiling - 1.0))
    assert [row.code.name for row in found] == ["MIX_LOUDNESS"]
    assert "3.0 LU" in found[0].message
    assert f"{LOUDNESS_TOLERANCE_LU:.1f} LU" in found[0].message


def test_a_mix_inside_the_tolerance_is_judged_at_all(tmp_path, write_project, open_run):
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    loudness = inputs.settings.mix.loudness
    inside = a_measurement(i=loudness.target_lufs + LOUDNESS_TOLERANCE_LU / 2, tp=loudness.true_peak_max_dbtp - 0.5)
    assert loudness_findings(inputs, opened.run, inside) == []
    assert opened.codes() == []


def test_the_measurement_becomes_the_one_shape_a_reader_receives(tmp_path, write_project):
    inputs = write_project(tmp_path)
    row = measured(inputs, a_measurement(i=-16.04, tp=-1.26, lra=7.44))
    assert (row.integrated_lufs, row.true_peak_dbtp, row.range_lu) == (-16.0, -1.3, 7.4)
    assert row.target_lufs == inputs.settings.mix.loudness.target_lufs


def test_the_limiter_headroom_keeps_it_under_the_ceiling_it_guards():
    """The limiter works on oversampled samples, so it has to act before the ceiling, not at it."""
    assert LIMITER_HEADROOM_DB > 0
