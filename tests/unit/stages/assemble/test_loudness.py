"""The loudness report: what counts as a peak over the ceiling or a missed target."""

from __future__ import annotations

from decktalk.model import Project


def test_loudness_problems_report_peaks_and_missed_targets(tmp_path, write_project):
    from decktalk.media.audio import Loudness
    from decktalk.stages.assemble.loudness import loudness_problems
    from decktalk.verdicts import Verdict

    p = Project.load(write_project(tmp_path), environ={})
    assert loudness_problems(p, Loudness(i=-16.4, tp=-1.6, lra=5, thresh=-27, offset=0)) == []
    over = loudness_problems(p, Loudness(i=-25.2, tp=-1.0, lra=5, thresh=-27, offset=0))
    assert len(over) == 2 and "true peak -1.0 dBTP" in over[0].detail and "9.2 LU" in over[1].detail
    # Each miss is a row a reader dispatches on, and it names the film whose loudness was measured.
    assert [row.verdict for row in over] == [Verdict.LOUDNESS_MISS, Verdict.LOUDNESS_MISS]
    assert {row.where for row in over} == {"build/out/t.mp4"}


def test_the_gain_and_the_ceiling_are_the_ones_the_project_configured(tmp_path, write_project, monkeypatch):
    """Every finished film passes through this filter string, so the numbers in it are checked here."""
    from decktalk.media.audio import Loudness
    from decktalk.stages.assemble import loudness as loudness_module
    from decktalk.stages.assemble.loudness import normalize_loudness

    p = Project.load(write_project(tmp_path), environ={})
    ln = p.mix.loudness
    measured = Loudness(i=ln.target_lufs - 4.0, tp=-3.0, lra=5, thresh=-27, offset=0)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(loudness_module.audio, "measure_loudness", lambda path, **kw: measured)
    monkeypatch.setattr(loudness_module.ffmpeg, "run", lambda *args: calls.append(args))

    before, after = normalize_loudness(p, tmp_path / "mix.mov", tmp_path / "out.mp4")
    assert before == measured and after == measured
    [args] = calls
    graph = args[args.index("-af") + 1]
    assert graph.startswith("volume=4.00dB,"), "the gain is the distance from the measured loudness to the target"
    # The numbers are written out here, because a limiter at the delivery rate catches no inter-sample
    # peak and a ceiling at the target leaves none of the headroom an oversampled limiter needs.
    assert "aresample=192000" in graph, "the limiter runs oversampled for inter-sample peaks"
    ceiling = 10 ** ((ln.true_peak_db - 0.3) / 20)
    assert f"alimiter=limit={ceiling:.4f}" in graph
    assert graph.endswith(f"aresample={p.settings.video.sample_rate}")
    assert "-c:a" in args and args[args.index("-c:a") + 1] == "aac"
