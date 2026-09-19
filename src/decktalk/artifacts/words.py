"""`Word` and its file, the time base everything else shares.

    build/narration/NN-slug.words.json   one row per spoken word, in seconds after the section starts

Every cut DeckTalk makes is made on a word, so this is the smallest artifact and the one every
other reads: the cue times resolve against it, the captions are built from it, and the clicks of
a build without voice sit at each `start`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from ..jsonio import as_json, read_json, write_json


@dataclass(frozen=True)
class Word:
    """One spoken word and the span it occupies, in seconds."""

    word: str
    start: float
    end: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(word=str(d["word"]), start=float(d["start"]), end=float(d["end"]))


def read_words(path: Path) -> list[Word]:
    """The words of one take, or [] when the file does not exist yet."""
    if not path.exists():
        return []
    return [Word.from_dict(w) for w in read_json(path)]


def write_words(path: Path, words: list[Word]) -> None:
    write_json(path, as_json(words), indent=1)
