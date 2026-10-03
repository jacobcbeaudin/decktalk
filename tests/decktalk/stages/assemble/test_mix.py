"""The whole soundtrack as one graph: the anchor, the narration runs, the clips, the beds and the effects.

The volume shapes are also rendered by the pinned ffmpeg, under `media`, because ffmpeg is the one
judge of the expressions it accepts and of the samples they make.
"""

from __future__ import annotations

import functools
import hashlib
import math
import shutil
import struct
from pathlib import Path

import pytest

from decktalk.events import FindingEvent
from decktalk.findings import Code
from decktalk.inputs.markers import Marker
from decktalk.media import ffmpeg
from decktalk.stages.assemble.mix import (
    LAVFI,
    LOOP,
    ONCE,
    MixInput,
    MixPlan,
    delay,
    max_expr,
    mix_input_args,
    mix_soundtrack,
    plan_mix,
    ramp_expr,
    ramp_groups,
    ramps_expr,
    resolve_marker_time,
    speech_spans,
)
from support.media_cards import write_tone_with_tail

from .conftest import MID_CLIP_TOML, cue_times, open_run, rendered, spoken, take_index, write_project

pytestmark = pytest.mark.usefixtures("fake_ffmpeg")

THREE_PAGES = {1: 2.0, 2: 2.5, 3: 1.5}
"""Three page sections of the lengths the take index below gives them."""


def three_page_plan(inputs, opened, *, score: bool = True) -> MixPlan:
    takes = take_index(
        inputs,
        {
            1: ("A", 2.0, 1.6, spoken("alpha beta")),
            2: ("B", 2.5, 2.1, spoken("gamma delta")),
            3: ("C", 1.5, 1.3, spoken("epsilon")),
        },
    )
    return plan_mix(inputs, opened.run, rendered(inputs, THREE_PAGES), takes, score=score)


def test_a_second_becomes_the_milliseconds_adelay_reads():
    assert delay(0.0) == "adelay=0:all=1"
    assert delay(2.5) == "adelay=2500:all=1"
    assert delay(0.0006) == "adelay=1:all=1"


def test_a_ramp_is_zero_outside_its_span_and_one_inside_it():
    assert ramp_expr(1.0, 2.0, 0.2) == "min(1,max(0,(t-1.000)/0.2))*min(1,max(0,(2.000-t)/0.2))"


def test_a_ramp_of_no_length_is_a_step_and_never_divides_by_zero():
    """0/0 is NaN in ffmpeg, and a NaN volume silences the frame that starts on the edge."""
    assert ramp_expr(1.0, 2.0, 0.0) == "gt(t,1.000)*lt(t,2.000)"


def test_the_largest_of_no_ramps_is_nothing_at_all():
    assert max_expr([]) == "0"
    assert max_expr(["a"]) == "a"
    assert max_expr(["a", "b", "c"]) == "max(a,max(b,c))"


def nesting(expr: str, call: str) -> int:
    """How many calls of `call` are open at once at the deepest point of an expression."""
    deepest, frames, at = 0, [], 0
    while at < len(expr):
        if expr.startswith(f"{call}(", at):
            frames.append(True)
            at += len(call) + 1
        elif expr[at] == "(":
            frames.append(False)
            at += 1
        else:
            if expr[at] == ")":
                frames.pop()
            at += 1
        deepest = max(deepest, sum(frames))
    return deepest


def apart(count: int) -> list[tuple[float, float]]:
    """Spans of one and a half seconds with half a second between them, which is how speech sits."""
    return [(2.0 * n, 2.0 * n + 1.5) for n in range(count)]


@pytest.mark.parametrize("count", [1, 2, 3, 30, 93, 94, 100, 1000])
def test_spans_that_never_overlap_are_searched_at_a_depth_of_log2_of_their_count(count):
    """ffmpeg refuses an expression nested a hundred deep, so the depth grows with the log of the spans."""
    expr = ramps_expr(apart(count), 0.5)
    assert nesting(expr, "if") == math.ceil(math.log2(count))
    assert nesting(expr, "max") == 1  # the max(0, ...) inside each ramp, and no fold around them
    assert expr.count("min(1,max(0,(t-") == count


def test_no_spans_at_all_are_nothing_at_all():
    assert ramps_expr([], 0.5) == "0"


def test_a_split_falls_where_the_later_group_starts():
    """t before the split can only be inside the earlier span, and t from it on only inside the later."""
    first, second = ramp_expr(0.0, 1.0, 0.5), ramp_expr(2.0, 3.0, 0.5)
    assert ramps_expr([(2.0, 3.0), (0.0, 1.0)], 0.5) == f"if(lt(t,2.000),{first},{second})"


def test_spans_that_overlap_merge_into_one_group_and_the_rest_stay_apart():
    """A ramp is zero at its own edges, so spans that only touch stay apart."""
    spans = [(5.0, 6.0), (0.0, 2.0), (1.0, 3.0), (3.0, 4.0), (2.5, 2.8), (8.0, 9.0)]
    assert ramp_groups(spans) == [[(0.0, 2.0), (1.0, 3.0), (2.5, 2.8)], [(3.0, 4.0)], [(5.0, 6.0)], [(8.0, 9.0)]]


def test_spans_are_compared_as_ffmpeg_reads_them_to_the_millisecond():
    """An overlap smaller than the millisecond the expression is written in is no overlap at all."""
    assert ramp_groups([(0.0, 1.0004), (1.0001, 2.0)]) == [[(0.0, 1.0004)], [(1.0001, 2.0)]]
    assert ramp_groups([(0.0, 1.0006), (1.0001, 2.0)]) == [[(0.0, 1.0006), (1.0001, 2.0)]]


def test_a_merged_group_is_one_leaf_holding_a_balanced_max_of_its_ramps():
    """Padded ambience sections all overlap, so one group holds every one and must still nest shallowly."""
    padded = [(2.0 * n - 0.5, 2.0 * n + 2.5) for n in range(200)]
    expr = ramps_expr(padded, 1.0)
    assert nesting(expr, "if") == 0
    assert nesting(expr, "max") == math.ceil(math.log2(200)) + 1  # and the max(0, ...) inside each ramp


def test_the_music_and_the_ambience_are_shaped_by_the_search_tree(tmp_path):
    """The duck, the marker swells and mutes, and the ambience bed are each one tree, never one long fold."""
    pages = "".join(
        f"[[section]]\nnumber = {n}\npage = 'deck/index.html'\nscene = '{n}'\nambience = true\n" for n in range(1, 121)
    )
    toml = (
        "[project]\nname = 't'\n[narration]\nlead_seconds = 0\n"
        '[mix]\nmusic = "media/bed.mp3"\nambience = "media/room.mp3"\n' + pages
    )
    inputs = write_project(tmp_path, toml)
    (tmp_path / "media").mkdir()
    for name in ("bed.mp3", "room.mp3"):
        (tmp_path / "media" / name).write_bytes(b"")
    opened = open_run(tmp_path)
    takes = take_index(inputs, {n: (f"S{n}", 2.0, 1.5, spoken("a b")) for n in range(1, 121)})
    plan = plan_mix(inputs, opened.run, rendered(inputs, dict.fromkeys(range(1, 121), 2.0)), takes, score=True)
    music = next(part for part in plan.filter.split(";") if part.endswith("[music]"))
    ambience = next(part for part in plan.filter.split(";") if part.endswith("[amb]"))
    assert nesting(music, "if") == math.ceil(math.log2(120))
    assert nesting(ambience, "max") == math.ceil(math.log2(120)) + 1


def test_the_graph_reaches_ffmpeg_as_a_file_and_never_as_one_argument(tmp_path, monkeypatch):
    """A long film's graph runs past the 32,767 characters a Windows command line holds."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    inputs.workspace.final_dir.mkdir(parents=True, exist_ok=True)
    seen: list[tuple[list[str], str]] = []

    def run(*args: str) -> None:
        if "-/filter_complex" in args:
            seen.append((list(args), Path(args[args.index("-/filter_complex") + 1]).read_text(encoding="utf-8")))

    monkeypatch.setattr(ffmpeg, "run", run)
    work = inputs.workspace.final_dir / ".film.mix.mov"
    plan = mix_soundtrack(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, work, score=True)
    [(args, graph)] = seen
    assert "-filter_complex" not in args
    assert plan.filter not in args
    assert graph == plan.filter
    assert not Path(args[args.index("-/filter_complex") + 1]).exists()


def test_a_silent_anchor_of_the_pictures_length_fixes_the_mix(tmp_path):  # fmt: skip
    """The picture carries no audio, so without the anchor the mix is as long as its longest layer."""
    inputs = write_project(tmp_path)
    plan = three_page_plan(inputs, open_run(tmp_path))
    assert plan.inputs[0].mode == LAVFI
    assert plan.inputs[0].path == "anullsrc=r=48000:cl=stereo"
    assert plan.total == 6.0
    assert plan.filter.startswith("[1:a]aresample=48000,aformat=channel_layouts=stereo[anchor]")
    assert plan.filter.endswith("amix=inputs=2:duration=first:normalize=0[a]")


def test_one_unbroken_run_of_pages_plays_the_whole_track_once(tmp_path):  # fmt: skip
    inputs = write_project(tmp_path)
    plan = three_page_plan(inputs, open_run(tmp_path))
    narration = [layer for layer in plan.inputs if layer.path.endswith("narration.mp3")]
    assert len(narration) == 1
    assert "[narr]" in plan.filter
    assert "atrim=start=" not in plan.filter


def test_a_clip_between_two_pages_splits_the_narration_into_its_own_runs(tmp_path):
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
    plan = plan_mix(inputs, opened.run, rows, takes, score=True)
    assert "[narr0]" in plan.filter
    assert "[narr1]" in plan.filter
    assert "atrim=start=0.000:end=2.000" in plan.filter
    # The second run opens where its own first section opens in the film, which is after the clip.
    assert "adelay=5000:all=1" in plan.filter


def test_a_clips_own_audio_lands_at_its_section_start_and_fades_at_both_ends(tmp_path):
    inputs = write_project(tmp_path, MID_CLIP_TOML)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    rows = rendered(inputs, {1: 2.0, 2: 3.0}, audio={2: tmp_path / "media" / "broll.mp4"})
    plan = plan_mix(inputs, opened.run, rows, takes, score=True)
    assert "[clip02]" in plan.filter
    assert "afade=t=in:d=0.02" in plan.filter
    assert "afade=t=out:st=2.980:d=0.02" in plan.filter
    assert "atrim=duration=3.000" in plan.filter


def test_a_music_bed_the_project_names_and_has_not_got_is_a_certain_finding(tmp_path):
    """A film mixed without the music it declares is not the film the project asked for."""
    toml = MID_CLIP_TOML + '\n[mix]\nmusic = "media/bed.mp3"\n'
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, score=True)
    assert opened.codes() == ["FILE_MISSING"]
    said = next(line.finding for line in opened.of(FindingEvent))
    assert "media/bed.mp3" in said.message
    assert said.location.where == "media/bed.mp3"
    assert "[music]" not in plan.filter


def test_a_music_bed_the_score_has_not_bought_yet_plays_silence_under_a_warning(tmp_path):
    """The bed is still the score's to buy, so a run without spend mixes silence and is not refused."""
    toml = MID_CLIP_TOML + '\n[mix]\nmusic = "media/bed.mp3"\n\n[score.music]\nprompt = "warm strings"\n'
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, score=True)
    assert opened.codes() == [Code.SOUND_MISSING.value]
    said = next(line.finding for line in opened.of(FindingEvent))
    assert said.location.where == "media/bed.mp3"
    assert "--spend" in said.message
    assert "[music]" not in plan.filter


def test_a_run_that_asks_for_no_score_lays_no_bed_and_judges_nothing(tmp_path):
    toml = MID_CLIP_TOML + '\n[mix]\nmusic = "media/bed.mp3"\n'
    inputs = write_project(tmp_path, toml)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {1: ("A", 2.0, 1.6, spoken("alpha beta"))})
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, score=False)
    assert opened.codes() == []
    assert "[music]" not in plan.filter


def test_an_effect_lands_at_the_second_its_own_cue_resolved_to(tmp_path):
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
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0, 2: 2.0}), takes, score=True)
    assert "[effect0]" in plan.filter
    # Section 2 opens at 2.0 s and the cue sits half a second into it.
    assert "adelay=2500:all=1" in plan.filter


def test_an_effect_whose_cue_is_unresolved_is_said_and_never_played(tmp_path):
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
    plan = plan_mix(inputs, opened.run, rendered(inputs, {1: 2.0}), takes, score=True)
    assert "[effect0]" not in plan.filter
    assert any("is unresolved" in note for note in opened.notes())


def test_speech_spans_cover_every_spoken_section_and_every_clip(tmp_path):
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


def test_a_marker_resolves_against_the_words_of_its_own_section(tmp_path):
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
        *ffmpeg.source("a.mp3"),
        "-stream_loop", "-1", *ffmpeg.source("bed.mp3"),
    ]  # fmt: skip


def test_a_sound_the_project_names_opens_as_one_file_and_follows_no_name_inside_it():
    """A music bed that is a playlist would otherwise read files and hosts the project never named."""
    for layer in (MixInput(ONCE, "music.m3u8"), MixInput(LOOP, "music.m3u8")):
        opened = ffmpeg.source("music.m3u8")
        assert layer.args(1.0)[-len(opened) :] == opened


@pytest.fixture
def real_ffmpeg(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned ffmpeg this machine fetched, in place of the fake the rest of this module runs.

    The module fakes ffmpeg through this same monkeypatch, so undoing it once the fake is in place
    restores the real tool. Its tests are marked `media`, so the machine's toolchain is already bound
    as a run binds it, because what these tests measure is what ffmpeg itself accepts and renders.
    """
    request.getfixturevalue("fake_ffmpeg")
    monkeypatch.undo()


RATE = 48000


def folded(spans: list[tuple[float, float]], ramp: float) -> str:
    """Every ramp folded into one `max(max(max(r1,r2),r3)...)`, which is the reference the tree must match.

    Each ramp is written whole here, with no step for a ramp of no length, so the reference is the
    fold alone and owes nothing to the builder it judges.
    """
    terms = [f"min(1,max(0,(t-{start:.3f})/{ramp}))*min(1,max(0,({end:.3f}-t)/{ramp}))" for start, end in spans]
    return functools.reduce(lambda joined, term: f"max({joined},{term})", terms) if terms else "0"


def heard(expr: str, seconds: float, tmp_path: Path) -> str:
    """The digest of a tone shaped by `expr` exactly as the music is, as 32-bit float samples."""
    graph = tmp_path / f"{hashlib.sha256(expr.encode()).hexdigest()[:12]}.graph"
    graph.write_text(
        f"[0:a]aresample={RATE},aformat=channel_layouts=stereo,atrim=duration={seconds:.3f},"
        f"volume='0.06310*(1-0.49881*{expr})':eval=frame[a]",
        encoding="utf-8",
    )
    samples = ffmpeg.raw(
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate={RATE}",
        "-/filter_complex", str(graph), "-map", "[a]", "-c:a", "pcm_f32le", "-f", "f32le", "-",
    )  # fmt: skip
    assert len(samples) > 0
    return hashlib.sha256(samples).hexdigest()


SPEECH = [(16.3 * n + 0.2, 16.3 * n + 15.0 - (n % 4) * 0.7) for n in range(30)]
"""Thirty spoken spans of a lesson, apart from one another, as the music ducks under them."""

AMBIENCE = [(16.3 * n - 0.5, 16.3 * n + 16.3 + 0.5) for n in range(30) if n % 7 != 3]
"""Padded ambience sections, which overlap their neighbours and break where a section asks for none."""

TOUCHING = [(8.0 * n, 8.0 * n + 8.0) for n in range(29)] + [(232.0, 239.95)]
"""Spans that end where the next begins, on the edges of 1,024-sample frames, where t lands on an edge exactly."""

MARKERS = [(3.0 * n + (n % 3) * 0.02, 3.0 * n + 2.5 + (n % 5) * 0.4) for n in range(30)]
"""Marker swells, some apart and some running into the next, with the short ramp a swell has."""


@pytest.mark.parametrize(
    ("spans", "ramp"),
    [(SPEECH, 0.5), (AMBIENCE, 1.0), (MARKERS, 0.3), (MARKERS, 0.04), (TOUCHING, 0.5), (SPEECH, 0.0)],
    ids=["speech", "ambience", "markers", "mutes", "touching", "no-ramp"],
)
@pytest.mark.media
@pytest.mark.usefixtures("real_ffmpeg")
def test_the_search_tree_sounds_exactly_as_the_fold_of_every_ramp(tmp_path, spans, ramp):
    seconds = max(end for _, end in spans) + 2.0
    assert heard(ramps_expr(spans, ramp), seconds, tmp_path) == heard(folded(spans, ramp), seconds, tmp_path)


FRAME = 1000
"""Samples in one frame of the level probe, so that every half second starts a frame and t lands on it exactly."""


def levels(expr: str, seconds: float, tmp_path: Path) -> list[float]:
    """The volume `expr` gives the music at every sample, read off a constant input of one."""
    graph = tmp_path / "levels.graph"
    graph.write_text(f"[0:a]volume='0.06310*(1-0.49881*{expr})':eval=frame[a]", encoding="utf-8")
    samples = ffmpeg.raw(
        "-f", "lavfi", "-i", f"aevalsrc=exprs=1:s={RATE}:n={FRAME}:d={seconds}",
        "-/filter_complex", str(graph), "-map", "[a]", "-c:a", "pcm_f32le", "-f", "f32le", "-",
    )  # fmt: skip
    return list(struct.unpack(f"<{len(samples) // 4}f", samples))


@pytest.mark.media
@pytest.mark.usefixtures("real_ffmpeg")
def test_a_ramp_of_no_length_never_silences_the_frame_that_starts_on_an_edge(tmp_path):
    """Dividing by a ramp of no length is 0/0 on that frame, and ffmpeg plays a NaN volume as silence."""
    spans = [(0.5 * n, 0.5 * n + 0.5) for n in range(10)]
    assert min(levels(ramps_expr(spans, 0.0), 6.0, tmp_path)) > 0
    assert min(levels(folded(spans, 0.0), 6.0, tmp_path)) == 0


SECTIONS = 120
SECTION_SECONDS = 0.4


@pytest.mark.media
@pytest.mark.usefixtures("real_ffmpeg")
def test_a_film_with_music_and_more_spoken_spans_than_ffmpeg_nests_assembles(tmp_path):
    """ffmpeg refuses an expression nested a hundred deep, which a fold of 94 ramps already is."""
    pages = "".join(
        f"[[section]]\nnumber = {n}\npage = 'deck/index.html'\nscene = '{n}'\nambience = true\n"
        for n in range(1, SECTIONS + 1)
    )
    toml = (
        "[project]\nname = 't'\n[narration]\nlead_seconds = 0\n"
        '[mix]\nmusic = "media/bed.mp3"\nambience = "media/room.mp3"\n' + pages
    )
    inputs = write_project(tmp_path, toml)
    (tmp_path / "media").mkdir()
    write_tone_with_tail(tmp_path / "media" / "bed.mp3", tail=0.5)
    write_tone_with_tail(tmp_path / "media" / "room.mp3", tail=0.5)
    inputs.workspace.narration_path.parent.mkdir(parents=True, exist_ok=True)
    write_tone_with_tail(inputs.workspace.narration_path, tail=0.5)
    opened = open_run(tmp_path)
    takes = take_index(inputs, {n: (f"S{n}", SECTION_SECONDS, 0.3, spoken("a")) for n in range(1, SECTIONS + 1)})
    rows = rendered(inputs, dict.fromkeys(range(1, SECTIONS + 1), SECTION_SECONDS))
    card = tmp_path / "card.mp4"
    ffmpeg.run(
        "-f", "lavfi", "-i", f"color=c=white:s=64x36:r=25:d={SECTION_SECONDS}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(card),
    )  # fmt: skip
    for row in rows:
        row.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(card, row.path)
    inputs.workspace.final_dir.mkdir(parents=True, exist_ok=True)
    work = inputs.workspace.final_dir / ".t.mix.mov"
    plan = mix_soundtrack(inputs, opened.run, rows, takes, work, score=True)
    assert plan.filter.count("min(1,max(0,(t-") == 2 * SECTIONS
    assert ffmpeg.probe_duration(work) == pytest.approx(SECTIONS * SECTION_SECONDS, abs=0.1)
