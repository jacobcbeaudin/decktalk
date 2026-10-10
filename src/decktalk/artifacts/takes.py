"""The take index, and the frozen inputs a take's name is taken over.

    takes/<digest>.<suffix>          one voiced take, named by the content that produced it, in the takes directory
    build/narrate/<digest>.mp3       one placeholder, which DeckTalk makes again for nothing
    build/narrate/takes.json         which section plays which take, and the clock the join makes

The suffix is the one the voice's adapter declares for its output format, so a take asked for in
`mp3_44100_128` is `<digest>.mp3`, and a placeholder, which DeckTalk writes itself, is always `.mp3`.

A voiced take sits in the takes directory, `takes/` unless `[narration] takes_dir` names another, and
the take index stays under the build. The take index is a cache over the takes, which narrate builds again from them
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

The index has one row per turn, the stretch of a section one voice reads, and it is also the time
base. A section runs for its lead, then each turn's take up to where that take's sound ends, each
followed by the gap before the next turn, then its tail after the last, and the sections run in
section order, so where each one sits in the joined narration is arithmetic over the rows rather than
a second file that can disagree with them. Only a section's first turn carries its lead, and every
turn but its last carries the gap after it where its tail would be. Each of those numbers is a pure
function of the take's own bytes and its own section's settings and script, so a section lands the
same way whether this run voiced its take or found it on disk, and a section of one turn lands
exactly as one take always has.

Each row also says who timed its take's words, `timed_by`: the provider, an aligner, whose kept words
the row names by `alignment`, or DeckTalk, on a placeholder or on a take no aligner could time. It
keeps the model the voice served the take with, when the voice named one.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from enum import Enum
from typing import Any, ClassVar, NamedTuple

from pydantic import Field, model_validator

from decktalk.artifacts.stored import Stored
from decktalk.errors import InputError
from decktalk.findings import Model
from decktalk.results import SectionKey, SectionNumber
from decktalk.settings import VOICE_NAME

TAKE_DIGITS = 16
"""How much of the sha256 names a take, which is far more than enough that two never collide."""

PLACEHOLDER_PREFIX = "placeholder-"
"""What marks the digest of a placeholder, so a placeholder and a voiced take never share a file name."""

PLACEHOLDER_SUFFIX = ".mp3"
"""What a placeholder is written under, which is the mp3 of click audio DeckTalk writes itself."""

VOICED_SUFFIX = ".mp3"
"""Truth: what every shipped adapter and a host's provider write a voiced take as, which a take whose words file
records no suffix is looked for under."""

FIELD_SEPARATOR = "\n"
"""What joins the fields of a digest's payload, which no field but the last may hold."""

PLACEHOLDER_DIGITS = 10
"""How much of the sha256 names a placeholder take, which is regenerated rather than bought."""

TAKE_DIGEST = rf"^(?:[0-9a-f]{{{TAKE_DIGITS}}}|{PLACEHOLDER_PREFIX}[0-9a-f]{{{PLACEHOLDER_DIGITS}}})$"
"""Every digest a take may be named by, which is the head of a sha256 in hex and nothing else.

A take's digest becomes a file name under the takes directory or build/narrate, so an index a user
supplied could otherwise name `../` and have the narration read a file from anywhere on the machine.
"""


ALIGNMENT_KEY = rf"^[0-9a-f]{{{TAKE_DIGITS}}}-[0-9a-f]{{{TAKE_DIGITS}}}$"
"""Derived: the key aligned words are kept under, the take's match and the aligner's identity, each a sha256 head.

It names a file in the takes directory's `aligned/` folder, so an index a user supplied could otherwise
name `../` and have a section read a file from anywhere on the machine.
"""


class TimedBy(Enum):
    """Who timed a take's words, which is what its words file is and what a reader of them can trust."""

    PROVIDER = "provider"
    """The speech provider sent the times back with the take."""
    ALIGNED = "aligned"
    """An aligner measured them in the take's audio."""
    ESTIMATED = "estimated"
    """DeckTalk spread them over the take from the script's pace, on a placeholder or a take no aligner timed."""


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

    provider: str = Field(description="The speech provider that spoke this take, as `[voices.NAME] provider` names it.")
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
                    hint="Take the newline out of the voice or provider setting that names it, then run it again.",
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

    Nothing is bought for one, so its digest exists only to let an unchanged section be skipped, and
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


class TurnKey(NamedTuple):
    """One turn of one section, which is how the take index, a plan and a run's ledger name it."""

    section: int
    turn: int


class Take(Model):
    """One turn's take: the files it names, what it cost to make, and where it lands."""

    section: SectionNumber
    turn: int = Field(ge=1, description="The turn of its section this take reads, counted from one.")
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
    lead_seconds: float = Field(
        0.0, ge=0, description="Silence placed before the take, which is not in the file, and only a first turn has."
    )
    tail_seconds: float = Field(
        0.0,
        ge=0,
        description="Silence placed after the take's sound, also not in the file: the section's tail after its last "
        "turn, and the gap before the next turn after any other.",
    )
    spoken: str = Field(description="The words the voice says, with the script's punctuation, which captions borrow.")
    voice: str = Field(
        pattern=VOICE_NAME,
        description="The voice that reads this take, by its name in decktalk.toml, a label that is never part of the "
        "take's digest.",
    )
    timed_by: TimedBy = Field(
        description="Who timed this take's words: its provider, an aligner, or DeckTalk from the script's pace."
    )
    alignment: str | None = Field(
        pattern=ALIGNMENT_KEY,
        description="The key of the aligned words this take plays, in the takes directory's `aligned/` folder, or null "
        "when no aligner timed it.",
    )
    model_digest: str | None = Field(
        description="The digest of the model the voice served this take with, when it named one."
    )

    @model_validator(mode="after")
    def _timing_agrees_with_itself(self) -> Take:
        """Refuse a row that names an alignment it is not timed by, or is timed by an aligner and names none."""
        if (self.alignment is not None) != (self.timed_by is TimedBy.ALIGNED):
            raise ValueError(
                f"the row of section {self.section} is timed by {self.timed_by.value} and names the alignment "
                f"{self.alignment}, and only a row an aligner timed names one"
            )
        return self

    @property
    def sound_seconds(self) -> float:
        """How much of the take plays, which is up to its sound end, or all of it when that was not measured."""
        return self.duration_seconds if self.sound_end_seconds is None else self.sound_end_seconds

    @property
    def span_seconds(self) -> float:
        """How long the turn runs in the joined narration: its lead, its take to its last sound, its tail or gap."""
        return round(self.lead_seconds + self.sound_seconds + self.tail_seconds, 3)

    @property
    def turn_key(self) -> TurnKey:
        """The turn this row reads, by its section and its number."""
        return TurnKey(self.section, self.turn)


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
    model: str = Field(description="The model the default voice of the last run that wrote this index asked for.")
    output_format: str = Field(
        description="The audio format the default voice of the last run that wrote this index asked for."
    )
    sections: tuple[Take, ...] = Field(
        (), description="One row per turn of every narrated section, in section order and turn order."
    )

    @model_validator(mode="after")
    def _turns_ascend_from_one(self) -> Takes:
        """Refuse rows out of section and turn order, a section whose turns do not count 1, 2, 3, and a lead on a
        turn other than a section's first."""
        keys = [take.turn_key for take in self.sections]
        if keys != sorted(set(keys)):
            raise ValueError("the take index holds its rows out of section and turn order, or one turn twice")
        for take in self.sections:
            if take.turn > 1 and TurnKey(take.section, take.turn - 1) not in keys:
                raise ValueError(f"section {take.section}'s turns do not count from one without a gap")
            if take.turn > 1 and take.lead_seconds:
                raise ValueError(
                    f"section {take.section} has a lead on turn {take.turn}, and only its first turn has a lead"
                )
        return self

    @model_validator(mode="after")
    def _rows_agree_with_their_digests(self) -> Takes:
        """Refuse a row whose `voiced` or `timed_by` disagrees with its digest, so each is one fact.

        A placeholder's words are always the ones DeckTalk estimated, so its row is timed by DeckTalk.
        """
        for take in self.sections:
            if take.voiced == is_placeholder(take.digest):
                spoken = "was" if take.voiced else "was not"
                raise ValueError(
                    f"the row of section {take.section} says its take {take.digest} {spoken} spoken by a provider, "
                    "and its digest says otherwise"
                )
            if is_placeholder(take.digest) and take.timed_by is not TimedBy.ESTIMATED:
                raise ValueError(
                    f"the row of section {take.section} plays the placeholder {take.digest}, whose words DeckTalk "
                    f"estimated, and says they were timed by {take.timed_by.value}"
                )
        return self

    @property
    def estimated(self) -> bool:
        """True when any row is a placeholder, whose click audio the loudness pass and the cue check treat apart."""
        return any(not take.voiced for take in self.sections)

    @property
    def total_seconds(self) -> float:
        """How long the joined narration runs, which is every turn's span added up."""
        return round(sum(take.span_seconds for take in self.sections), 3)

    def of(self, section: int) -> Take | None:
        """One section's first turn's row, or None when that section has no take."""
        return next((take for take in self.sections if take.section == section), None)

    def turns_of(self, section: int) -> tuple[Take, ...]:
        """Every turn's row of one section, in turn order, which is empty when that section has no take."""
        return tuple(take for take in self.sections if take.section == section)

    @property
    def numbers(self) -> tuple[int, ...]:
        """Every section with a row, in order."""
        return tuple(dict.fromkeys(take.section for take in self.sections))

    @property
    def voiced_sections(self) -> tuple[int, ...]:
        """Every section holding a voiced take of any of its turns."""
        return tuple(dict.fromkeys(take.section for take in self.sections if take.voiced))

    @property
    def voiced_turns(self) -> tuple[TurnKey, ...]:
        """Every turn holding a voiced take."""
        return tuple(take.turn_key for take in self.sections if take.voiced)

    def span_seconds(self, section: int) -> float:
        """How long one section runs in the joined narration: its lead, every turn with its gap, and its tail."""
        return round(sum(take.span_seconds for take in self.turns_of(section)), 3)

    @property
    def placed_turns(self) -> dict[TurnKey, Placed]:
        """Where each turn's take sits in the joined narration, added up in one pass in the order they are joined."""
        ats = itertools.accumulate((take.span_seconds for take in self.sections), initial=0.0)
        return {
            take.turn_key: Placed(
                start=round(at, 3),
                end=round(at + take.span_seconds, 3),
                speech_end=None
                if take.speech_end_seconds is None
                else round(at + take.lead_seconds + take.speech_end_seconds, 3),
            )
            for take, at in zip(self.sections, ats, strict=False)
        }

    @property
    def placed(self) -> dict[int, Placed]:
        """Where each section sits in the joined narration: its first turn's start, its last turn's end, and where
        its last turn's last word ends.

        A caller that walks the sections reads this once, so a film of many sections is placed in one
        pass rather than once per lookup.
        """
        turns = self.placed_turns
        out: dict[int, Placed] = {}
        for take in self.sections:
            at = turns[take.turn_key]
            first = out.get(take.section)
            start = at.start if first is None else first.start
            out[take.section] = Placed(start=start, end=at.end, speech_end=at.speech_end)
        return out


__all__ = [
    "ALIGNMENT_KEY",
    "PLACEHOLDER_PREFIX",
    "PLACEHOLDER_SUFFIX",
    "TAKE_DIGITS",
    "TAKE_DIGEST",
    "VOICED_SUFFIX",
    "PlaceholderInputs",
    "Placed",
    "Take",
    "TakeInputs",
    "Takes",
    "TimedBy",
    "TurnKey",
    "is_placeholder",
    "take_file",
]
