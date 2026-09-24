"""Cutting a span of one built section into its own file, with its own sound and its own words."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Words, words_file
from decktalk.errors import Cancel, InputError, NotBuiltError
from decktalk.events import Log
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.results import ClipResult, Word
from decktalk.stages.clip import clip

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"
lead_seconds = 0.5

[[section]]
number = 2
clip = "media/b-roll.mp4"
"""

WORDS = (
    Word(word="Hello", start=0.0, end=0.4),
    Word(word="there", start=0.6, end=1.0),
    Word(word="again", start=1.2, end=1.6),
)
"""The words section one speaks, before its own lead of half a second is added to them."""

FPS = 25
"""The rate `[video] output_fps` is left at, which is what turns a second into whole frames here."""


pytestmark = pytest.mark.usefixtures("fake_ffmpeg")
"""Every case here drives a stage that reaches for ffmpeg, so the encoder is faked at its own seam."""


def a_run(root: Path) -> Run:
    machine = Machine(environ={}, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    return Run(machine, id="r1", cancel=Cancel(), root=root)


def a_project(tmp_path: Path, *, voiced: bool = True, cut: bool = True, take_on_disk: bool = True) -> Inputs:
    """A project whose section one is narrated and cut, which is what a clip is taken out of."""
    (tmp_path / "deck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deck" / "index.html").write_text("<div data-scene='1'></div>", encoding="utf-8")
    (tmp_path / "media").mkdir(parents=True, exist_ok=True)
    (tmp_path / "media" / "b-roll.mp4").write_bytes(b"")
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    inputs = Inputs.load(tmp_path, environ={})
    take = Take(
        section=1,
        key="01",
        chapter="One",
        hash="h",
        voiced=voiced,
        word_count=3,
        characters=18,
        estimated_seconds=2.0,
        duration_seconds=2.0,
        speech_end_seconds=1.6,
        lead_seconds=0.5,
        spoken="Hello there again",
    )
    Takes(script="script.md", model="m", output_format="mp3_44100_128", sections=(take,)).write(
        inputs.workspace.takes_path
    )
    Words(words=WORDS).write(inputs.workspace.takes_dir / words_file("h"))
    if take_on_disk:
        (inputs.workspace.takes_dir / take.file).write_bytes(b"")
    if cut:
        inputs.workspace.section_video("01").parent.mkdir(parents=True, exist_ok=True)
        inputs.workspace.section_video("01").write_bytes(b"")
    return inputs


def cut_a_clip(inputs: Inputs, run: Run, **options: object) -> ClipResult:
    """One clip of section one, over the whole second the fake encoder says the section runs for."""
    settings: dict[str, object] = {"section": 1, "start": 0.0, "end": 0.8, "out": Path("media/answer.mp4")}
    settings.update(options)
    return clip(inputs, run, **settings)  # type: ignore[arg-type]  (the test names the same keywords)


def notes(run: Run) -> list[str]:
    said: list[str] = []
    run.machine.events.subscribe(lambda event: said.append(event.message) if isinstance(event, Log) else None)
    return said


# ---- what it writes ------------------------------------------------------------------------------


def test_a_clip_reports_its_span_its_hold_and_the_two_files_it_wrote(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    result = cut_a_clip(inputs, a_run(tmp_path), hold_seconds=0.2)
    assert result.section == 1
    assert result.film == Path("media/answer.mp4")
    assert result.words == Path("media/answer.words.json")
    assert (result.start, result.end) == (0.0, 0.8)
    assert result.hold_seconds == 0.2
    assert result.seconds == 1.0
    assert set(result.written) == {Path("media/answer.mp4"), Path("media/answer.words.json")}


def test_the_span_is_rounded_to_whole_frames(tmp_path: Path) -> None:
    """A clip that began mid-frame would play its first frame twice, so the span names frames."""
    result = cut_a_clip(a_project(tmp_path), a_run(tmp_path), start=0.01, end=0.79)
    assert (result.start, result.end) == (0.0, 0.8)


def test_the_words_file_holds_every_word_wholly_inside_the_span(tmp_path: Path) -> None:
    """The clip's own clock starts at its first frame, so its words are moved back to it."""
    inputs = a_project(tmp_path)
    cut_a_clip(inputs, a_run(tmp_path), start=0.4, end=1.0)
    written = Words.read(tmp_path / "media" / "answer.words.json")
    assert written is not None
    assert [word.word for word in written.words] == ["Hello"]
    assert written.words[0].start == 0.1


def test_a_word_the_span_cuts_in_two_is_said_rather_than_written(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    """No code in the frozen list names a cut word, so the run says it and the file leaves it out.

    The span holds the first word whole and cuts the second, which is the case a caller has to be
    told about: a words file that quietly dropped a word would caption the clip with a gap in it.
    """
    inputs = a_project(tmp_path)
    fake_ffmpeg.duration_seconds = 2.5
    run = a_run(tmp_path)
    said = notes(run)
    result = cut_a_clip(inputs, run, start=0.4, end=1.2)
    written = Words.read(tmp_path / "media" / "answer.words.json")
    assert written is not None
    assert [word.word for word in written.words] == ["Hello"]
    assert any("cuts the word 'there' in two" in line for line in said)
    assert result.findings == ()


def test_a_clip_of_a_placeholder_take_says_its_word_times_are_estimates(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, voiced=False)
    run = a_run(tmp_path)
    said = notes(run)
    result = cut_a_clip(inputs, run)
    assert result.estimated is True
    assert any("estimate" in line for line in said)


def test_a_clip_of_a_voiced_take_is_not_estimated(tmp_path: Path) -> None:
    assert cut_a_clip(a_project(tmp_path), a_run(tmp_path)).estimated is False


# ---- what it asks ffmpeg for ----------------------------------------------------------------------


def test_the_picture_is_the_frames_of_the_span_and_the_hold_clones_the_last(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    cut_a_clip(a_project(tmp_path), a_run(tmp_path), start=0.0, end=0.8, hold_seconds=0.2)
    graph = next(call[call.index("-filter_complex") + 1] for call in fake_ffmpeg.calls)
    assert f"trim=start_frame=0:end_frame={round(0.8 * FPS)}" in graph
    assert f"tpad=stop_mode=clone:stop={round(0.2 * FPS)}" in graph


def test_the_sound_is_the_take_moved_to_where_the_span_starts(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    """A span that opens inside the section's lead opens on the silence the film has there."""
    cut_a_clip(a_project(tmp_path), a_run(tmp_path), start=0.0, end=0.8)
    graph = next(call[call.index("-filter_complex") + 1] for call in fake_ffmpeg.calls)
    assert "adelay=delays=" in graph
    assert "atrim=start=0.000000" in graph


def test_the_gain_reaches_the_filter_graph(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    cut_a_clip(a_project(tmp_path), a_run(tmp_path), gain_db=-3.0)
    graph = next(call[call.index("-filter_complex") + 1] for call in fake_ffmpeg.calls)
    assert "volume=-3dB" in graph


# ---- what it refuses -----------------------------------------------------------------------------


def test_a_section_the_project_does_not_have_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="section 9 is not in"):
        cut_a_clip(a_project(tmp_path), a_run(tmp_path), section=9)


def test_a_clip_section_is_refused_because_there_is_nothing_to_cut_out_of_it(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="plays a clip the project already has"):
        cut_a_clip(a_project(tmp_path), a_run(tmp_path), section=2)


def test_a_span_that_ends_before_it_starts_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="ends before it starts"):
        cut_a_clip(a_project(tmp_path), a_run(tmp_path), start=0.8, end=0.4)


def test_a_span_past_the_end_of_the_section_is_refused(tmp_path: Path, fake_ffmpeg) -> None:  # noqa: ANN001
    fake_ffmpeg.duration_seconds = 1.0
    with pytest.raises(InputError, match="runs for 1s"):
        cut_a_clip(a_project(tmp_path), a_run(tmp_path), end=2.0)


def test_a_span_holding_no_whole_frame_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="holds no whole frame"):
        cut_a_clip(a_project(tmp_path), a_run(tmp_path), start=0.401, end=0.409)


def test_a_hold_of_less_than_no_time_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="less than no time"):
        cut_a_clip(a_project(tmp_path), a_run(tmp_path), hold_seconds=-1.0)


def test_an_out_that_names_the_section_cut_is_refused(tmp_path: Path) -> None:
    """A clip written over the cut it reads would leave the film with no section at all."""
    inputs = a_project(tmp_path)
    with pytest.raises(InputError, match="which is the section cut"):
        cut_a_clip(inputs, a_run(tmp_path), out=Path("build/sections/01.mp4"))


def test_a_section_with_no_cut_names_the_command_that_makes_one(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="has no cut at") as refused:
        cut_a_clip(a_project(tmp_path, cut=False), a_run(tmp_path))
    assert refused.value.hint == "Run `decktalk assemble` first."


def test_a_project_with_no_take_index_names_the_command_that_writes_one(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    inputs.workspace.takes_path.unlink()
    with pytest.raises(NotBuiltError):
        cut_a_clip(inputs, a_run(tmp_path))


def test_a_take_the_index_names_and_the_project_has_not_got_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="which is not on disk"):
        cut_a_clip(a_project(tmp_path, take_on_disk=False), a_run(tmp_path))
