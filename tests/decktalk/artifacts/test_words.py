"""The words of one take, which is the time base every other artifact is measured against."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.words import WORDS_SUFFIX, Words, words_file
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
