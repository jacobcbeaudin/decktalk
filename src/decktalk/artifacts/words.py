"""The words of one take, which is the time base every other artifact is measured against.

    takes/<digest>.words.json                    one row per spoken word of a voiced take, in seconds after the
                                                 take starts, or none when its voice sent no times
    takes/aligned/<match>-<identity>.words.json  the words an aligner measured in a take its voice sent none with
    build/narrate/<digest>.words.json            the same for a placeholder, with estimated words

Every cut DeckTalk makes is made on a word, so this is the smallest artifact and the one every
other reads: a cue resolves against it, the captions are built from it, and the clicks of a
placeholder take sit at each `start`. The rows are the same `Word` a result carries, so the file a
stage writes and the JSON a caller reads are one shape.

`Words` is the shape every words file shares, and each file is a kind named by who timed the words,
because that decides what a broken file costs:

    ProviderWords   the speech provider sent them back with its take, so only voicing it again gives them back.
                    `timed` is false when it sent none, and `model_digest` names the model that spoke
    AlignedWords    an aligner measured them in a take whose voice sent none, and measures them again for nothing
    EstimatedWords  DeckTalk estimated them from the script's pace, and estimates them again for nothing
    ClipWords       `decktalk clip` cut them from a take for one clip, into a file the author keeps

Only `ProviderWords` is a paid record, refused rather than built again when it does not read. It also
carries `audio`, the size and BLAKE3 of the take it was sent with, written in the same atomic step as
that take. A take's name is the digest of what was asked for, so a truncated or swapped audio file
keeps a valid name forever, and only this fingerprint tells a damaged copy from a good one, so a
words file without one does not read. A clip's words file beside it is the author's own input, so
`Inputs.clip_words` refuses one that does not read as `INPUT` and never asks for it to be deleted.

A take plays to its sound end and is cut there, so every reader of the words a take plays reads
them `cut_at` that sound end: a word given to the take's trailing silence ends where the sound does.

Aligned words are a cache kept under a key, `<match>-<identity>`. The match is taken over the take's
audio print and the words it should hold, and the identity over the aligner, its revision and the
digest of its model, so every alignment of one take shares a prefix and a new aligner keeps the old
alignment beside its own. A file plays only when its name is its own key and its words are spelled as
its key's words are, and one that does not read is aligned again. A copy moved onto a section's clock
is made by `on_section_clock` and is a reading, which no writer puts on disk.
"""

from __future__ import annotations

import builtins
import hashlib
import json
from pathlib import Path
from typing import ClassVar, Self

from pydantic import Field, model_validator

from decktalk.artifacts.stored import Stored, content_digest
from decktalk.artifacts.takes import TAKE_DIGITS
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

    def cut_at(self, sound_end: float) -> Self:
        """These words as a take cut at `sound_end` plays them, which is how the join plays every take.

        A word whose end runs past the take's sound end ends there, because a provider or an aligner
        can give its last word to the take's trailing silence, which never plays. A word that starts at
        or past the sound end is not heard at all, so it keeps its place in the order with no length, at
        the sound end. Every other word is as it was. The copy is a reading, which no writer puts on
        disk, so the record its voice sent is never rewritten.
        """
        cut = tuple(
            word if word.end <= sound_end else Word(word=word.word, start=min(word.start, sound_end), end=sound_end)
            for word in self.words
        )
        heard = self.model_copy(update={"words": cut})
        heard._view = True
        return heard

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
    suffix: str = Field(description="The suffix the take's audio file is named with, such as .mp3.")

    @classmethod
    def of(cls, audio: builtins.bytes, *, suffix: str) -> AudioPrint:
        """The fingerprint of these bytes, which is the audio a provider answered with, written under `suffix`."""
        return cls(bytes=len(audio), blake3=content_digest(audio), suffix=suffix)


class ProviderWords(Words):
    """Every word the speech provider sent back with a take it spoke, which only voicing the take again gives back."""

    label: ClassVar[str] = "the words the speech provider sent back with this take"

    paid: ClassVar[bool] = True
    regained: ClassVar[str] = "only voicing this take again gives these words back"

    audio: AudioPrint = Field(description="The size, BLAKE3 and suffix of the take these words came with.")
    timed: bool = Field(
        True,
        description="Whether the provider sent word times with the take. False holds no words, and the take is timed "
        "by an aligner.",
    )
    model_digest: str | None = Field(
        None, description="The digest of the model the provider served this take with, or null when it named none."
    )

    @model_validator(mode="after")
    def _no_words_when_untimed(self) -> ProviderWords:
        """Refuse words on a take whose provider sent no word times, so the two never disagree."""
        if not self.timed and self.words:
            raise ValueError("a take whose provider sent no word times holds no words")
        return self


class EstimatedWords(Words):
    """Every word of a placeholder take, or of a take no aligner could time, at the times DeckTalk estimated from the
    script's pace."""

    label: ClassVar[str] = "the words DeckTalk estimated from the script's pace"

    estimated: ClassVar[bool] = True


class SpokenWord(Word):
    """One word inside a clip with the voice and the turn that say it, which a caption of it carries and never names."""

    voice: str | None = Field(None, description="The voice that says the word, or null when the file does not say.")
    turn: int | None = Field(None, ge=1, description="The turn of its section the word is said in, or null.")


class ClipWords(Words):
    """Every word inside one clip, on the clip's own clock, as the captions show them.

    `decktalk clip` writes one beside every clip it cuts, and every build writes its film's own beside
    the film as `<name>.words.json`, each word with its voice and its turn, so a pane that plays either
    captions its speech. A section that names one in `decktalk.toml` makes it an input the author owns,
    which DeckTalk reads and never writes again.
    """

    label: ClassVar[str] = "the words of one clip"

    words: tuple[SpokenWord, ...] = Field((), description="Every spoken word with its span, in speaking order.")


def words_beside(media: Path) -> Path:
    """Where the words of a clip or a film sit, beside it under its name: `film.words.json` beside `film.mp4`."""
    return media.with_name(f"{media.stem}{WORDS_SUFFIX}")


def _head(text: str) -> str:
    """The head of the sha256 of `text`, as long as a take's digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:TAKE_DIGITS]


def alignment_match(audio: str, words: tuple[str, ...]) -> str:
    """The part of an alignment's key a take names: its audio print and the words it should hold."""
    return _head(audio + "\n" + json.dumps(list(words), separators=(",", ":")))


class AlignmentInputs(Model):
    """What one alignment is keyed by: the take's audio and words, and the aligner that timed them."""

    audio: str = Field(description="The BLAKE3 of the take's audio, as its words file's audio print records it.")
    words: tuple[str, ...] = Field(description="The words the take should hold, as the aligner was sent them.")
    aligner: str = Field(description="The name of the aligner that timed them.")
    revision: str = Field(description="The aligner's revision.")
    model_digest: str = Field(description="The digest of the model the aligner served.")

    @property
    def match(self) -> str:
        """The take's half of the key, which every alignment of this take and these words shares."""
        return alignment_match(self.audio, self.words)

    @property
    def identity(self) -> str:
        """The aligner's half of the key, which moves with its name, its revision and its model."""
        return _head("\n".join((self.aligner, self.revision, self.model_digest)))

    @property
    def key(self) -> str:
        """The name the alignment is kept under, before its suffix."""
        return f"{self.match}-{self.identity}"


class AlignedWords(Words):
    """Every word of a take whose voice sent no word times, at the times an aligner measured in its audio."""

    label: ClassVar[str] = "the words an aligner measured in this take"

    inputs: AlignmentInputs = Field(description="What these words are keyed by, which their file is named after.")
    generation: int = Field(
        ge=1, description="One more than the highest generation kept for this take when these were written."
    )

    @model_validator(mode="after")
    def _spelled_as_sent(self) -> AlignedWords:
        """Refuse words that are not the words the aligner was sent, in order, so a file can only time its own take."""
        if tuple(word.word for word in self.words) != self.inputs.words:
            raise ValueError("the aligned words are not spelled as the words they were keyed by")
        return self


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
    "SpokenWord",
    "WORDS_SUFFIX",
    "AlignedWords",
    "AlignmentInputs",
    "AudioPrint",
    "ClipWords",
    "EstimatedWords",
    "ProviderWords",
    "Words",
    "alignment_match",
    "on_section_clock",
    "words_beside",
    "words_file",
]
