"""Writing one take, placing it, and joining every take into one narration track."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from decktalk.artifacts import (
    AlignedWords,
    AlignmentInputs,
    AudioPrint,
    EstimatedWords,
    Placed,
    ProviderWords,
    Take,
    Takes,
    TimedBy,
    TurnKey,
    Words,
    take_file,
    words_file,
)
from decktalk.events import TakeCharged
from decktalk.inputs import Inputs
from decktalk.media import audio
from decktalk.results import TakeOutcome, Word
from decktalk.speech import SpeechFactory, canonical_text
from decktalk.stages.cost import Buy
from decktalk.stages.narrate import narrate, take_states
from decktalk.stages.narrate.align import first_words
from decktalk.stages.narrate.plan import placeholder_inputs
from decktalk.stages.narrate.takes import (
    PLACEHOLDER_CLOSE_SECONDS,
    charge_take,
    join_takes,
    place,
    retimed,
    take_row,
    write_placeholder_take,
)
from support.fakes import FakeFfmpeg, FakeVoice
from support.runs import Watched
from support.takes import TAKE_SUFFIX, a_take

from .conftest import DIALOGUE, WITH_A_GUEST, a_paid_take


def test_placing_a_take_reads_its_own_bytes_and_its_own_section(inputs: Inputs) -> None:
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes / take_file("0000000000000abc", TAKE_SUFFIX)).write_bytes(b"")
    placed = place(inputs, a_paid_take(), gap=None)
    assert placed.sound_end_seconds == pytest.approx(0.8)
    assert placed.lead_seconds == pytest.approx(0.5)
    assert placed.tail_seconds == pytest.approx(0.7)
    assert placed.span_seconds == pytest.approx(2.0)


def test_a_row_that_carries_its_sound_end_keeps_it(inputs: Inputs) -> None:
    """The file its hash names holds the same bytes it was measured on, so it is measured once."""
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes / take_file("0000000000000abc", TAKE_SUFFIX)).write_bytes(b"")
    kept = a_paid_take().model_copy(update={"sound_end_seconds": 0.25})
    assert place(inputs, kept, gap=None).sound_end_seconds == 0.25


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_placeholder_take_writes_its_audio_and_its_words(inputs: Inputs) -> None:
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    digest = placeholder_inputs(inputs, section).digest
    row, written = write_placeholder_take(inputs, section, section.turns[0], "Open", digest)
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
    write_placeholder_take(inputs, section, section.turns[0], "Open", "000000000000000d")
    assert asked == [pytest.approx(section.placeholder_seconds(inputs.settings.narration) + PLACEHOLDER_CLOSE_SECONDS)]


def test_a_voiced_take_is_charged_on_the_stream_once(inputs: Inputs, watched: Watched) -> None:
    """The line a host's ledger reads carries the section, the take, its characters and its price."""
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    buy = Buy(
        characters=len(canonical_text(section.pieces)),
        seconds=section.estimated_seconds(inputs.settings.narration),
        sections=(1,),
        voice=inputs.voices["narrator"],
    )
    charge_take(inputs, watched.run, section, section.turns[0], "00000000000000af", buy)
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


VOICED = "0000000000000abc"
"""A voiced take's digest, held in the takes directory by the tests that read its words."""

SAID = (Word(word="A", start=0.0, end=0.3), Word(word="bowl", start=0.3, end=0.7))


@pytest.mark.usefixtures("fake_ffmpeg")
def test_a_row_says_who_timed_its_words_and_the_model_that_spoke(inputs: Inputs) -> None:
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    said = ProviderWords(words=SAID, model_digest="sha256:5eed")
    row = take_row(inputs, section, section.turns[0], "Open", VOICED, said, said=said)
    assert (row.voiced, row.timed_by, row.alignment, row.model_digest) == (True, TimedBy.PROVIDER, None, "sha256:5eed")
    assert row.speech_end_seconds == SAID[-1].end
    keyed = AlignmentInputs(audio="a" * 16, words=("A", "bowl"), aligner="x", revision="1", model_digest="m")
    aligned = AlignedWords(words=SAID, inputs=keyed, generation=1)
    moved = retimed(row, aligned)
    assert (moved.timed_by, moved.alignment, moved.model_digest) == (TimedBy.ALIGNED, keyed.key, "sha256:5eed")
    assert retimed(moved, EstimatedWords(words=SAID)).alignment is None
    placeholder = placeholder_inputs(inputs, section).digest
    row = take_row(inputs, section, section.turns[0], "Open", placeholder, EstimatedWords(words=SAID), said=None)
    assert (row.voiced, row.timed_by, row.model_digest) == (False, TimedBy.ESTIMATED, None)


def test_a_take_with_no_word_times_keeps_the_model_its_voice_served_on_its_row(
    inputs: Inputs, fake_ffmpeg: FakeFfmpeg
) -> None:
    """The served model belongs to the take, so it reaches the row even while DeckTalk estimates its words."""
    fake_ffmpeg.duration_seconds = 2.0
    inputs.workspace.takes.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes / take_file(VOICED, TAKE_SUFFIX)).write_bytes(b"take")
    held = ProviderWords(timed=False, model_digest="sha256:5eed", audio=AudioPrint.of(b"take", suffix=TAKE_SUFFIX))
    held.write(inputs.workspace.takes / words_file(VOICED))
    (section,) = [s for s in inputs.spoken() if s.number == 1]
    words, said = first_words(inputs, section.spoken, VOICED)
    assert said == held
    assert type(words) is EstimatedWords and [w.word for w in words.words] == ["A", "bowl", "A", "ball"]
    row = take_row(inputs, section, section.turns[0], "Open", VOICED, words, said=said)
    assert (row.timed_by, row.model_digest, row.alignment) == (TimedBy.ESTIMATED, "sha256:5eed", None)


# ---- turns on the section clock -----------------------------------------------------------------


def turn_row(section: int, turn: int, *, sound: float, lead: float, tail: float, voice: str = "narrator") -> Take:
    """One take index row of one turn, placed by hand at these numbers."""
    return a_take(
        section,
        turn=turn,
        voice=voice,
        seconds=sound,
        digest=f"{section:08x}{turn:08x}",
        lead_seconds=lead,
        tail_seconds=tail,
    )


DIALOGUE_INDEX = Takes(
    script="script.md",
    model="m",
    output_format="mp3_44100_128",
    sections=(
        turn_row(1, 1, sound=2.0, lead=0.5, tail=0.7),
        turn_row(2, 1, sound=1.0, lead=0.5, tail=0.3),
        turn_row(2, 2, sound=1.5, lead=0.0, tail=0.4, voice="guest"),
        turn_row(2, 3, sound=0.5, lead=0.0, tail=0.7),
    ),
)
"""A one-turn section, then a dialogue of three turns: the narrator, the guest, the narrator."""


def test_a_section_runs_for_its_lead_its_turns_their_gaps_and_its_tail() -> None:
    assert DIALOGUE_INDEX.span_seconds(2) == round(0.5 + 1.0 + 0.3 + 1.5 + 0.4 + 0.5 + 0.7, 3)
    assert DIALOGUE_INDEX.placed[2] == Placed(start=3.2, end=8.1, speech_end=7.4)
    assert DIALOGUE_INDEX.placed_turns[TurnKey(2, 2)] == Placed(start=5.0, end=6.9, speech_end=6.5)
    assert DIALOGUE_INDEX.of(2) == DIALOGUE_INDEX.sections[1]
    assert [row.turn for row in DIALOGUE_INDEX.turns_of(2)] == [1, 2, 3]
    assert DIALOGUE_INDEX.numbers == (1, 2)
    assert DIALOGUE_INDEX.total_seconds == 8.1


def test_a_section_of_one_turn_is_placed_as_before() -> None:
    """The rows of a film no voice direction touches place exactly as the clock always added them."""
    index = Takes(
        script="script.md",
        model="m",
        output_format="mp3_44100_128",
        sections=(
            a_take(1, seconds=2.0, lead_seconds=0.5, tail_seconds=0.7),
            a_take(2, seconds=3.0, lead_seconds=0.5, tail_seconds=0.7),
        ),
    )
    assert index.placed == {1: Placed(0.0, 3.2, 2.5), 2: Placed(3.2, 7.4, 6.7)}
    assert index.placed_turns == {TurnKey(1, 1): index.placed[1], TurnKey(2, 1): index.placed[2]}
    assert index.span_seconds(1) == index.sections[0].span_seconds


def test_only_the_first_turn_of_a_section_has_a_lead() -> None:
    with pytest.raises(ValidationError, match="only its first turn has a lead"):
        Takes(
            script="script.md",
            model="m",
            output_format="mp3_44100_128",
            sections=(turn_row(1, 1, sound=1.0, lead=0.5, tail=0.3), turn_row(1, 2, sound=1.0, lead=0.5, tail=0.7)),
        )


@pytest.mark.parametrize(
    "keys",
    [[(1, 2)], [(1, 1), (1, 1)], [(2, 1), (1, 1)], [(1, 1), (1, 3)]],
    ids=["not-from-one", "twice", "out-of-order", "a-gap"],
)
def test_turns_ascend_and_count_from_one(keys: list[tuple[int, int]]) -> None:
    rows = tuple(turn_row(section, turn, sound=1.0, lead=0.5 if turn == 1 else 0.0, tail=0.3) for section, turn in keys)
    with pytest.raises(ValidationError, match="turn"):
        Takes(script="script.md", model="m", output_format="mp3_44100_128", sections=rows)


@pytest.mark.usefixtures("fake_ffmpeg")
def test_the_join_puts_each_gap_between_two_turns_and_one_lead_and_one_tail_around_the_section(
    make_inputs: Callable[..., Inputs],
    make_run: Callable[..., Watched],
    monkeypatch: pytest.MonkeyPatch,
    voices: dict[str, SpeechFactory],
) -> None:
    """The pause written where the voice changes is the gap, the default gap fills the other, and only the
    section's edges carry its lead and its tail."""
    guest = FakeVoice(name="dtsp")
    voices["dtsp"] = lambda _context: guest
    placed: list[audio.Placement] = []
    monkeypatch.setattr(audio, "concat_audio", lambda parts, _out, **_k: placed.extend(parts))
    project = make_inputs(toml=WITH_A_GUEST, script=DIALOGUE)
    narrate(project, make_run(project).run)
    cfg = project.settings.narration
    assert [(part.lead, part.tail) for part in placed] == [
        (cfg.lead_seconds, cfg.tail_seconds),
        (cfg.lead_seconds, 1.0),
        (0.0, cfg.turn_gap_seconds),
        (0.0, cfg.tail_seconds),
        (cfg.lead_seconds, cfg.tail_seconds),
    ]
    index = project.takes(required=True)
    assert [(row.section, row.turn, row.voice) for row in index.sections] == [
        (1, 1, "narrator"),
        (2, 1, "narrator"),
        (2, 2, "guest"),
        (2, 3, "narrator"),
        (3, 1, "narrator"),
    ]


@pytest.mark.usefixtures("fake_ffmpeg")
def test_retiming_the_pause_where_the_voice_changes_moves_the_rows_and_buys_nothing(
    make_inputs: Callable[..., Inputs], make_run: Callable[..., Watched], fake_voice: FakeVoice
) -> None:
    project = make_inputs(
        toml=WITH_A_GUEST.replace('provider = "dtsp"\nid = "af_heart"', 'id = "guest-voice"'), script=DIALOGUE
    )
    narrate(project, make_run(project, spend=True).run)
    sent = len(fake_voice.requests)
    longer = make_inputs(
        toml=WITH_A_GUEST.replace('provider = "dtsp"\nid = "af_heart"', 'id = "guest-voice"'),
        script=DIALOGUE.replace("[pause 1]", "[pause 2.5]"),
    )
    assert not take_states(longer).plan(spend=True).cost.buys
    result = narrate(longer, make_run(longer, spend=True).run)
    assert len(fake_voice.requests) == sent
    assert {row.outcome for row in result.sections} == {TakeOutcome.KEPT}
    (first, *_rest) = longer.takes(required=True).turns_of(2)
    assert first.tail_seconds == 2.5
