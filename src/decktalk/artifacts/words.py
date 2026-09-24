"""The words of one take, which is the time base every other artifact is measured against.

    build/narrate/<hash>.words.json   one row per spoken word, in seconds after the take starts

Every cut DeckTalk makes is made on a word, so this is the smallest artifact and the one every
other reads: a cue resolves against it, the captions are built from it, and the clicks of a
placeholder take sit at each `start`. The rows are the same `Word` a result carries, so the file a
stage writes and the JSON a caller reads are one shape.
"""

from __future__ import annotations

from pydantic import Field

from decktalk.artifacts.stored import Stored
from decktalk.results import Word

WORDS_SUFFIX = ".words.json"
"""What a take's words file is called beside the take itself, which is its digest and this suffix."""


class Words(Stored):
    """Every word of one take, in the order the voice speaks them."""

    words: tuple[Word, ...] = Field((), description="Every spoken word with its span, in speaking order.")

    @property
    def end(self) -> float | None:
        """Where the last word ends, or None when the take says nothing."""
        return self.words[-1].end if self.words else None

    def shifted(self, by: float) -> tuple[Word, ...]:
        """These words moved `by` seconds later, which is how a take's words join a longer clock."""
        return tuple(Word(word=w.word, start=round(w.start + by, 3), end=round(w.end + by, 3)) for w in self.words)


def words_file(digest: str) -> str:
    """The name of the words file of the take with this digest."""
    return f"{digest}{WORDS_SUFFIX}"


__all__ = ["WORDS_SUFFIX", "Words", "words_file"]
