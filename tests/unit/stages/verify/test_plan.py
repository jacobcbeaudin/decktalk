"""The arithmetic behind a cue check, which needs no file and no ffmpeg."""

from __future__ import annotations

import dataclasses

import pytest

from decktalk.artifacts import CueTime, CueTimes
from decktalk.errors import ConfigError
from decktalk.settings import Settings, VerifyConfig
from decktalk.stages.verify.plan import (
    CueCheck,
    control_spans,
    cue_reach,
    default_checks,
    onset_offset_ms,
    reference_time,
    round_or_none,
    skipped,
    thin_change,
)
from decktalk.verdicts import SkipReason, Verdict

CFG = VerifyConfig()
FPS = 25


def test_a_measured_number_rounds_and_an_unmeasured_one_stays_none():
    assert round_or_none(1.23456, 3) == 1.235
    assert round_or_none(None, 3) is None


def test_the_reference_lead_covers_the_whole_early_window():
    """A reveal may land max_offset_frames early, so the reference must sit before that window."""
    assert cue_reach(CFG, FPS) == pytest.approx(0.14)
    wide = dataclasses.replace(CFG, reference_lead_seconds=0.5)
    assert cue_reach(wide, FPS) == 0.5


def test_the_reference_frame_sits_before_the_cue_and_outside_the_fade():
    assert reference_time(0.0, 1.0, False, 0.2, CFG, FPS) == pytest.approx(0.86)
    # Inside the fade-in the reference is pushed to the end of the dip.
    assert reference_time(0.0, 0.3, True, 0.2, CFG, FPS) == pytest.approx(0.2)
    # A cue inside the fade-in leaves no frame at all.
    assert reference_time(0.0, 0.2, True, 0.2, CFG, FPS) is None
    assert reference_time(0.0, 0.0, False, 0.0, CFG, FPS) is None


def test_the_control_spans_end_at_the_reference_and_stop_at_the_floor():
    def rounded(before, span, floor):
        return [(round(a, 3), round(b, 3)) for a, b in control_spans(before, span, floor)]

    assert rounded(2.0, 0.2, 0.0) == [(1.8, 2.0), (1.6, 1.8)]
    assert rounded(0.3, 0.2, 0.0) == [(0.1, 0.3)]
    assert rounded(0.1, 0.2, 0.0) == []


def test_a_thin_pass_is_one_a_slightly_smaller_reveal_would_fail():
    cfg = Settings().verify
    assert cfg.thin_change_factor == 3.0
    assert thin_change(0.11, 0.11, cfg) and thin_change(0.29, 5.0, cfg) and thin_change(5.0, 0.29, cfg)
    assert not thin_change(0.3, 0.3, cfg) and not thin_change(6.56, 6.56, cfg)
    assert not thin_change(0.11, 0.11, dataclasses.replace(cfg, thin_change_factor=1.0))


def a_cue_times(**sections) -> CueTimes:
    return CueTimes(sections={k: [CueTime(cue=c, on=c, at=t) for c, t in v.items()] for k, v in sections.items()})


def test_the_default_checks_are_every_resolved_cue_in_section_then_time_order():
    cue_times = a_cue_times(**{"02": {"c": 2.0}, "01": {"b": 3.0, "a": 1.0}})
    assert default_checks(cue_times) == ["1:a", "1:b", "2:c"]
    assert default_checks(cue_times, only=[2]) == ["2:c"]


def test_a_malformed_check_names_the_shape_it_wanted(tmp_path):
    from decktalk.model import Project

    (tmp_path / "decktalk.toml").write_text("[[section]]\nnumber = 1\npage = 'a.html'\n", encoding="utf-8")
    project = Project.load(tmp_path, environ={})
    from decktalk.stages.verify import wanted_checks

    with pytest.raises(ConfigError, match="is not a section and a cue") as malformed:
        wanted_checks(project, ["3.1draw"], None)
    assert malformed.value.hint is not None and "SECTION:CUE" in malformed.value.hint
    with pytest.raises(ConfigError, match="is not a section and a cue"):
        wanted_checks(project, ["x:3.1draw"], None)
    # A named cue is measured although cues.json opts it out, so nothing is skipped for that reason.
    assert wanted_checks(project, ["1:a"], None) == (["1:a"], set())
    assert wanted_checks(project, [], None) == ([], set())


def test_a_skipped_row_carries_its_reason_and_measures_nothing():
    row = skipped("1:a", SkipReason.TOO_CLOSE_TO_END, "every probe falls past the section end")
    assert row.skipped and row.ok and row.verdict == Verdict.SKIPPED
    assert row.reason == SkipReason.TOO_CLOSE_TO_END
    assert row.note.startswith("skipped TOO_CLOSE_TO_END:")
    assert row.to_dict()["changed_percent"] is None


def test_a_cue_row_names_its_section_and_its_cue():
    row = CueCheck("12:12.1draw", 1.0, 5.0, 9.0, 1.0, True)
    assert (row.section, row.cue) == (12, "12.1draw")
    assert row.verdict == Verdict.CHANGED and not row.skipped
    assert CueCheck("1:a", 1.0, 1.0, 0.0, 0.0, False).verdict == Verdict.NO_CHANGE
    assert CueCheck("1:a", 1.0, 1.0, 9.0, 0.0, False, offset_ms=400).verdict == Verdict.OFF_CUE


def test_the_onset_is_the_frame_whose_changed_share_jumps():
    series = [(1.0, 0.0), (1.04, 0.0), (1.08, 0.0), (1.12, 4.0), (1.16, 4.2)]
    blocks = dict.fromkeys([t for t, _ in series], 1.0)
    assert onset_offset_ms(series, 1.0, 1.12, 0.5, blocks=blocks) == 0
    # A share that only creeps up is motion, not a reveal, so the floor rule finds it instead.
    creep = [(1.0, 0.0), (1.04, 0.1), (1.08, 0.2), (1.12, 0.3), (1.16, 0.4)]
    assert onset_offset_ms(creep, 1.0, 1.12, 0.5, blocks=dict.fromkeys([t for t, _ in creep], 1.0)) is None
    # A frame whose blocks did not change is the encoder's ringing, never the onset.
    ringing = dict.fromkeys([t for t, _ in series], 1.0) | {1.12: 0.0}
    assert onset_offset_ms(series, 1.0, 1.12, 0.5, blocks=ringing) == 40
