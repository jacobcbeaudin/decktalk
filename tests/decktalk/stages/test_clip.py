"""Cutting a span of one built section into its own file, with its own sound and its own words."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from decktalk.artifacts import Words, words_file
from decktalk.errors import InputError, NotBuiltError
from decktalk.inputs import Inputs
from decktalk.machine import Run
from decktalk.results import ClipResult, Word
from decktalk.stages.clip import clip
from support.pages import SCENE_ONE
from support.projects import load_project
from support.runs import a_run, notes
from support.takes import a_take, write_takes

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


def a_project(tmp_path: Path, *, voiced: bool = True, cut: bool = True, take_on_disk: bool = True) -> Inputs:
    """A project whose section one is narrated and cut, which is what a clip is taken out of."""
    inputs = load_project(tmp_path, TOML, page=SCENE_ONE, media=("media/b-roll.mp4",))
    take = a_take(1, seconds=2.0, hash="0123456789abcdef", voiced=voiced, lead_seconds=0.5)
    write_takes(inputs, take)
    Words(words=WORDS).write(inputs.workspace.takes_dir / words_file("0123456789abcdef"))
    if take_on_disk:
        (inputs.workspace.takes_dir / take.file).write_bytes(b"")
    if cut:
        inputs.workspace.section_video("01").parent.mkdir(parents=True, exist_ok=True)
        inputs.workspace.section_video("01").write_bytes(b"")
    return inputs


def cut_a_clip(inputs: Inputs, run: Run, **options: Any) -> ClipResult:
    """One clip of section one, over the whole second the fake encoder says the section runs for."""
    settings: dict[str, Any] = {"section": 1, "start": 0.0, "end": 0.8, "out": Path("media/answer.mp4")}
    settings.update(options)
    return clip(inputs, run, **settings)


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


def test_a_word_the_span_cuts_in_two_is_said_rather_than_written(tmp_path: Path, fake_ffmpeg) -> None:
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


def graph_of(fake_ffmpeg) -> str:
    """The filter graph of the one call that cut the clip."""
    return next(call[call.index("-filter_complex") + 1] for call in fake_ffmpeg.calls)


@pytest.mark.parametrize(
    ("options", "fragments"),
    [
        pytest.param(
            {"hold_seconds": 0.2},
            (f"trim=start_frame=0:end_frame={round(0.8 * FPS)}", f"tpad=stop_mode=clone:stop={round(0.2 * FPS)}"),
            id="the picture is the span's frames and the hold clones the last",
        ),
        # A span that opens inside the section's lead opens on the silence the film has there.
        pytest.param({}, ("adelay=delays=", "atrim=start=0.000000"), id="the sound is the take moved to the span"),
        pytest.param({"gain_db": -3.0}, ("volume=-3dB",), id="the gain reaches the graph"),
    ],
)
def test_the_filter_graph_carries_what_the_clip_asked_for(
    tmp_path: Path,
    fake_ffmpeg,
    options: dict[str, object],
    fragments: tuple[str, ...],
) -> None:
    cut_a_clip(a_project(tmp_path), a_run(tmp_path), **options)
    graph = graph_of(fake_ffmpeg)
    for fragment in fragments:
        assert fragment in graph


# ---- what it refuses -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "match"),
    [
        pytest.param({"section": 9}, "section 9 is not in", id="a section the project does not have"),
        # A clip section has nothing to cut out of it.
        pytest.param({"section": 2}, "plays a clip the project already has", id="a clip section"),
        pytest.param({"start": 0.8, "end": 0.4}, "ends before it starts", id="a span that ends before it starts"),
        # The fake encoder says the section runs for one second.
        pytest.param({"end": 2.0}, "runs for 1s", id="a span past the end of the section"),
        pytest.param({"start": 0.401, "end": 0.409}, "holds no whole frame", id="a span holding no whole frame"),
        pytest.param({"hold_seconds": -1.0}, "less than no time", id="a hold of less than no time"),
        # A clip written over the cut it reads would leave the film with no section at all.
        pytest.param({"out": Path("build/sections/01.mp4")}, "which is the section cut", id="an out over the cut"),
    ],
)
def test_a_clip_the_project_cannot_cut_is_refused(tmp_path: Path, options: dict[str, object], match: str) -> None:
    with pytest.raises(InputError, match=match):
        cut_a_clip(a_project(tmp_path), a_run(tmp_path), **options)


def test_a_section_with_no_cut_names_the_command_that_makes_one(tmp_path: Path) -> None:
    with pytest.raises(NotBuiltError, match="has no cut at") as refused:
        cut_a_clip(a_project(tmp_path, cut=False), a_run(tmp_path))
    assert refused.value.hint == "Run `decktalk assemble` first."


def test_a_project_with_no_take_index_names_the_command_that_writes_one(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    inputs.workspace.takes_path.unlink()
    with pytest.raises(NotBuiltError):
        cut_a_clip(inputs, a_run(tmp_path))


def test_a_take_the_index_names_and_the_project_has_not_got_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NotBuiltError, match="which is not on disk"):
        cut_a_clip(a_project(tmp_path, take_on_disk=False), a_run(tmp_path))
