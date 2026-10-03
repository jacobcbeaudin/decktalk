"""The whole stage: cut, mix, normalize and publish, with the toolchain faked at its own seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import InputError, NotBuiltError, ToolError
from decktalk.events import StageProgress
from decktalk.media import audio
from decktalk.results import AssembleResult, Substitute, Word
from decktalk.stages.assemble import assemble, slate

from .conftest import TITLED_TOML, draw_slate, open_run, spoken, take_index, write_project

pytestmark = pytest.mark.usefixtures("rendering")


@pytest.fixture(autouse=True)
def no_poster(monkeypatch: pytest.MonkeyPatch) -> None:
    """The poster is one picture beside a finished film, and no test here is about drawing it."""
    monkeypatch.setattr("decktalk.stages.assemble.render_poster", lambda *_args: None)


def a_film(inputs, *, voiced: bool = True):
    """A project whose three page sections all have takes and recordings, which is a whole film."""
    inputs.workspace.recordings_dir.mkdir(parents=True, exist_ok=True)
    for number in (1, 2, 3):
        inputs.workspace.recording(f"{number:02d}").write_bytes(b"a recording")
    return take_index(
        inputs,
        {
            1: ("One", 2.0, 1.6, spoken("alpha beta")),
            2: ("Two", 2.5, 2.1, spoken("gamma delta")),
            3: ("Three", 1.5, 1.3, spoken("epsilon")),
        },
        voiced=voiced,
    )


def mastered(monkeypatch: pytest.MonkeyPatch, inputs, off: float = 0.0) -> None:
    """Every loudness reading lands `off` from the target the project masters to."""
    target = inputs.settings.audio.target_lufs
    reading = audio.Loudness(i=target + off, tp=-2.0, lra=6.0, thresh=-30.0, offset=0.0)
    monkeypatch.setattr(audio, "measure_loudness", lambda *_a, **_k: reading)


GAPPED: dict[int, tuple[str, float, float | None, list[Word]]] = {
    1: ("Open", 2.0, 1.6, spoken("alpha")),
    3: ("The edit", 2.0, 1.6, spoken("beta")),
    4: ("Close", 1.0, 0.8, spoken("gamma")),
}
"""Takes for the titled project's page sections, whose clip section two has no file on disk."""


def test_a_film_with_no_take_index_names_the_stage_that_writes_one(tmp_path):
    inputs = write_project(tmp_path)
    with pytest.raises(NotBuiltError) as refused:
        assemble(inputs, open_run(tmp_path).run)
    assert "decktalk narrate" in (refused.value.hint or "")


def test_the_stage_answers_with_the_result_named_after_it(tmp_path, monkeypatch):  # fmt: skip
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    a_film(inputs)
    mastered(monkeypatch, inputs)
    result = assemble(inputs, opened.run)
    assert isinstance(result, AssembleResult)
    assert result.run == "r1"
    assert result.film == Path("build/final/t.mp4")
    assert [row.section for row in result.sections] == [1, 2, 3]
    assert result.loudness is not None
    assert result.loudness.target_lufs == inputs.settings.audio.target_lufs
    assert result.elapsed_seconds >= 0


def test_every_file_the_run_wrote_is_in_the_result_and_written_once(tmp_path, monkeypatch):  # fmt: skip
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    a_film(inputs)
    mastered(monkeypatch, inputs)
    result = assemble(inputs, opened.run)
    written = [path.as_posix() for path in result.written]
    assert len(written) == len(set(written))
    for name in ("build/final/t.srt", "build/final/t.vtt", "build/final/placements.json", "build/final/t.mp4"):
        assert name in written
    assert "build/sections/01.mp4" in written


def test_a_run_says_how_far_through_its_own_passes_it_is(tmp_path, monkeypatch):  # fmt: skip
    """A renderer never works out a fraction, so every pass reports the same shape."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    a_film(inputs)
    mastered(monkeypatch, inputs)
    assemble(inputs, opened.run)
    labels = opened.progress()
    assert labels[:3] == ["cut section 1", "cut section 2", "cut section 3"]
    assert labels[-1] == "publish the film"
    lines = opened.of(StageProgress)
    assert {line.total for line in lines} == {len(labels)}
    assert [line.done for line in lines] == list(range(1, len(labels) + 1))


def test_a_placeholder_narration_is_never_normalized(tmp_path, monkeypatch):  # fmt: skip
    """Normalizing clicks would move the very clicks the a/v check listens for."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    a_film(inputs, voiced=False)
    called: list[str] = []
    monkeypatch.setattr(audio, "measure_loudness", lambda *_a, **_k: called.append("measured"))
    result = assemble(inputs, opened.run)
    assert called == []
    assert result.loudness is None
    assert any("the narration is a placeholder" in note for note in opened.notes())


def test_a_film_that_stood_a_frame_in_for_a_missing_file_is_not_ok(tmp_path, monkeypatch):  # fmt: skip
    """`ok` is false when any judgement is an error, and a missing file is an error."""
    inputs = write_project(tmp_path, TITLED_TOML)
    opened = open_run(tmp_path)
    monkeypatch.setattr(slate, "render_slate", draw_slate)
    take_index(inputs, GAPPED, voiced=False)
    result = assemble(inputs, opened.run)
    assert not result.ok
    assert {row.code.name for row in result.findings} == {"FILE_MISSING"}
    assert Substitute.SLATE in {row.substitute for row in result.sections}


def test_strict_refuses_a_placeholder_frame_where_the_file_is_missing(tmp_path, monkeypatch):  # fmt: skip
    """A strict run stops at the file it has not got, naming it, rather than publishing a stand-in."""
    inputs = write_project(tmp_path, TITLED_TOML)
    opened = open_run(tmp_path)
    monkeypatch.setattr(slate, "render_slate", draw_slate)
    inputs.workspace.recordings_dir.mkdir(parents=True)
    for number in (1, 3, 4):
        inputs.workspace.recording(f"{number:02d}").write_bytes(b"a recording")
    take_index(inputs, GAPPED, voiced=False)
    with pytest.raises(InputError) as refused:
        assemble(inputs, opened.run, strict=True)
    assert refused.value.location is not None
    assert refused.value.location.where == "media/before.mov"


def test_strict_refuses_a_mix_that_missed_the_loudness_it_was_mastered_to(tmp_path, monkeypatch):
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    a_film(inputs)
    mastered(monkeypatch, inputs, off=-5.0)
    with pytest.raises(ToolError) as refused:
        assemble(inputs, opened.run, strict=True)
    assert "loudness" in str(refused.value)


def test_a_run_that_asks_for_no_score_lays_no_bed(tmp_path, rendering, monkeypatch):  # fmt: skip
    inputs = write_project(
        tmp_path, TITLED_TOML.replace("[narration]", '[mix]\nmusic = "media/bed.mp3"\n\n[narration]')
    )
    opened = open_run(tmp_path)
    monkeypatch.setattr(slate, "render_slate", draw_slate)
    take_index(inputs, GAPPED, voiced=False)
    result = assemble(inputs, opened.run, score=False)
    assert "media/bed.mp3" not in {row.location.where for row in result.findings}
    assert not any("bed.mp3" in " ".join(call) for call in rendering.calls)


def test_the_work_files_never_survive_the_run(tmp_path):
    """A viewer opens the film and never a half-made one, so nothing beginning with a dot is left."""
    inputs = write_project(tmp_path)
    opened = open_run(tmp_path)
    a_film(inputs, voiced=False)
    assemble(inputs, opened.run)
    left = [path.name for path in inputs.workspace.final_dir.iterdir() if path.name.startswith(".")]
    assert left == []
