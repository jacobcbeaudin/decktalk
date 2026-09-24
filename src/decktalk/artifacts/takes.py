"""The take index, and the frozen inputs a take's name is taken over.

    build/narrate/<hash>.mp3     one take, named by the content that produced it
    build/narrate/takes.json     which section plays which take, and the clock the join makes

A take is identified by its content hash and by nothing else, so the index maps a section to a piece
of content and never the other way round. Renumbering a section rewrites one row and moves no file,
and two sections with the same words share one take.

`TakeInputs` is the whole of what that hash is taken over, declared as a model so the set is frozen
by a shape rather than by a convention. Every byte of speech is paid for once, so adding a field or
changing the order here re-voices every project there is, which is what
`tests/decktalk/artifacts/test_takes.py` holds against the digests of a film that was really voiced.
The voice id is one of the inputs because two voices reading one sentence are two different takes,
and it is a published name rather than a secret, so it is written into the file a reader can see.

The index is also the time base. A section runs for its lead, then its take up to where the take's
sound ends, then its tail, and the sections run in section order, so where each one sits in the
joined narration is arithmetic over the rows rather than a second file that can disagree with them.
Each of those three numbers is a pure function of the take's own bytes and its own section's
settings, so a section lands the same way whether this run voiced its take or found it cached.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

from decktalk.artifacts.stored import Stored
from decktalk.findings import MODEL
from decktalk.results import SectionKey, SectionNumber

TAKE_DIGITS = 16
"""How much of the sha256 names a take, which is far more than enough that two never collide."""

PLACEHOLDER_PREFIX = "placeholder-"
"""What marks the digest of a take nobody paid for, so the two kinds never share a file name."""

PLACEHOLDER_DIGITS = 10
"""How much of the sha256 names a placeholder take, which is regenerated rather than bought."""


class TakeInputs(BaseModel):
    """Everything that decides what a paid take sounds like, which is everything its name is taken over.

    The payload is the fields below joined by newlines, in the order they are declared. A reader
    who wants to know why a take was voiced again compares two of these rather than guessing.
    """

    model_config = MODEL

    provider: str = Field(description="The speech provider that spoke this take, such as elevenlabs.")
    voice: str = Field(description="The provider's id for the voice, which is a published name and not a secret.")
    model: str = Field(description="The provider's model id, which changes how the same words are read.")
    output_format: str = Field(description="The audio format asked for, such as mp3_44100_128.")
    settings: str = Field(description="The provider's voice settings as compact JSON with its keys sorted.")
    text: str = Field(description="The exact text sent to the voice, with its pause tags.")

    @classmethod
    def of(
        cls,
        *,
        provider: str,
        voice: str,
        model: str,
        output_format: str,
        settings: dict[str, Any],
        text: str,
    ) -> TakeInputs:
        """These inputs with the voice settings rendered the one way the digest is taken over."""
        return cls(
            provider=provider,
            voice=voice,
            model=model,
            output_format=output_format,
            settings=json.dumps(settings, sort_keys=True),
            text=text,
        )

    @property
    def payload(self) -> str:
        """The bytes the digest is taken of, which is every field in declaration order, newline separated."""
        return "\n".join(str(getattr(self, name)) for name in type(self).model_fields)

    @property
    def digest(self) -> str:
        """The take's name, which is the head of the sha256 of the payload."""
        return hashlib.sha256(self.payload.encode("utf-8")).hexdigest()[:TAKE_DIGITS]


class PlaceholderInputs(BaseModel):
    """Everything that decides what a placeholder take sounds like, which is its length and its clicks.

    No credit is spent on one, so its digest exists only to let an unchanged section be skipped, and
    its prefix keeps it out of the paid takes a run must never overwrite.
    """

    model_config = MODEL

    words_per_minute: float = Field(gt=0, description="The pace the placeholder is sized at.")
    beat_seconds: float = Field(ge=0, description="How long a declared pause is held in a placeholder.")
    text: str = Field(description="The spoken text this placeholder stands in for.")

    @property
    def payload(self) -> str:
        """The bytes the digest is taken of, which is every field in declaration order, newline separated."""
        return "\n".join(str(getattr(self, name)) for name in type(self).model_fields)

    @property
    def digest(self) -> str:
        """The placeholder's name, which is marked so no paid take can ever be mistaken for one."""
        return PLACEHOLDER_PREFIX + hashlib.sha256(self.payload.encode("utf-8")).hexdigest()[:PLACEHOLDER_DIGITS]


def take_file(digest: str) -> str:
    """The name of the audio file of the take with this digest."""
    return f"{digest}.mp3"


def is_placeholder(digest: str) -> bool:
    """True when this digest names a take nobody paid for."""
    return digest.startswith(PLACEHOLDER_PREFIX)


class Take(BaseModel):
    """One section's take: the files it names, what it cost to make, and where it lands."""

    model_config = MODEL

    section: SectionNumber
    key: SectionKey
    chapter: str = Field(description="The section's title, which the film's chapter marker carries.")
    hash: str = Field(description="The digest of the inputs this take was made from, which names its files.")
    voiced: bool = Field(description="True when a provider spoke this take, false on a placeholder.")
    word_count: int = Field(ge=0, description="How many words this take speaks.")
    characters: int = Field(ge=0, description="How many characters were sent to the voice, which is what is billed.")
    estimated_seconds: float = Field(ge=0, description="How long the script said this take would run.")
    duration_seconds: float = Field(ge=0, description="How long the audio file runs, measured from its own bytes.")
    speech_end_seconds: float | None = Field(None, ge=0, description="Where the last word ends, or null.")
    sound_end_seconds: float | None = Field(None, ge=0, description="Where the take's sound ends, or null.")
    lead_seconds: float = Field(0.0, ge=0, description="Silence placed before the take, which is not in the file.")
    tail_seconds: float = Field(0.0, ge=0, description="Silence placed after the take's sound, also not in the file.")
    spoken: str = Field(description="The words the voice says, with the script's punctuation, which captions borrow.")

    @property
    def file(self) -> str:
        """The take's audio file, which is named by its digest and lives in the take directory."""
        return take_file(self.hash)

    @property
    def sound_seconds(self) -> float:
        """How much of the take plays, which is up to its sound end, or all of it when that was not measured."""
        return self.duration_seconds if self.sound_end_seconds is None else self.sound_end_seconds

    @property
    def span_seconds(self) -> float:
        """How long the section runs in the joined narration: its lead, its take to its last sound, its tail."""
        return round(self.lead_seconds + self.sound_seconds + self.tail_seconds, 3)


class Takes(Stored):
    """The take index: what was voiced, with what, and the clock the joined narration runs on.

    Nothing here is stored that the rows already say. How long the narration runs and whether any of
    it is a placeholder are read off the rows, so the file cannot hold a total that disagrees with
    what it lists.
    """

    script: str = Field(description="The script these takes were made from, project-relative.")
    model: str = Field(description="The provider model every voiced row was spoken by.")
    output_format: str = Field(description="The audio format every row was asked for in.")
    sections: tuple[Take, ...] = Field((), description="One row per narrated section, in section order.")

    @property
    def estimated(self) -> bool:
        """True when any row is a placeholder, which is what tells a reader the clock is a guess."""
        return any(not take.voiced for take in self.sections)

    @property
    def total_seconds(self) -> float:
        """How long the joined narration runs, which is every section's span added up."""
        return round(sum(take.span_seconds for take in self.sections), 3)

    def of(self, section: int) -> Take | None:
        """One section's row, or None when that section has no take."""
        return next((take for take in self.sections if take.section == section), None)

    @property
    def voiced(self) -> tuple[int, ...]:
        """Every section holding a take somebody paid for, which a placeholder run must not replace."""
        return tuple(take.section for take in self.sections if take.voiced)

    @property
    def starts(self) -> dict[int, float]:
        """Where each section begins in the joined narration, added up in the order the takes are joined."""
        at, out = 0.0, {}
        for take in self.sections:
            out[take.section] = round(at, 3)
            at += take.span_seconds
        return out

    def start(self, section: int) -> float | None:
        """Where a section begins in the joined narration, or None when it has no take."""
        return self.starts.get(section)

    def end(self, section: int) -> float | None:
        """Where a section ends in the joined narration, or None when it has no take."""
        take, start = self.of(section), self.start(section)
        return None if take is None or start is None else round(start + take.span_seconds, 3)

    def speech_end(self, section: int) -> float | None:
        """Where the last word of a section lands in the joined narration, or None when it says nothing."""
        take, start = self.of(section), self.start(section)
        if take is None or start is None or take.speech_end_seconds is None:
            return None
        return round(start + take.lead_seconds + take.speech_end_seconds, 3)


__all__ = [
    "PLACEHOLDER_PREFIX",
    "TAKE_DIGITS",
    "PlaceholderInputs",
    "Take",
    "TakeInputs",
    "Takes",
    "is_placeholder",
    "take_file",
]
