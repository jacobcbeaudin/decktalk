"""The ffmpeg calls behind the cue plan: the section starts, the probes and the click search."""

from __future__ import annotations

from pathlib import Path

from decktalk.stages.verify.measure import assembled_starts, click_offset_ms


def test_click_search_stays_inside_the_section(monkeypatch):
    from decktalk.media import audio as audio_module

    calls: list[tuple[float, float]] = []

    def fake_span(path, start, seconds, *, sample_rate=48000):
        calls.append((round(start, 3), round(seconds, 3)))
        return [0] * 100 + [2000] + [0] * 100

    monkeypatch.setattr(audio_module, "pcm_span", fake_span)
    assert click_offset_ms(Path("f.mp4"), 10.0, 0.25) is not None
    assert click_offset_ms(Path("f.mp4"), 5.1, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 8.9, 0.25, floor=5.0, ceiling=9.0) is not None
    assert click_offset_ms(Path("f.mp4"), 9.5, 0.25, floor=5.0, ceiling=9.0) is None
    assert calls == [(9.75, 0.5), (5.0, 0.35), (8.65, 0.35)]


def test_the_section_starts_are_the_cumulative_lengths_of_the_assembled_sections(verify_project):
    project = verify_project({"01": "a@1.0"})
    starts, total = assembled_starts(project)
    assert starts == {"01": 0.0, "02": 5.0} and total == 10.0
    # Section 3 has no sections/03.mp4, so it is not counted and not a start.
    assert "03" not in starts
