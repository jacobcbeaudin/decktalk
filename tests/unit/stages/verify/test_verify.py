"""The verify stage over an assembled project, with every ffmpeg measurement replaced by a number."""

from __future__ import annotations

import dataclasses
import importlib
import json
from pathlib import Path

import pytest

from decktalk.cli import main
from decktalk.errors import ConfigError
from decktalk.model import Project
from decktalk.settings import Settings
from decktalk.stages.verify import verify
from decktalk.verdicts import Findings, SkipReason, Verdict


def test_onset_offset_finds_the_jump_and_falls_back_to_the_floor():
    from decktalk.stages.verify.plan import onset_offset_ms

    # A fade of a small element, as measured on the scaffold: change begins 60 ms after the cue
    # but only crosses a tenth of the picture 220 ms after it.
    fade = [(9.2, 0.0), (9.24, 0.0), (9.28, 0.0), (9.32, 0.0), (9.36, 0.018), (9.4, 0.038), (9.52, 0.103)]
    assert onset_offset_ms(fade, before=9.2, cue_at=9.3, onset=0.01) == 60
    assert onset_offset_ms(fade, before=9.2, cue_at=9.3, onset=0.1) == 220
    # A camera push is a slope: the share grows a little every frame and never jumps, so
    # the onset is the first real jump, even though the slope crosses the threshold earlier.
    push = [(9.2, 0.0), (9.24, 0.006), (9.28, 0.012), (9.32, 0.018), (9.36, 0.06), (9.4, 0.07)]
    assert onset_offset_ms(push, before=9.2, cue_at=9.3, onset=0.02) == 60
    # A reveal that lands a frame early reports a negative offset rather than being hidden.
    early = [(9.2, 0.0), (9.24, 0.0), (9.28, 0.3), (9.32, 0.3), (9.36, 0.3)]
    assert onset_offset_ms(early, before=9.2, cue_at=9.3, onset=0.02) == -20
    assert onset_offset_ms([(9.2, 0.0), (9.24, 0.0)], before=9.2, cue_at=9.3, onset=0.01) is None
    # When the reference time falls between frames, the series starts on the frame after it,
    # which is the reference itself, and a reveal on the very next frame is still the onset.
    off_grid = [(9.24, 0.0), (9.28, 0.23), (9.32, 0.26), (9.36, 0.26)]
    assert onset_offset_ms(off_grid, before=9.21, cue_at=9.31, onset=0.002) == -30
    # Encoder ringing, as measured on the scaffold's 3:3.1again: two frames before the reveal change
    # a few pixels, but no block changes, so the onset is the reveal itself, not 100 ms early.
    ringing = [(9.16, 0.0), (9.2, 0.1065), (9.24, 0.0455), (9.28, 1.6088), (9.32, 2.2168)]
    blocks = {9.16: 0.0, 9.2: 0.0, 9.24: 0.0, 9.28: 1.926, 9.32: 2.793}
    assert onset_offset_ms(ringing, before=9.16, cue_at=9.3, onset=0.01) == -100
    assert onset_offset_ms(ringing, before=9.16, cue_at=9.3, onset=0.01, blocks=blocks) == -20
    assert Settings().verify.max_offset_frames == 2 and Settings().verify.onset_percent == 0.01


def test_reference_time_skips_the_fade_and_keeps_the_lead():
    from decktalk.settings import VerifyConfig
    from decktalk.stages.verify.plan import reference_time

    cfg = VerifyConfig()  # lead_seconds 0.1
    # The lead clears a reveal that lands max_offset_frames (2) early: (2 + 1.5) / 25 = 0.14 s.
    assert reference_time(10.0, 2.0, False, 0.16, cfg, 25) == 11.86
    assert reference_time(10.0, 0.05, False, 0.16, cfg, 25) == 10.0  # the section's first frame, a frame early
    assert reference_time(10.0, 0.2, True, 0.16, cfg, 25) == 10.16  # the first frame after the fade-in
    assert reference_time(10.0, 0.15, True, 0.16, cfg, 25) is None  # the cue sits inside the fade-in
    assert reference_time(10.0, 0.0, False, 0.16, cfg, 25) is None  # a $start cue has no frame before it


def test_verify_default_checks_come_from_cue_times_json(verify_project, monkeypatch):

    p = verify_project({"02": "c@2.0", "01": "b@3.0,a@1.0"})
    result = verify(p)
    assert [c.check for c in result.cues] == ["1:a", "1:b", "2:c"]  # section order, then cue time
    assert all(c.verdict == Verdict.NO_CHANGE for c in result.cues) and not result.ok
    assert [c.check for c in verify(p, only=[2]).cues] == ["2:c"]
    assert verify(p, checks=[]).cues == [] and verify(p, checks=[]).ok
    assert [c.check for c in verify(p, checks=["1:b", "2:c"], only=[1]).cues] == ["1:b"]
    missing = verify(p, checks=["1:nope"]).cues[0]
    assert missing.verdict == Verdict.UNRESOLVED and not missing.ok and missing.cue_seconds is None


def test_verify_skips_clamped_start_cue(verify_project, monkeypatch):

    p = verify_project({"01": "start@0.0", "03": "later@1.0"}, change=5.0)
    result = verify(p)
    start, later = result.cues
    assert (start.verdict, start.reason) == (Verdict.SKIPPED, SkipReason.REFERENCE_CLAMPED)
    assert start.cue_seconds is None and start.note.startswith("skipped REFERENCE_CLAMPED")
    assert (later.verdict, later.reason) == (Verdict.SKIPPED, SkipReason.SECTION_NOT_ASSEMBLED)
    assert result.ok  # Skipped rows never fail.


def test_verify_opted_out_cue_is_skipped(verify_project, monkeypatch):

    cues = {"1": {"cues": [{"cue": "a", "on": "hello", "verify": False}, {"cue": "b", "on": "there"}]}}
    p = verify_project({"01": "a@1.0,b@2.0"}, cues=cues)
    assert [c.verify for c in p.cue_specs()[0].cues] == [False, True]
    a, b = verify(p).cues
    assert (a.verdict, a.reason) == (Verdict.SKIPPED, SkipReason.OPTED_OUT) and b.verdict == Verdict.NO_CHANGE
    (named,) = verify(p, checks=["1:a"]).cues  # A cue named on purpose is measured anyway.
    assert named.verdict == Verdict.NO_CHANGE and named.reason is None
    bad = {"sections": {"1": {"cues": [{"cue": "a", "on": "x", "verify": 0}]}}}
    (p.root / "cues.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ConfigError, match="'verify' must be bool, got int"):
        p.cue_specs()


def test_verify_marks_a_thin_change_as_uncertain(verify_project, monkeypatch, capsys):

    from decktalk.stages.verify import thin_change, verify

    verify_module = importlib.import_module("decktalk.stages.verify.measure")

    cfg = Settings().verify
    assert cfg.thin_change_factor == 3.0
    assert thin_change(0.11, 0.11, cfg) and thin_change(0.29, 5.0, cfg) and thin_change(5.0, 0.29, cfg)
    assert not thin_change(0.3, 0.3, cfg) and not thin_change(6.56, 6.56, cfg)
    assert not thin_change(0.11, 0.11, dataclasses.replace(cfg, thin_change_factor=1.0))

    p = verify_project({"01": "a@1.0"}, change=0.11)
    (row,) = verify(p).cues
    assert (row.verdict, row.ok, row.reason) == (Verdict.THIN_CHANGE, True, None)
    assert verify(p).ok and row.to_dict()["verdict"] == Verdict.THIN_CHANGE.value
    # The onset branch keeps the thin verdict when on time, and OFF CUE still wins when late.
    monkeypatch.setattr(verify_module, "first_change_offset", lambda *a, **kw: 0)
    assert [c.verdict for c in verify(p).cues] == [Verdict.THIN_CHANGE]
    monkeypatch.setattr(verify_module, "first_change_offset", lambda *a, **kw: 400)
    assert [c.verdict for c in verify(p).cues] == [Verdict.OFF_CUE]
    monkeypatch.setattr(verify_module, "first_change_offset", lambda *a, **kw: None)

    # An uncertain finding: exit 0, and 1 only with --strict. The table and the JSON both show it.
    assert main(["-p", str(p.root), "verify"]) == 0
    assert Verdict.THIN_CHANGE.value in capsys.readouterr().out
    assert main(["-p", str(p.root), "verify", "--strict"]) == 1
    capsys.readouterr()
    assert main(["-p", str(p.root), "verify", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["findings"] == {"certain": 0, "uncertain": 1}
    assert doc["verify"]["cues"][0]["verdict"] == Verdict.THIN_CHANGE.value

    # A clear change reads changed, and the factor can turn the warning off.
    monkeypatch.setattr("decktalk.media.frames.changed_pixels_percent", lambda path, t1, t2, **kw: 0.5)
    assert [c.verdict for c in verify(p).cues] == [Verdict.CHANGED]
    monkeypatch.setattr("decktalk.media.frames.changed_pixels_percent", lambda path, t1, t2, **kw: 0.11)
    monkeypatch.setenv("DECKTALK_VERIFY_THIN_CHANGE_FACTOR", "1")
    assert [c.verdict for c in verify(Project.load(p.root)).cues] == [Verdict.CHANGED]


def test_verify_to_dict_is_json_serialisable_and_relative(verify_project, monkeypatch):
    from decktalk.stages.verify import CueCheck

    p = verify_project({"01": "start@0.0,a@1.23456"})
    d = json.loads(json.dumps(verify(p).to_dict(p.root)))
    assert d["final"] == "build/out/t.mp4" and d["total_seconds"] == 10.0 and d["silent"] is False
    first = {"key": "01", "start": 0.0, "probe_at": 0.2, "yavg": 100.0, "ymax": 200.0, "verdict": "ok"}
    assert d["starts"][0] == first and d["cuts"] == []
    start, a = d["cues"]
    assert start["section"] == 1 and start["cue"] == "start" and start["reason"] == "REFERENCE_CLAMPED"
    assert a["cue_seconds"] == 1.235 and a["verdict"] == "NO CHANGE" and a["reason"] is None
    row = CueCheck("3:3.1eq", 15.6612, 72.38123, 0.29444, 0.0, True, "", -20, -5).to_dict()
    assert row == {
        "section": 3,
        "cue": "3.1eq",
        "cue_seconds": 15.661,
        "final_seconds": 72.381,
        "changed_percent": 0.29,
        "control_percent": 0.0,
        "offset_ms": -20,
        "av_ms": -5,
        "verdict": "changed",
        "reason": None,
    }


def test_probe_plan_fits_the_gap_between_close_cues_and_keeps_well_spaced_cues():
    from decktalk.stages.verify.plan import probe_plan, reference_time

    cfg = Settings().verify
    fps = 25
    # The demo's section 1: 1.1four fires 0.56 s after 1.1three, in a run of counting cues.
    cues = {"bowl": 0.86, "ball": 1.76, "count": 3.58, "one": 4.42, "two": 5.11, "three": 5.62, "four": 6.18}
    cues["word"] = 10.52

    def plan(cue: str, end: float = 30.0) -> tuple[list[float], bool]:
        before = reference_time(0.0, cues[cue], False, 0.0, cfg, fps)
        assert before is not None
        others = [t for c, t in cues.items() if c != cue]
        return probe_plan(cues[cue], before, 0.0, end, others, cfg, fps)

    # Both control spans of 1.1four's probes hold an earlier count, so its probe and control fit after 1.1three.
    assert plan("four") == ([0.14], True)
    # The later probe of 1.1count would reach 1.1one's reveal, so it stops where that reveal can begin.
    assert plan("count") == ([0.7], True)
    # A well-spaced cue keeps probe_delays exactly, and so does every cue with no neighbor.
    assert plan("word") == ([0.7, 1.5], False)
    assert plan("word", end=11.5) == ([0.7], False)
    assert probe_plan(6.18, 6.04, 0.0, 30.0, [], cfg, fps) == ([0.7, 1.5], False)
    # A cue within the reference lead is the same reveal, and a gap too short for any probe keeps probe_delays.
    assert probe_plan(6.18, 6.04, 0.0, 30.0, [6.1, 6.3], cfg, fps) == ([0.7, 1.5], False)
    assert probe_plan(6.18, 6.04, 0.0, 30.0, [6.4], cfg, fps) == ([0.7, 1.5], False)


def test_reference_sits_before_an_early_reveal_the_offset_limit_allows():

    from decktalk.stages.verify.plan import reference_time

    cfg = Settings().verify
    fps = 25
    # A reveal may land max_offset_frames early, so the reference must sit before that window.
    earliest_allowed = 10.0 - cfg.max_offset_frames / fps
    ref = reference_time(0.0, 10.0, False, 0.0, cfg, fps)
    assert ref is not None and ref <= earliest_allowed - 1.0 / fps
    # A wider limit pushes the reference further back.
    wide = dataclasses.replace(cfg, max_offset_frames=4)
    ref_wide = reference_time(0.0, 10.0, False, 0.0, wide, fps)
    assert ref_wide is not None and ref_wide <= 10.0 - 4 / fps - 1.0 / fps
    # A cue close to the section start still clamps to one frame before the cue.
    assert reference_time(0.0, 0.08, False, 0.0, cfg, fps) is not None


def test_verify_and_assemble_ignore_a_leftover_section_video(verify_project, tmp_path, monkeypatch, caplog):
    """A sections/04.mp4 left after sections were renumbered is not counted as a section."""
    from decktalk.artifacts import Timeline, TimelineSection, Word

    asm = importlib.import_module("decktalk.stages.assemble")
    p = verify_project({"01": "a@1.0"})
    (p.sections_dir / "04.mp4").write_bytes(b"x")
    (p.out_dir / "t-20260101-0000.mp4").write_bytes(b"x")  # files outside build/sections are not section videos
    assert p.stray_section_videos() == [p.sections_dir / "04.mp4"]

    with caplog.at_level("WARNING", logger="decktalk"):
        result = verify(p, checks=[])
    assert [s.key for s in result.starts] == ["01", "02"] and result.total_seconds == 10.0
    assert [r.getMessage() for r in caplog.records] == [
        "build/sections/04.mp4 is not a section in decktalk.toml, so verify ignores it. "
        "Delete the file if an earlier build left it."
    ]

    (p.root / "script.md").write_text("## 1. A\n\nHi.\n\n## 2. B\n\nYes.\n\n## 3. C\n\nNo.\n", encoding="utf-8")
    Timeline(
        narration="narration.mp3",
        total_seconds=5.0,
        sections={"01": TimelineSection("A", 0, 5.0, 5.0, 1.0, [Word("Hi", 0.7, 1.0)])},
        estimated=True,
    ).save(p.timeline_path)
    rows = [asm.RenderedSection(p.sections[0], p.sections_dir / "01.mp4", 5.0, "page")]
    monkeypatch.setattr(asm, "render_sections", lambda project, timeline, strict: rows)
    monkeypatch.setattr(asm, "concat", lambda files, out: out.write_bytes(b"x"))
    monkeypatch.setattr(asm, "mux_chapters", lambda src, chapters, dst: dst.write_bytes(b"x"))
    monkeypatch.setattr(asm.ffmpeg, "run", lambda *args: Path(args[-1]).write_bytes(b"x"))
    assert asm.assemble(p, soundscape=False).warnings == [
        "build/sections/04.mp4 is not a section in decktalk.toml, so assemble ignores it. "
        "Delete the file if an earlier build left it."
    ]


def test_the_recordings_table_repeats_what_each_recording_log_judged(verify_project):
    """A page that never typeset its equations is a certain finding long after the build that recorded it."""
    from decktalk.artifacts import Luma, RecordingChecks, RecordingLog

    p = verify_project({"01": "a@1.0"})
    checks = RecordingChecks(5.0, 5.0, Luma(90.0, 90.0, 90.0, 200.0), (Verdict.KATEX_NOT_LOADED,))
    RecordingLog(
        url="http://project.localhost/deck/index.html?scene=1",
        requested_seconds=5.0,
        settle_seconds=0.5,
        load_seconds=0.1,
        clock_start_seconds=1.5,
        t0_seconds=1.44,
        t0_method="cover (36 magenta frames)",
        checks=checks,
        warnings=["KaTeX did not load within 5 s, so [data-tex] elements stay plain text"],
    ).save(p.recording_log(p.page_sections[0]))

    result = verify(p, checks=[])
    [row] = result.recordings
    assert (row.key, row.verdicts, row.ok) == ("01", (Verdict.KATEX_NOT_LOADED,), False)
    assert row.where == "build/recordings/01.json" and row.t0_method.startswith("cover")
    assert result.findings == Findings(certain=1) and not result.ok
    assert result.to_dict(p.root)["recordings"][0]["verdicts"] == ["KATEX_NOT_LOADED"]
    # `--only` keeps the table to the sections it names, as it does every other table.
    assert verify(p, checks=[], only=[2]).recordings == []
