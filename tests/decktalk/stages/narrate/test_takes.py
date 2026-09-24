"""Writing one take, placing it, and joining every take into one narration track."""

from __future__ import annotations

import pytest

from decktalk.artifacts import Take, Takes, Words, take_file, words_file
from decktalk.inputs import Inputs
from decktalk.inputs.script import parse_script
from decktalk.media import audio
from decktalk.results import Word
from decktalk.speech import SpeechRequest
from decktalk.stages.narrate.plan import placeholder_plan, voiced_plan
from decktalk.stages.narrate.takes import (
    PLACEHOLDER_CLOSE_SECONDS,
    estimated_words,
    join_takes,
    place,
    planned_words,
    write_placeholder_take,
    write_voiced_take,
)

from .conftest import VOICE_ID


@pytest.fixture(autouse=True)
def quiet_sound_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Where a take's sound ends is read from real bytes, which no take written here has.

    The fake encoder writes an empty file, so the scan is answered at the seam the stage reads it
    through, and every placement test measures the arithmetic rather than ffmpeg.
    """
    monkeypatch.setattr(audio, "sound_end", lambda _path, **_levels: 0.8)


def a_take(section: int = 1, *, digest: str = "abc", seconds: float = 1.0) -> Take:
    return Take(
        section=section,
        key=f"{section:02d}",
        chapter="Open",
        hash=digest,
        voiced=True,
        word_count=2,
        characters=8,
        estimated_seconds=1.0,
        duration_seconds=seconds,
        spoken="A bowl.",
    )


def test_estimated_words_space_the_section_evenly_and_drop_its_punctuation() -> None:
    (segment,) = parse_script("## 1. Open\n\nA bowl, a ball.\n")
    words = estimated_words(segment, 4.0)
    assert [word.word for word in words] == ["A", "bowl", "a", "ball"]
    assert words[0].start == 0.0
    assert words[-1].end == pytest.approx(3.98)


def test_a_section_that_says_nothing_has_no_estimated_words() -> None:
    (segment,) = parse_script("## 1. Open\n\n[A direction alone.]\n")
    assert estimated_words(segment, 4.0) == []


def test_placing_a_take_reads_its_own_bytes_and_its_own_section(inputs: Inputs) -> None:
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes_dir / take_file("abc")).write_bytes(b"")
    placed = place(inputs, 1, a_take())
    assert placed.sound_end_seconds == pytest.approx(0.8)
    assert placed.lead_seconds == pytest.approx(0.5)
    assert placed.tail_seconds == pytest.approx(0.7)
    assert placed.span_seconds == pytest.approx(2.0)


def test_a_row_that_carries_its_sound_end_keeps_it(inputs: Inputs) -> None:
    """The file its hash names holds the same bytes it was measured on, so it is measured once."""
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes_dir / take_file("abc")).write_bytes(b"")
    assert place(inputs, 1, a_take().model_copy(update={"sound_end_seconds": 0.25})).sound_end_seconds == 0.25


def test_a_placeholder_take_writes_its_audio_and_its_words(inputs: Inputs, fake_ffmpeg: object) -> None:
    assert fake_ffmpeg is not None
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (segment,) = [s for s in inputs.spoken() if s.index == 1]
    plan = placeholder_plan(inputs, [segment])[0]
    assert plan.digest is not None
    row, written = write_placeholder_take(inputs, segment, "Open", plan.digest)
    assert [path.name for path in written] == [take_file(plan.digest), words_file(plan.digest)]
    assert all(path.exists() for path in written)
    assert row.voiced is False
    assert row.section == 1
    words = Words.read(inputs.workspace.takes_dir / words_file(plan.digest))
    assert words is not None
    assert [word.word for word in words.words] == ["A", "bowl", "A", "ball"]


def test_a_placeholder_take_closes_on_silence_so_its_sound_end_can_be_read(
    inputs: Inputs, fake_ffmpeg: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert fake_ffmpeg is not None
    asked: list[float] = []
    monkeypatch.setattr(audio, "write_clicks", lambda path, duration, times, **_k: asked.append(duration))
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (segment,) = [s for s in inputs.spoken() if s.index == 1]
    (inputs.workspace.takes_dir / take_file("d")).write_bytes(b"")
    write_placeholder_take(inputs, segment, "Open", "d")
    assert asked == [pytest.approx(segment.silent_seconds(inputs.settings.narration) + PLACEHOLDER_CLOSE_SECONDS)]


def test_a_voiced_take_writes_what_the_provider_answered(
    inputs: Inputs, fake_ffmpeg: object, fake_voice: object
) -> None:
    assert fake_ffmpeg is not None
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (segment,) = [s for s in inputs.spoken() if s.index == 1]
    request = SpeechRequest(text=segment.tts_text, voice_id=VOICE_ID, model="m")
    row, written = write_voiced_take(inputs, fake_voice, segment, "Open", "paid", request)  # type: ignore[arg-type]
    assert (inputs.workspace.takes_dir / take_file("paid")).read_bytes() == b"take"
    assert row.voiced is True
    assert row.speech_end_seconds == pytest.approx(1.0)
    assert fake_voice.requests == [request]  # type: ignore[attr-defined]
    assert len(written) == 2


def test_the_narration_is_joined_in_the_order_the_index_holds(
    inputs: Inputs, fake_ffmpeg: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The index is the one order the narration plays in, so the join never reads the script."""
    placed: list[audio.Placement] = []
    monkeypatch.setattr(audio, "concat_audio", lambda parts, _out, **_k: placed.extend(parts))
    assert fake_ffmpeg is not None
    index = Takes(
        script="script.md",
        model="m",
        output_format="mp3_44100_128",
        sections=(
            a_take(1, digest="one").model_copy(
                update={"sound_end_seconds": 0.8, "lead_seconds": 0.5, "tail_seconds": 0.7}
            ),
            a_take(2, digest="two").model_copy(
                update={"sound_end_seconds": 0.4, "lead_seconds": 0.1, "tail_seconds": 0.2}
            ),
        ),
    )
    join_takes(inputs, index)
    assert [part.path.name for part in placed] == [take_file("one"), take_file("two")]
    assert [part.lead for part in placed] == [0.5, 0.1]
    assert [part.play for part in placed] == [0.8, 0.4]
    assert [part.tail for part in placed] == [0.7, 0.2]


def test_planned_words_estimate_a_section_nothing_has_voiced_yet(inputs: Inputs) -> None:
    (segment,) = [s for s in inputs.spoken() if s.index == 1]
    plan = placeholder_plan(inputs, [segment])[0]
    words, span, estimated = planned_words(inputs, plan)
    assert estimated is True
    assert words[0].start == pytest.approx(inputs.lead_seconds(1))
    assert span == pytest.approx(inputs.lead_seconds(1) + segment.silent_seconds(inputs.settings.narration) + 0.7)


def test_planned_words_read_the_take_on_disk_when_there_is_one(
    inputs: Inputs, fake_ffmpeg: object, fake_voice: object
) -> None:
    """A cached take already carries its own words, so a cue resolves against them and not a guess."""
    assert fake_ffmpeg is not None and fake_voice is not None
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    targets = [s for s in inputs.spoken() if s.index == 1]
    plans, _why = voiced_plan(inputs, targets, model="m", voice_id=VOICE_ID)
    digest = plans[0].digest
    assert digest is not None
    (inputs.workspace.takes_dir / take_file(digest)).write_bytes(b"")
    Words(words=(Word(word="A", start=0.0, end=0.4),)).write(inputs.workspace.takes_dir / words_file(digest))
    kept, _why = voiced_plan(inputs, targets, model="m", voice_id=VOICE_ID)
    words, span, estimated = planned_words(inputs, kept[0])
    assert estimated is False
    assert [word.word for word in words] == ["A"]
    assert words[0].start == pytest.approx(0.5)
    assert span == pytest.approx(0.5 + 0.8 + 0.7)
