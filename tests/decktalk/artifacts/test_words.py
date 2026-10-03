"""The words of one take, which is the time base every other artifact is measured against."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts.stored import file_digest
from decktalk.artifacts.words import WORDS_SUFFIX, AudioPrint, ProviderWords, Words, words_file
from decktalk.errors import InputError
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
    assert timers == {"ProviderWords": True, "EstimatedWords": False, "ClipWords": False}


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
