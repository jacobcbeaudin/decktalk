"""The words of one take, which is the time base every other artifact is measured against.

    takes/<hash>.words.json   one row per spoken word, in seconds after the take starts

Every cut DeckTalk makes is made on a word, so this is the smallest artifact and the one every
other reads: a cue resolves against it, the captions are built from it, and the clicks of a
placeholder take sit at each `start`. The rows are the same `Word` a result carries, so the file a
stage writes and the JSON a caller reads are one shape.

`Words` is the shape every words file shares, and each file is a kind named by who timed the words,
because that decides what a broken file costs:

    ProviderWords   the speech provider sent them back with its take, so only voicing it again gives them back
    EstimatedWords  DeckTalk estimated them from the script's pace, and estimates them again for nothing
    ClipWords       `decktalk clip` cut them from a take for one clip, into a file the author keeps

Only `ProviderWords` is a paid record, refused rather than built again when it does not read. It also
carries `audio`, the size and BLAKE3 of the take it was sent with, written in the same atomic step as
that take. A take's name is the digest of what was asked for, so a truncated or swapped audio file
keeps a valid name forever, and only this fingerprint tells a damaged copy from a good one. A words
file with no fingerprint is read as it is and never filled in, because that would rewrite a paid
record. A clip's
words file is the author's own input once a `[[section]] words` key names it, so `Inputs.clip_words`
refuses one that does not read as `INPUT`, naming that key, and never asks for it to be deleted. Words
DeckTalk can time again for nothing, such as an aligner reading a take's audio, are a cache of their
own kind, kept under a key of their own and never at a take's `<hash>.words.json`.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from decktalk.artifacts.stored import Stored, Unreadable, content_digest, file_digest
from decktalk.findings import Model
from decktalk.results import Word

WORDS_SUFFIX = ".words.json"
"""What a take's words file is called beside the take itself, which is its digest and this suffix."""


class Words(Stored):
    """Every word of one take or one clip, in the order they are spoken."""

    label: ClassVar[str] = "the words of a take or a clip"

    words: tuple[Word, ...] = Field((), description="Every spoken word with its span, in speaking order.")

    @property
    def end(self) -> float | None:
        """Where the last word ends, or None when the take says nothing."""
        return self.words[-1].end if self.words else None

    def shifted(self, by: float) -> tuple[Word, ...]:
        """These words moved `by` seconds later, which is how a take's words join a longer clock."""
        return tuple(Word(word=w.word, start=round(w.start + by, 3), end=round(w.end + by, 3)) for w in self.words)


class AudioPrint(Model):
    """The size and the BLAKE3 of one take's audio, which is how a good copy of it is told from a damaged one."""

    bytes: int = Field(ge=0, description="How many bytes the take's audio file holds.")
    blake3: str = Field(description="The BLAKE3 of the take's audio, as `file_digest` spells it.")

    @classmethod
    def of(cls, audio: builtins.bytes) -> AudioPrint:
        """The fingerprint of these bytes, which is the audio a provider answered with."""
        return cls(bytes=len(audio), blake3=content_digest(audio))


class ProviderWords(Words):
    """Every word the speech provider sent back with a take it spoke, which only voicing the take again gives back."""

    label: ClassVar[str] = "the words the speech provider sent back with this take"

    paid: ClassVar[bool] = True
    regained: ClassVar[str] = "only voicing this take again gives these words back"

    audio: AudioPrint | None = Field(
        None, description="The size and BLAKE3 of the take these words came with, or null when the file carries none."
    )


class EstimatedWords(Words):
    """Every word of a placeholder take, at the times DeckTalk estimated from the script's pace."""

    label: ClassVar[str] = "the words DeckTalk estimated for this placeholder take"


class ClipWords(Words):
    """Every word inside one clip, on the clip's own clock, cut from the words of the take it plays.

    The file lives wherever `decktalk clip` was told to write it, and a section that names it in
    `decktalk.toml` makes it an input the author owns, which DeckTalk reads and never writes again.
    """

    label: ClassVar[str] = "the words of one clip"


def pair_fault(audio: Path, words: Path, *, whole: bool = False) -> str | None:
    """What is wrong with a take and its words file, both on disk, in one sentence, or None when they agree.

    The words must read, and the audio must hold the number of bytes they recorded, which is cheap
    enough to ask on every lookup. `whole` also takes the audio's BLAKE3, which is what tells a
    swapped take of the same length, and which a run asks once per take and before every copy. A words
    file with no fingerprint vouches only that its audio is not empty.
    """
    try:
        said = ProviderWords.parse(words)
    except Unreadable as unread:
        return str(unread)
    size = audio.stat().st_size
    printed = said.audio if said is not None else None
    if printed is None:
        return None if size else f"{audio.name} is empty."
    if size != printed.bytes:
        return f"{audio.name} holds {size} bytes where {words.name} recorded {printed.bytes}."
    if whole and file_digest(audio) != printed.blake3:
        return f"{audio.name} does not hold the bytes {words.name} recorded."
    return None


def words_file(digest: str) -> str:
    """The name of the words file of the take with this digest."""
    return f"{digest}{WORDS_SUFFIX}"


__all__ = [
    "WORDS_SUFFIX",
    "AudioPrint",
    "ClipWords",
    "EstimatedWords",
    "ProviderWords",
    "Words",
    "pair_fault",
    "words_file",
]
