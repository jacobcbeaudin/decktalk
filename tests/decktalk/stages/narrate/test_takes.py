"""Writing one take, placing it, and joining every take into one narration track."""

from __future__ import annotations

import pytest

from decktalk.artifacts import Takes, Words, take_file, words_file
from decktalk.events import TakeCharged
from decktalk.inputs import Inputs
from decktalk.inputs.script import parse_script
from decktalk.media import audio
from decktalk.speech import SpeechRequest, canonical_text
from decktalk.stages.narrate.plan import placeholder_inputs
from decktalk.stages.narrate.takes import (
    PLACEHOLDER_CLOSE_SECONDS,
    estimated_words,
    join_takes,
    place,
    write_placeholder_take,
    write_voiced_take,
)
from support.fakes import FakeVoice
from support.runs import Watched
from support.takes import TAKE_SUFFIX

from .conftest import VOICE_ID, a_paid_take


def test_estimated_words_space_the_section_evenly_and_drop_its_punctuation() -> None:
    (section,) = parse_script("## 1. Open\n\nA bowl, a ball.\n")
    words = estimated_words(section, 4.0)
    assert [word.word for word in words] == ["A", "bowl", "a", "ball"]
    assert words[0].start == 0.0
    assert words[-1].end == pytest.approx(3.98)


def test_a_section_that_says_nothing_has_no_estimated_words() -> None:
    (section,) = parse_script("## 1. Open\n\n[A direction alone.]\n")
    assert estimated_words(section, 4.0) == []


def test_placing_a_take_reads_its_own_bytes_and_its_own_section(inputs: Inputs) -> None:
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes / take_file("0000000000000abc", TAKE_SUFFIX)).write_bytes(b"")
    placed = place(inputs, 1, a_paid_take())
    assert placed.sound_end_seconds == pytest.approx(0.8)
    assert placed.lead_seconds == pytest.approx(0.5)
    assert placed.tail_seconds == pytest.approx(0.7)
    assert placed.span_seconds == pytest.approx(2.0)


def test_a_row_that_carries_its_sound_end_keeps_it(inputs: Inputs) -> None:
    """The file its hash names holds the same bytes it was measured on, so it is measured once."""
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes / take_file("0000000000000abc", TAKE_SUFFIX)).write_bytes(b"")
    assert place(inputs, 1, a_paid_take().model_copy(update={"sound_end_seconds": 0.25})).sound_end_seconds == 0.25


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_placeholder_take_writes_its_audio_and_its_words(inputs: Inputs) -> None:
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    digest = placeholder_inputs(inputs, section).digest
    row, written = write_placeholder_take(inputs, section, "Open", digest)
    assert [path.name for path in written] == [take_file(digest, TAKE_SUFFIX), words_file(digest)]
    assert all(path.exists() for path in written)
    assert row.voiced is False
    assert row.section == 1
    words = Words.read(inputs.workspace.narrate_dir / words_file(digest))
    assert words is not None
    assert [word.word for word in words.words] == ["A", "bowl", "A", "ball"]


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_placeholder_take_closes_on_silence_so_its_sound_end_can_be_read(
    inputs: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[float] = []
    monkeypatch.setattr(audio, "write_clicks", lambda path, duration, times, **_k: asked.append(duration))
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    (inputs.workspace.takes / take_file("000000000000000d", TAKE_SUFFIX)).write_bytes(b"")
    write_placeholder_take(inputs, section, "Open", "000000000000000d")
    assert asked == [pytest.approx(section.placeholder_seconds(inputs.settings.narration) + PLACEHOLDER_CLOSE_SECONDS)]


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_voiced_take_writes_what_the_provider_answered(
    inputs: Inputs, watched: Watched, fake_voice: FakeVoice
) -> None:
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    request = SpeechRequest(pieces=section.pieces, voice_id=VOICE_ID, model="m")
    row, written = write_voiced_take(inputs, watched.run, fake_voice, section, "Open", "00000000000000af", request)
    assert (inputs.workspace.takes / take_file("00000000000000af", TAKE_SUFFIX)).read_bytes() == b"take"
    assert row.voiced is True
    assert row.speech_end_seconds == pytest.approx(1.0)
    assert fake_voice.requests == [request]
    assert len(written) == 2


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_voiced_take_is_charged_on_the_stream_once(inputs: Inputs, watched: Watched, fake_voice: FakeVoice) -> None:
    """The line a host's ledger reads carries the section, the take, its characters and its price."""
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    request = SpeechRequest(pieces=section.pieces, voice_id=VOICE_ID, model="m")
    write_voiced_take(inputs, watched.run, fake_voice, section, "Open", "00000000000000af", request)
    (charged,) = watched.of(TakeCharged)
    assert charged.section == 1
    assert charged.digest == "00000000000000af"
    assert charged.characters == len(canonical_text(section.pieces))
    assert charged.dollars == pytest.approx(len(canonical_text(section.pieces)) / 1000 * 0.30)


@pytest.mark.usefixtures("fake_ffmpeg")
def test_the_narration_is_joined_in_the_order_the_index_holds(inputs: Inputs, monkeypatch: pytest.MonkeyPatch) -> None:
    """The index is the one order the narration plays in, so the join never reads the script."""
    placed: list[audio.Placement] = []
    monkeypatch.setattr(audio, "concat_audio", lambda parts, _out, **_k: placed.extend(parts))
    index = Takes(
        script="script.md",
        model="m",
        output_format="mp3_44100_128",
        sections=(
            a_paid_take(1, digest="0000000000000001").model_copy(
                update={"sound_end_seconds": 0.8, "lead_seconds": 0.5, "tail_seconds": 0.7}
            ),
            a_paid_take(2, digest="0000000000000002").model_copy(
                update={"sound_end_seconds": 0.4, "lead_seconds": 0.1, "tail_seconds": 0.2}
            ),
        ),
    )
    join_takes(inputs, index)
    assert [part.path.name for part in placed] == [
        take_file("0000000000000001", TAKE_SUFFIX),
        take_file("0000000000000002", TAKE_SUFFIX),
    ]
    assert [part.lead for part in placed] == [0.5, 0.1]
    assert [part.play for part in placed] == [0.8, 0.4]
    assert [part.tail for part in placed] == [0.7, 0.2]
