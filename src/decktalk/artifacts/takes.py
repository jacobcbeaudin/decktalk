"""The take index, and the frozen inputs a take's name is taken over.

    build/narrate/<digest>.<suffix>  one take, named by the content that produced it and by what it holds
    build/narrate/takes.json       which section plays which take, and the clock the join makes

The suffix is the one the voice's adapter declares for its output format, so a take asked for in
`mp3_44100_128` is `<digest>.mp3`, and a placeholder, which DeckTalk writes itself, is always `.mp3`.

A take sits in the project's `[narration] takes_dir` instead when it names one, and the index stays
under the build either way. The index is a cache over the takes, which narrate builds again from them
when it does not read, so a checkout that commits its takes never sees it change, and the takes are
what a project keeps.

A take is identified by its input digest and by nothing else, so the index maps a section to a piece
of content and never the other way round. Renumbering a section rewrites one row and moves no file,
and two sections with the same words share one take.

`TakeInputs` is the whole of what that digest is taken over, declared as a model so the set is frozen
by a shape rather than by a convention. Every byte of speech is paid for once, so adding a field or
changing the order here re-voices every project there is, which is what
`tests/contract/test_take_hash.py` holds against the digests of films that were really voiced. Every
field but the text refuses a newline and the text is last, so the payload, which joins the fields
with newlines, reads back as one set of inputs and no two sets share a digest.
The voice id is one of the inputs because two voices reading one sentence are two different takes,
and it is a published name rather than a secret, so it is written into the file a reader can see.

The index is also the time base. A section runs for its lead, then its take up to where the take's
sound ends, then its tail, and the sections run in section order, so where each one sits in the
joined narration is arithmetic over the rows rather than a second file that can disagree with them.
Each of those three numbers is a pure function of the take's own bytes and its own section's
settings, so a section lands the same way whether this run voiced its take or found it on disk.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any, ClassVar, NamedTuple

from pydantic import Field, model_validator

from decktalk.artifacts.stored import Stored
from decktalk.errors import InputError
from decktalk.findings import Model
from decktalk.results import SectionKey, SectionNumber

TAKE_DIGITS = 16
"""How much of the sha256 names a take, which is far more than enough that two never collide."""

PLACEHOLDER_PREFIX = "placeholder-"
"""What marks the digest of a placeholder, so a placeholder and a voiced take never share a file name."""

PLACEHOLDER_SUFFIX = ".mp3"
"""What a placeholder take is written under, which is the mp3 click track DeckTalk writes itself."""

FIELD_SEPARATOR = "\n"
"""What joins the fields of a digest's payload, which no field but the last may hold."""

PLACEHOLDER_DIGITS = 10
"""How much of the sha256 names a placeholder take, which is regenerated rather than bought."""

TAKE_DIGEST = rf"^(?:[0-9a-f]{{{TAKE_DIGITS}}}|{PLACEHOLDER_PREFIX}[0-9a-f]{{{PLACEHOLDER_DIGITS}}})$"
"""Every digest a take may be named by, which is the head of a sha256 in hex and nothing else.

A take's digest becomes a file name under the take directory, so an index a user supplied could
otherwise name `../` and have the narration read a file from anywhere on the machine.
"""


class _Digested(Model):
    """A frozen set of inputs whose digest is taken over its payload."""

    @property
    def payload(self) -> str:
        """The bytes the digest is taken of, which is every field in declaration order, newline separated."""
        return FIELD_SEPARATOR.join(str(getattr(self, name)) for name in type(self).model_fields)


class TakeInputs(_Digested):
    """Everything that decides what a voiced take sounds like, which is everything its name is taken over.

    The payload is the fields below joined by newlines, in the order they are declared. A reader
    who wants to know why a take was voiced again compares two of these rather than guessing.
    """

    provider: str = Field(description="The speech provider that spoke this take, as `[voice] provider` names it.")
    voice: str = Field(description="The provider's id for the voice, which is a published name and not a secret.")
    model: str = Field(description="The provider's model id, which changes how the same words are read.")
    output_format: str = Field(description="The audio format asked for, such as mp3_44100_128.")
    settings: str = Field(description="The provider's voice settings as compact JSON with its keys sorted.")
    text: str = Field(description="The exact text sent to the voice, with its pause tags.")

    @model_validator(mode="after")
    def _one_line_each(self) -> TakeInputs:
        """Refuse a newline in any field but the text, before anything is sent, so the payload stays one reading.

        The text is the last field and may hold any newline. A newline anywhere before it would let
        two different sets of inputs join to the same payload and so share one take.
        """
        for name in type(self).model_fields:
            value = getattr(self, name)
            if name != "text" and FIELD_SEPARATOR in value:
                raise InputError(
                    f"the take's {name.replace('_', ' ')} {value!r} holds a newline, and no field a take is named "
                    "by may hold one but its text, so nothing was sent.",
                    hint="Take the newline out of the [voice] or provider setting that names it, then run it again.",
                )
        return self

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
    def digest(self) -> str:
        """The take's name, which is the head of the sha256 of the payload."""
        return hashlib.sha256(self.payload.encode("utf-8")).hexdigest()[:TAKE_DIGITS]


class PlaceholderInputs(_Digested):
    """Everything that decides what a placeholder take sounds like, which is its length and its clicks.

    No credit is spent on one, so its digest exists only to let an unchanged section be skipped, and
    its prefix keeps it out of the voiced takes a run must never overwrite.
    """

    words_per_minute: float = Field(gt=0, description="The pace the placeholder is sized at.")
    beat_seconds: float = Field(ge=0, description="How long a declared pause is held in a placeholder.")
    text: str = Field(description="The spoken text this placeholder stands in for.")

    @property
    def digest(self) -> str:
        """The placeholder's name, which is marked so no voiced take can ever be mistaken for one."""
        return PLACEHOLDER_PREFIX + hashlib.sha256(self.payload.encode("utf-8")).hexdigest()[:PLACEHOLDER_DIGITS]


def take_file(digest: str, suffix: str) -> str:
    """The name of the audio file of the take with this digest, under the suffix of what it holds.

    A placeholder is always the mp3 DeckTalk writes, whatever the voice's own format.
    """
    return f"{digest}{PLACEHOLDER_SUFFIX if is_placeholder(digest) else suffix}"


def is_placeholder(digest: str) -> bool:
    """True when this digest names a placeholder rather than a voiced take."""
    return digest.startswith(PLACEHOLDER_PREFIX)


class Take(Model):
    """One section's take: the files it names, what it cost to make, and where it lands."""

    section: SectionNumber
    key: SectionKey
    chapter: str = Field(description="The section's title, which the film's chapter marker carries.")
    digest: str = Field(
        pattern=TAKE_DIGEST,
        description="The digest of the inputs this take was made from, which names its files, in lowercase hex.",
    )
    voiced: bool = Field(description="True when a provider spoke this take, false on a placeholder.")
    word_count: int = Field(ge=0, description="How many words this take speaks.")
    characters: int = Field(
        ge=0, description="How many characters were sent to the voice, which a per-character bill counts."
    )
    estimated_seconds: float = Field(ge=0, description="How long the script said this take would run.")
    duration_seconds: float = Field(ge=0, description="How long the audio file runs, measured from its own bytes.")
    speech_end_seconds: float | None = Field(None, ge=0, description="Where the last word ends, or null.")
    sound_end_seconds: float | None = Field(None, ge=0, description="Where the take's sound ends, or null.")
    lead_seconds: float = Field(0.0, ge=0, description="Silence placed before the take, which is not in the file.")
    tail_seconds: float = Field(0.0, ge=0, description="Silence placed after the take's sound, also not in the file.")
    spoken: str = Field(description="The words the voice says, with the script's punctuation, which captions borrow.")

    @property
    def sound_seconds(self) -> float:
        """How much of the take plays, which is up to its sound end, or all of it when that was not measured."""
        return self.duration_seconds if self.sound_end_seconds is None else self.sound_end_seconds

    @property
    def span_seconds(self) -> float:
        """How long the section runs in the joined narration: its lead, its take to its last sound, its tail."""
        return round(self.lead_seconds + self.sound_seconds + self.tail_seconds, 3)


class Placed(NamedTuple):
    """Where one take sits in the joined narration: where it starts, where it ends, and where its last word ends."""

    start: float
    end: float
    speech_end: float | None


class Takes(Stored):
    """The take index: what was voiced, with what, and the clock the joined narration runs on.

    Nothing here is stored that the rows already say. How long the narration runs and whether any of
    it is a placeholder are read off the rows, so the file cannot hold a total that disagrees with
    what it lists. It is a cache rather than a paid record: every row is read again off the script,
    the settings and the take files its digest names, so narrate builds an index that does not read
    again and buys nothing for it. The paid records are the take audio and the words its voice sent.
    """

    label: ClassVar[str] = "the take index"

    script: str = Field(description="The script these takes were made from, project-relative.")
    model: str = Field(description="The provider model the last run that wrote this index asked for.")
    output_format: str = Field(description="The audio format the last run that wrote this index asked for.")
    sections: tuple[Take, ...] = Field((), description="One row per narrated section, in section order.")

    @model_validator(mode="after")
    def _rows_agree_with_their_digests(self) -> Takes:
        """Refuse a row whose `voiced` disagrees with its digest, so whether a take was voiced is one fact."""
        for take in self.sections:
            if take.voiced == is_placeholder(take.digest):
                spoken = "was" if take.voiced else "was not"
                raise ValueError(
                    f"the row of section {take.section} says its take {take.digest} {spoken} spoken by a provider, "
                    "and its digest says otherwise"
                )
        return self

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
    def voiced_sections(self) -> tuple[int, ...]:
        """Every section holding a voiced take."""
        return tuple(take.section for take in self.sections if take.voiced)

    @property
    def placed(self) -> dict[int, Placed]:
        """Where each section's take sits in the joined narration, added up in one pass in the order they are joined.

        A caller that walks the sections reads this once, so a film of many sections is placed in one
        pass rather than once per lookup.
        """
        ats = itertools.accumulate((take.span_seconds for take in self.sections), initial=0.0)
        return {
            take.section: Placed(
                start=round(at, 3),
                end=round(at + take.span_seconds, 3),
                speech_end=None
                if take.speech_end_seconds is None
                else round(at + take.lead_seconds + take.speech_end_seconds, 3),
            )
            for take, at in zip(self.sections, ats, strict=False)
        }


__all__ = [
    "PLACEHOLDER_PREFIX",
    "PLACEHOLDER_SUFFIX",
    "TAKE_DIGITS",
    "TAKE_DIGEST",
    "PlaceholderInputs",
    "Placed",
    "Take",
    "TakeInputs",
    "Takes",
    "is_placeholder",
    "take_file",
]
