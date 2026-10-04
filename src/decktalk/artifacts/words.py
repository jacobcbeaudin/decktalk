"""The words of one take, which is the time base every other artifact is measured against.

    takes/<digest>.words.json          one row per spoken word of a voiced take, in seconds after the take starts
    build/narrate/<digest>.words.json  the same for a placeholder, with estimated words

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
refuses one that does not read as `INPUT`, naming that key, and never asks for it to be deleted. Aligned
words under the takes directory's `aligned/` are read by no stage and never stand in for a take's
`<digest>.words.json`. A copy moved onto a
section's clock is made by `on_section_clock` and is a reading, which no writer puts on disk.
"""

from __future__ import annotations

import builtins
from typing import ClassVar

from pydantic import Field

from decktalk.artifacts.stored import Stored, content_digest
from decktalk.findings import Model
from decktalk.results import Word

WORDS_SUFFIX = ".words.json"
"""What a take's words file is called beside the take itself, which is its digest and this suffix."""


class Words(Stored):
    """Every word of one take or one clip, in the order they are spoken."""

    label: ClassVar[str] = "the words of a take or a clip"

    estimated: ClassVar[bool] = False
    """True on words DeckTalk timed from the script's pace rather than from audio, so every time is a guess."""

    words: tuple[Word, ...] = Field((), description="Every spoken word with its span, in speaking order.")

    @property
    def end(self) -> float | None:
        """Where the last word ends, or None when the take says nothing."""
        return self.words[-1].end if self.words else None

    def shifted(self, by: float) -> tuple[Word, ...]:
        """These words moved `by` seconds later, which is how a take's words join a longer clock."""
        return tuple(Word(word=w.word, start=round(w.start + by, 3), end=round(w.end + by, 3)) for w in self.words)


class AudioPrint(Model):
    """The size, the BLAKE3 and the suffix of one take's audio, which is how a good copy is found and told apart.

    The suffix is the format the take was written in, so a take found after the voice moved to another
    format is still played under the name it was written with.
    """

    bytes: int = Field(ge=0, description="How many bytes the take's audio file holds.")
    blake3: str = Field(description="The BLAKE3 of the take's audio, as `file_digest` spells it.")
    suffix: str | None = Field(
        None, description="The suffix the take's audio file is named with, or null when the file records none."
    )

    @classmethod
    def of(cls, audio: builtins.bytes, *, suffix: str) -> AudioPrint:
        """The fingerprint of these bytes, which is the audio a provider answered with, written under `suffix`."""
        return cls(bytes=len(audio), blake3=content_digest(audio), suffix=suffix)


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

    estimated: ClassVar[bool] = True


class ClipWords(Words):
    """Every word inside one clip, on the clip's own clock, cut from the words of the take it plays.

    The file lives wherever `decktalk clip` was told to write it, and a section that names it in
    `decktalk.toml` makes it an input the author owns, which DeckTalk reads and never writes again.
    """

    label: ClassVar[str] = "the words of one clip"


def on_section_clock[W: Words](words: W, lead: float) -> W:
    """These words moved `lead` seconds later onto their section's clock, as a copy no writer puts on disk.

    A words file holds its take's own clock, and a section's clock starts its lead earlier, so a copy on
    the section clock is a reading of the record and never the record: `Stored.text` refuses it.
    """
    moved = words.model_copy(update={"words": words.shifted(lead)}) if lead else words.model_copy()
    moved._view = True
    return moved


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
    "on_section_clock",
    "words_file",
]
