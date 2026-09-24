"""The whole soundtrack as one graph: the anchor, the narration runs, the clips, the beds and the effects."""

from __future__ import annotations

import pytest

from decktalk.inputs.markers import Marker
from decktalk.stages.assemble.mix import (
    LAVFI,
    LOOP,
    ONCE,
    MixInput,
    MixPlan,
    delay,
    gain,
    max_expr,
    mix_input_args,
    plan_mix,
    ramp_expr,
    resolve_marker_time,
    speech_spans,
)

from .conftest import MID_CLIP_TOML

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")

THREE_PAGES = {1: 2.0, 2: 2.5, 3: 1.5}
"""Three page sections of the lengths the take index below gives them."""


def three_page_plan(inputs, opened, take_index, spoken, rendered, *, soundscape: bool = True) -> MixPlan:
    takes = take_index(
        inputs,
        {
            1: ("A", 2.0, 1.6, spoken("alpha beta")),
            2: ("B", 2.5, 2.1, spoken("gamma delta")),
            3: ("C", 1.5, 1.3, spoken("epsilon")),
        },
    )
    return plan_mix(inputs, opened.run, rendered(inputs, THREE_PAGES), takes, soundscape=soundscape)


def test_a_level_becomes_the_factor_that_plays_it():
    assert gain(0) == 1.0
    assert round(gain(-6), 3) == 0.501
    assert round(gain(20), 3) == 10.0


def test_a_second_becomes_the_milliseconds_adelay_reads():
    assert delay(0.0) == "adelay=0:all=1"
    assert delay(2.5) == "adelay=2500:all=1"
    assert delay(0.0006) == "adelay=1:all=1"


def test_a_ramp_is_zero_outside_its_span_and_one_inside_it():
    assert ramp_expr(1.0, 2.0, 0.2) == "min(1,max(0,(t-1.000)/0.2))*min(1,max(0,(2.000-t)/0.2))"


def test_the_largest_of_no_ramps_is_nothing_at_all():
    assert max_expr([]) == "0"
    assert max_expr(["a"]) == "a"
    assert max_expr(["a", "b", "c"]) == "max(max(a,b),c)"


def test_a_silent_anchor_of_the_pictures_length_fixes_the_mix(tmp_path, write_project, open_run, take_index, spoken,
                                                              rendered):  # fmt: skip
    """The picture carries no audio, so without the anchor the mix is as long as its longest layer."""
    inputs = write_project(tmp_path)
    plan = three_page_plan(inputs, open_run(tmp_path), take_index, spoken, rendered)
    assert plan.inputs[0].mode == LAVFI
    assert plan.inputs[0].path == "anullsrc=r=48000:cl=stereo"
    assert plan.total == 6.0
    assert plan.filter.startswith("[1:a]aresample=48000,aformat=channel_layouts=stereo[anchor]")
    assert plan.filter.endswith("amix=inputs=2:duration=first:normalize=0[a]")


def test_one_unbroken_run_of_pages_plays_the_whole_track_once(tmp_path, write_project, open_run, take_index, spoken,
                                                              rendered):  # fmt: skip
    inputs = write_project(tmp_path)
    plan = three_page_plan(inputs, open_run(tmp_path), take_index, spoken, rendered)
    narration = [layer for layer in plan.inputs if layer.path.endswith("narration.mp3")]
    assert len(narration) == 1
    assert "[narr]" in plan.filter
    assert "atrim=start=" not in plan.filter


def test_a_clip_between_two_pages_splits_the_narration_into_its_own_runs(
    tmp_path, write_project, open_run, take_index, spoken, rendered
):
    """The narration pauses for a clip, so each run of page sections plays its own stretch of the track."""
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    opened = open_run(tmp_path)
    takes = take_index(
        inputs,
        {
            1: ("A", 2.0, 1.6, spoken("alpha beta")),
            3: ("C", 2.5, 2.1, spoken("gamma delta")),
            4: ("D", 1.5, 1.3, spoken("epsilon")),
        },
    )
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5, 4: 1.5}, audio={2: tmp_path / "media" / "broll.mp4"})
    plan = plan_mix(inputs, opened.run, rows, takes, soundscape=True)
    assert "[narr0]" in plan.filter
    assert "[narr1]" in plan.filter
    assert "atrim=start=0.000:end=2.000" in plan.filter
    # The second run opens where its own first section opens in the film, which is after the clip.
    assert "adelay=5000:all=1" in plan.filter


def test_a_clips_own_audio_lands_at_its_section_start_and_fades_at_both_ends(
    tmp_path, write_project, open_run, take_index, spoken, rendered
):
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    rows = rendered(inputs, {1: 2.0, 2: 3.0}, audio={2: tmp_path / "media" / "broll.mp4"})
    plan = plan_mix(inputs, opened.run, rows, takes, soundscape=True)
    assert "[clip02]" in plan.filter
    assert "afade=t=in:d=0.02" in plan.filter
    assert "afade=t=out:st=2.980:d=0.02" in plan.filter
    assert "atrim=duration=3.000" in plan.filter


def test_a_music_bed_the_project_names_and_has_not_got_is_a_certain_finding(
    tmp_path, write_project, open_run, take_index, spoken, rendered
):
    """A film mixed without the music it declares is not the film the project asked for."""
    toml = MID_CLIP_TOML + '\n[mix]\nmusic = "media/bed.mp3"\n'
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, soundscape=True)
    assert opened.codes() == ["FILE_MISSING"]
    said = next(line.finding for line in opened.lines if getattr(line, "finding", None) is not None)
    assert "media/bed.mp3" in said.message
    assert said.location.where == "media/bed.mp3"
    assert "[music]" not in plan.filter


def test_a_run_that_asks_for_no_soundscape_lays_no_bed_and_judges_nothing(
    tmp_path, write_project, open_run, take_index, spoken, rendered
):
    toml = MID_CLIP_TOML + '\n[mix]\nmusic = "media/bed.mp3"\n'
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, soundscape=False)
    assert opened.codes() == []
    assert "[music]" not in plan.filter


def test_an_effect_lands_at_the_second_its_own_cue_resolved_to(
    tmp_path, write_project, open_run, take_index, spoken, rendered, cue_times
):
    toml = (
        "[project]\nname = 't'\n[narration]\nlead_seconds = 0\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n"
        "[[section]]\nnumber = 2\npage = 'deck/index.html'\nscene = '2'\n"
        '[[mix.effects]]\nfile = "media/ping.mp3"\nsection = 2\ncue = "2.1:ping"\ndb = -16\n'
    )
    inputs = write_project(tmp_path, toml)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "ping.mp3").write_bytes(b"")
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("a b")), 2: ("B", 2.0, 1.6, spoken("c d"))})
    cue_times(inputs, {2: {"2.1:ping": 0.5}})
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0, 2: 2.0}), takes, soundscape=True)
    assert "[effect0]" in plan.filter
    # Section 2 opens at 2.0 s and the cue sits half a second into it.
    assert "adelay=2500:all=1" in plan.filter


def test_an_effect_whose_cue_is_unresolved_is_said_and_never_played(
    tmp_path, write_project, open_run, take_index, spoken, rendered
):
    toml = (
        "[project]\nname = 't'\n[narration]\nlead_seconds = 0\n"
        "[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n"
        '[[mix.effects]]\nfile = "media/ping.mp3"\nsection = 1\ncue = "1.1:ping"\n'
    )
    inputs = write_project(tmp_path, toml)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "ping.mp3").write_bytes(b"")
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("a b"))})
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, soundscape=True)
    assert "[effect0]" not in plan.filter
    assert any("is unresolved" in note for note in opened.notes())


def test_speech_spans_cover_every_spoken_section_and_every_clip(tmp_path, write_project, take_index, spoken, rendered):
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    takes = take_index(
        inputs,
        {1: ("A", 2.0, 1.6, spoken("alpha beta")), 3: ("C", 2.5, 2.1, spoken("gamma delta"))},
    )
    rows = rendered(inputs, {1: 2.0, 2: 3.0, 3: 2.5})
    starts = {1: 0.0, 2: 2.0, 3: 5.0}
    spans = speech_spans(rows, takes, starts)
    # Each spoken section speaks until its last word, and the clip speaks for the whole of its length.
    assert spans[0] == (0.0, 1.6)
    assert (2.0, 5.0) in spans


def test_a_marker_resolves_against_the_words_of_its_own_section(tmp_path, write_project, take_index, spoken):
    inputs = write_project(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta gamma"))})
    starts = {1: 4.0}
    assert resolve_marker_time(Marker(name="m", section=1, on="$start"), starts, takes, inputs) == 4.0
    assert resolve_marker_time(Marker(name="m", section=1, on="beta"), starts, takes, inputs) == 4.4
    assert resolve_marker_time(Marker(name="m", section=1, on="nowhere"), starts, takes, inputs) is None
    assert resolve_marker_time(Marker(name="m", section=9, on="$start"), starts, takes, inputs) is None


def test_the_input_arguments_follow_the_order_the_graph_indexes_them():
    plan = MixPlan(
        inputs=(MixInput(LAVFI, "anullsrc"), MixInput(ONCE, "a.mp3"), MixInput(LOOP, "bed.mp3")),
        filter="",
        total=3.5,
    )
    assert mix_input_args(plan) == [
        "-f", "lavfi", "-t", "3.500", "-i", "anullsrc",
        "-i", "a.mp3",
        "-stream_loop", "-1", "-i", "bed.mp3",
    ]  # fmt: skip
