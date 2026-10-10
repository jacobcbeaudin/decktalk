"""The words of one take, which is the time base every other artifact is measured against."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts.stored import file_digest
from decktalk.artifacts.words import (
    WORDS_SUFFIX,
    AlignedWords,
    AlignmentInputs,
    AudioPrint,
    ProviderWords,
    Words,
    alignment_match,
    on_section_clock,
    words_file,
)
from decktalk.errors import InputError, NotBuiltError
from decktalk.results import Word

SPOKEN = Words(words=(Word(word="two", start=0.0, end=0.4), Word(word="friends", start=0.4, end=0.9)))


def test_a_words_file_is_named_by_the_digest_of_its_take() -> None:
    assert words_file("abc123") == f"abc123{WORDS_SUFFIX}"


def test_an_empty_words_file_ends_nowhere() -> None:
    assert Words().end is None


def test_the_end_is_where_the_last_word_ends() -> None:
    assert SPOKEN.end == 0.9


def test_shifting_moves_every_word_and_keeps_its_length() -> None:
    moved = SPOKEN.shifted(0.5)
    assert [(w.start, w.end) for w in moved] == [(0.5, 0.9), (0.9, 1.4)]
    assert [w.word for w in moved] == ["two", "friends"]


def test_words_round_trip_through_their_own_file(tmp_path: Path) -> None:
    path = SPOKEN.write(tmp_path / words_file("abc123"))
    assert Words.read(path) == SPOKEN


def test_every_words_file_is_named_by_who_timed_it_and_only_a_provider_s_words_are_paid() -> None:
    """Who timed the words decides what a broken file costs, so each timer is its own kind.

    A provider's words come back only by voicing the take again. DeckTalk estimates a placeholder's again
    for nothing, and a clip's are cut again from its take. A new timer, such as an aligner, adds its
    row here and says which it is.
    """
    timers = {kind.__name__: kind.paid for kind in Words.__subclasses__()}
    assert timers == {"ProviderWords": True, "EstimatedWords": False, "ClipWords": False, "AlignedWords": False}


def test_a_providers_words_that_do_not_read_say_only_voicing_the_take_again_gives_them_back(tmp_path: Path) -> None:
    """The sentence is true for a free voice too, which charged nothing, so it names the remedy and never a payment."""
    path = tmp_path / words_file("abc123")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InputError) as refused:
        ProviderWords.read(path)
    assert "Only voicing this take again gives these words back" in str(refused.value)
    assert "paid for" not in str(refused.value)
    assert "costs money on a paid provider" in (refused.value.hint or "")


def test_a_providers_words_carry_the_fingerprint_of_the_audio_they_were_sent_with(tmp_path: Path) -> None:
    audio = tmp_path / "take.mp3"
    audio.write_bytes(b"the take's own bytes")
    printed = AudioPrint.of(audio.read_bytes(), suffix=".mp3")
    assert (printed.bytes, printed.blake3) == (audio.stat().st_size, file_digest(audio))
    path = ProviderWords(words=SPOKEN.words, audio=printed).write(tmp_path / words_file("abc123"))
    assert ProviderWords.read(path) == ProviderWords(words=SPOKEN.words, audio=printed)


def test_a_providers_words_with_no_fingerprint_still_read(tmp_path: Path) -> None:
    """A words file with no fingerprint is read as it is, because filling one in would rewrite a paid record."""
    path = SPOKEN.write(tmp_path / words_file("abc123"))
    read = ProviderWords.read(path)
    assert read is not None and read.audio is None


def test_words_moved_onto_a_section_clock_are_never_written_to_any_file(tmp_path: Path) -> None:
    """A copy on the section clock is a reading of a paid record, so writing it would overwrite what was bought."""
    printed = AudioPrint.of(b"the take's own bytes", suffix=".mp3")
    path = ProviderWords(words=SPOKEN.words, audio=printed).write(tmp_path / words_file("abc123"))
    kept = path.read_bytes()
    read = ProviderWords.read(path)
    assert read is not None
    moved = on_section_clock(read, 0.5)
    with pytest.raises(ValueError, match="never written"):
        moved.write(path)
    with pytest.raises(ValueError, match="never written"):
        _ = moved.text
    assert path.read_bytes() == kept
    assert type(moved) is ProviderWords and moved.audio == read.audio
    assert moved.words[0].start == read.words[0].start + 0.5


def test_a_copy_at_no_lead_keeps_every_time_as_it_was_and_is_still_never_written(tmp_path: Path) -> None:
    moved = on_section_clock(SPOKEN, 0.0)
    assert moved.words == SPOKEN.words
    with pytest.raises(ValueError, match="never written"):
        moved.write(tmp_path / words_file("abc123"))
    assert not (tmp_path / words_file("abc123")).exists()


def test_words_on_a_section_clock_equal_the_same_fields_read_from_the_file(tmp_path: Path) -> None:
    """Equality reads the words, never whether a value is a copy made for a reader."""
    read = Words.read(SPOKEN.write(tmp_path / words_file("abc123")))
    assert read is not None
    moved = on_section_clock(read, 0.0)
    assert moved == read and read == moved
    assert hash(moved) == hash(read)
    assert on_section_clock(read, 0.5) != read


def test_a_providers_words_file_without_the_new_fields_reads_as_timed_by_an_unnamed_model(tmp_path: Path) -> None:
    path = tmp_path / words_file("abc123")
    path.write_text('{"words": [{"word": "two", "start": 0.0, "end": 0.4}], "audio": null}', encoding="utf-8")
    said = ProviderWords.read(path)
    assert said is not None
    assert said.timed is True
    assert said.model_digest is None


def test_an_older_empty_providers_words_file_reads_as_a_take_with_no_word_times(tmp_path: Path) -> None:
    """A take that says nothing would leave every cue unresolved, so an empty older file goes to the aligner."""
    path = tmp_path / words_file("abc123")
    path.write_text('{"words": [], "audio": null}', encoding="utf-8")
    said = ProviderWords.read(path)
    assert said is not None
    assert said.timed is False


def test_a_words_file_that_says_its_voice_sent_no_times_holds_no_words() -> None:
    with pytest.raises(ValueError, match="sent no word times"):
        ProviderWords(words=SPOKEN.words, timed=False)
    assert ProviderWords(words=(), timed=False).words == ()


ALIGNED = AlignmentInputs(
    audio="0123456789abcdef",
    words=("two", "friends"),
    aligner="fake-aligner",
    revision="1",
    model_digest="sha256:0f00",
)


@pytest.mark.parametrize(
    ("moved", "same_match"),
    [
        ({"audio": "fedcba9876543210"}, False),
        ({"words": ("two", "enemies")}, False),
        ({"words": ("twof", "riends")}, False),
        ({"aligner": "other-aligner"}, True),
        ({"revision": "2"}, True),
        ({"model_digest": "sha256:5eed"}, True),
    ],
    ids=["print", "word", "word-boundary", "aligner", "revision", "model-digest"],
)
def test_aligned_words_are_a_cache_kept_under_a_key_that_moves_with_every_input(
    moved: dict[str, object], same_match: bool
) -> None:
    """The take and its words name the match, and the aligner names the identity, so one take's alignments share a
    prefix."""
    other = ALIGNED.model_copy(update=moved)
    assert other.key != ALIGNED.key
    assert (other.match == ALIGNED.match) is same_match
    assert ALIGNED.key == f"{ALIGNED.match}-{ALIGNED.identity}"
    assert ALIGNED.match == alignment_match(ALIGNED.audio, ALIGNED.words)
    assert AlignedWords.paid is False


def test_aligned_words_that_do_not_read_are_built_again(tmp_path: Path) -> None:
    path = tmp_path / f"{ALIGNED.key}{WORDS_SUFFIX}"
    good = AlignedWords(
        words=(Word(word="two", start=0.0, end=0.4), Word(word="friends", start=0.4, end=0.9)),
        inputs=ALIGNED,
        generation=1,
    )
    assert AlignedWords.read(good.write(path)) == good
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(NotBuiltError):
        AlignedWords.read(path)
    with pytest.raises(ValueError, match="spelled as"):
        AlignedWords(words=(Word(word="two", start=0.0, end=0.4),), inputs=ALIGNED, generation=1)
