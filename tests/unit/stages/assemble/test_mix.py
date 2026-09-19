"""The soundtrack as one filter graph: its inputs, its narration runs and its delays."""

from __future__ import annotations

import dataclasses

import pytest

from decktalk.artifacts import Take, Takes, Timeline, TimelineSection, Word, write_words
from decktalk.model import Project


def test_a_marker_falls_where_its_phrase_plays_in_the_final_file(tmp_path):
    """The music answers to a spoken word, and a section lead moves that word without moving the take."""
    from decktalk.model.markers import Marker
    from decktalk.stages.assemble.mix import resolve_marker_time

    write_words(tmp_path / "02-close.words.json", [Word("a", 0.0, 0.4), Word("b", 1.0, 1.6)])
    takes = Takes(script="script.md", model="m", output_format="mp3")
    takes.sections["02"] = Take(2, "Close", "02-close.mp3", "02-close.words.json", "h", 2, 2.0, 2.0)
    marker = Marker(name="turn", section=2, on="b")
    # The section starts ten seconds in, its lead is 1.25 seconds of silence, and `b` is spoken one second later.
    assert resolve_marker_time(marker, {"02": 10.0}, takes, tmp_path, {"02": 1.25}) == pytest.approx(12.25)
    assert resolve_marker_time(marker, {"02": 10.0}, takes, tmp_path) == pytest.approx(11.0)
    assert resolve_marker_time(Marker(name="gone", section=9), {"02": 10.0}, takes, tmp_path) is None


def test_plan_mix_delays_clip_audio_and_drops_the_limiter(tmp_path, write_project):
    from decktalk.stages.assemble.cut import RenderedSection
    from decktalk.stages.assemble.mix import mix_input_args, plan_mix
    from decktalk.stages.assemble.publish import build_chapters

    p = Project.load(write_project(tmp_path), environ={})
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=4.0,
        sections={
            "01": TimelineSection("Open", 0, 2.0, 2.0, 1.8),
            "02": TimelineSection("Close", 2.0, 4.0, 2.0, 3.8),
        },
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "00.mp4", 3.0, "00.mp4 (own audio)", audio=tmp_path / "open.mp4"),
        RenderedSection(p.sections[1], tmp_path / "01.mp4", 2.0, "01.webm"),
        RenderedSection(p.sections[2], tmp_path / "02.mp4", 3.5, "02.webm"),
    ]
    plan = plan_mix(p, rows, tl, soundscape=False)
    assert plan.total == 8.5
    assert [(i.mode, i.path) for i in plan.inputs][0] == ("lavfi", "anullsrc=r=48000:cl=stereo")
    assert mix_input_args(plan)[:5] == ["-f", "lavfi", "-t", "8.500", "-i"]
    assert "alimiter" not in plan.filter
    assert "adelay=3000:all=1[narr]" in plan.filter  # narration starts with the first page section
    assert "atrim=duration=3.000" in plan.filter and "afade=t=in:d=0.02,afade=t=out:st=2.980:d=0.02" in plan.filter
    assert "adelay=0:all=1[clip00]" in plan.filter
    assert plan.filter.endswith("[anchor][narr][clip00]amix=inputs=3:duration=first:normalize=0[a]")
    chapters = build_chapters(rows, p.chapters())
    assert [(c.start, c.end, c.title) for c in chapters] == [
        (0.0, 3.0, "Section 0"),
        (3.0, 5.0, "Section 1"),
        (5.0, 8.5, "Section 2"),
    ]
    paths = p.workspace.output_paths()
    assert paths["srt"].name == "t.srt" and paths["vtt"].name == "t.vtt" and paths["chapters"].name == "t.chapters.txt"


def test_narration_runs_pause_for_a_clip_between_page_sections(tmp_path, mid_clip_plan):
    from decktalk.stages.assemble.cut import rendered_starts
    from decktalk.stages.assemble.mix import NarrationRun, narration_offsets, narration_runs

    p, tl, rows = mid_clip_plan(tmp_path)
    starts = rendered_starts(rows)
    assert starts == {"01": 0.0, "02": 2.0, "03": 5.0, "04": 7.52}
    assert narration_runs(rows, tl, starts) == [
        NarrationRun(keys=("01",), at=0.0, start=0.0, end=2.0),
        NarrationRun(keys=("03", "04"), at=5.0, start=2.0, end=None),
    ]
    # Every section after the clip hears its words one clip later than the track holds them.
    assert narration_offsets(rows, tl, starts) == {"01": 0.0, "03": 3.0, "04": 3.0}
    # Without a clip between page sections there is one run, and every section shares its offset.
    edge = [rows[1], rows[0], rows[2], rows[3]]
    edge_starts = rendered_starts(edge)
    assert len(narration_runs(edge, tl, edge_starts)) == 1
    assert narration_offsets(edge, tl, edge_starts) == {"01": 3.0, "03": 3.0, "04": 3.0}


def test_plan_mix_places_each_narration_run_at_its_section_start(tmp_path, mid_clip_plan):
    from decktalk.stages.assemble.mix import plan_mix

    p, tl, rows = mid_clip_plan(tmp_path)
    plan = plan_mix(p, rows, tl, soundscape=False)
    assert plan.total == 9.0
    narration = str(p.narration_dir / "narration.mp3")
    assert [path for mode, path in ((i.mode, i.path) for i in plan.inputs)] == [
        "anullsrc=r=48000:cl=stereo",
        narration,
        narration,
        str(rows[1].audio),
    ]
    assert "atrim=start=0.000:end=2.000,asetpts=PTS-STARTPTS,adelay=0:all=1[narr0]" in plan.filter
    assert "atrim=start=2.000,asetpts=PTS-STARTPTS,adelay=5000:all=1[narr1]" in plan.filter
    assert "adelay=2000:all=1[clip02]" in plan.filter
    assert plan.filter.endswith("[anchor][narr0][narr1][clip02]amix=inputs=4:duration=first:normalize=0[a]")
    # The music ducks under each section where it plays, and under the clip.
    music = tmp_path / "music.mp3"
    music.write_bytes(b"x")
    p.document = dataclasses.replace(p.document, mix=type(p.mix)(music=str(music)))
    ducked = plan_mix(p, rows, tl, soundscape=True).filter
    for a, b in [(0.0, 1.6), (5.0, 7.1), (7.5, 8.8), (2.0, 5.0)]:
        assert f"(t-{a:.3f})" in ducked and f"({b:.3f}-t)" in ducked, (a, b)


def test_a_hold_between_page_sections_pauses_the_narration(tmp_path, spoken, write_project):
    from decktalk.stages.assemble.cut import RenderedSection, rendered_starts
    from decktalk.stages.assemble.mix import NarrationRun, narration_offsets, narration_runs, plan_mix
    from decktalk.stages.assemble.publish import build_captions

    toml = (
        "[[section]]\nnumber = 1\npage = 'a.html'\nhold_seconds = 2\n[[section]]\nnumber = 2\npage = 'a.html'\n"
        "[[section]]\nnumber = 3\npage = 'a.html'\nhold_seconds = 1\n"
    )
    p = Project.load(write_project(tmp_path, toml), environ={})
    tl = Timeline(
        narration="narration.mp3",
        total_seconds=6.0,
        sections={
            "01": TimelineSection("A", 0.0, 2.0, 2.0, 1.6, spoken("alpha beta", 0.7)),
            "02": TimelineSection("B", 2.0, 4.5, 2.5, 4.1, spoken("gamma delta", 2.1)),
            "03": TimelineSection("C", 4.5, 6.0, 1.5, 5.8, spoken("epsilon", 4.6)),
        },
    )
    rows = [
        RenderedSection(p.sections[0], tmp_path / "01.mp4", 4.0, "01.webm"),  # 2 s of narration and a 2 s hold
        RenderedSection(p.sections[1], tmp_path / "02.mp4", 2.52, "02.webm"),
        RenderedSection(p.sections[2], tmp_path / "03.mp4", 2.48, "03.webm"),
    ]
    starts = rendered_starts(rows)
    assert narration_runs(rows, tl, starts) == [
        NarrationRun(keys=("01",), at=0.0, start=0.0, end=2.0),
        NarrationRun(keys=("02", "03"), at=4.0, start=2.0, end=None),
    ]
    offsets = narration_offsets(rows, tl, starts)
    assert offsets == {"01": 0.0, "02": 2.0, "03": 2.0}
    plan = plan_mix(p, rows, tl, soundscape=False)
    assert "atrim=start=0.000:end=2.000,asetpts=PTS-STARTPTS,adelay=0:all=1[narr0]" in plan.filter
    assert "atrim=start=2.000,asetpts=PTS-STARTPTS,adelay=4000:all=1[narr1]" in plan.filter
    assert [(c.text, c.start) for c in build_captions(tl, offsets)] == [
        ("alpha beta", 0.7),
        ("gamma delta", 4.1),
        ("epsilon", 6.6),
    ]
