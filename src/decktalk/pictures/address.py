"""Where a page section is opened: the address the recorder plays, the address of each frozen state of it.

A page section is opened on the local origin at its page, with its scene, the cues `cue` resolved, its
own params, the words its take speaks with their seconds and the turns that say them. The recorder asks
the page to play from second zero on the stepped clock. A frozen state asks it for one slide with its
cues fired up to one of them instead, so the recorder's scene and clock keys are left off and the
freeze keys go on, and everything else is what the recorder passes, because a still that differed
from the film would not be a still of it.

`Freeze` names one frozen state, which is what a panel of a storyboard and each frame `check`
compares are.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from decktalk.artifacts import CueTimes
from decktalk.errors import InputError
from decktalk.inputs import Inputs, PageSection, RecordedSection, TurnWords
from decktalk.media.origin import page_url
from decktalk.page import LIST_SEPARATOR, SECOND_DIGITS, T0_SIGNAL, TIME_MARK, Q
from decktalk.results import Word

LABEL_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
"""Everything a frozen state's name may not carry into a file name, which becomes one underscore."""

CAPTION_BAND = "1"
"""What `?caption=` says to ask a page for its caption band alone."""


def as_query(params: dict[str, str]) -> dict[Q, str]:
    """A section's own params as contract keys, refusing a key no page reads.

    The query vocabulary has one home, so a param that names a key outside it is a project file
    asking the page for something it cannot answer, which is worth refusing at load rather than
    sending and watching nothing happen.
    """
    known = {key.value: key for key in Q}
    unknown = sorted(name for name in params if name not in known)
    if unknown:
        raise InputError(
            f"a section's params name {', '.join(unknown)}, which no DeckTalk page reads.",
            hint=f"The query keys a page reads are {', '.join(sorted(known))}.",
        )
    return {known[name]: value for name, value in params.items()}


def scene_params(section: PageSection, cue_times: CueTimes | None) -> dict[Q, str]:
    """The section's own query parameters, plus its resolved cues unless the section sets `cues` itself."""
    params = as_query(dict(section.params))
    if cue_times is not None and Q.CUES not in params:
        query = cue_times.query(section.number)
        if query:
            params[Q.CUES] = query
    return params


def words_param(words: tuple[Word, ...]) -> str | None:
    """A section's words as word@seconds pairs, in seconds after that section starts.

    The contract's list separator divides two pairs and its time mark divides a word from its
    second, so neither may appear inside a word that is written into the value. Each second is
    written to `SECOND_DIGITS`, as every word time is kept, so rounding never moves a word onto the
    second the next turn begins.
    """
    if not words:
        return None
    pairs = ((word.word.replace(LIST_SEPARATOR, "").replace(TIME_MARK, ""), max(0.0, word.start)) for word in words)
    return LIST_SEPARATOR.join(f"{said}{TIME_MARK}{start:.{SECOND_DIGITS}f}" for said, start in pairs)


def turns_param(turns: Sequence[TurnWords]) -> str | None:
    """A section's turns as voice@seconds pairs in turn order, each the second on the section clock the turn begins.

    The page numbers the turns by their place in the list, so a turn that says no word is still
    written, and a word belongs to the last turn that began at or before its own second. The second
    is written as a word's is, so the first word of a turn is never written before its turn begins, and
    a last word that starts before the next turn begins is never written at or after it.
    """
    if not turns:
        return None
    return LIST_SEPARATOR.join(f"{turn.voice}{TIME_MARK}{max(0.0, turn.start):.{SECOND_DIGITS}f}" for turn in turns)


def spoken_query(inputs: Inputs, section: RecordedSection) -> dict[Q, str]:
    """The section's spoken words with their seconds and the turns that say them, as the page reads them.

    The words are what a word-synced line is drawn on, and the turns are what `DeckTalk.words` reads
    the voice and the turn of each word from. A key with nothing to carry is left out.
    """
    found = {
        Q.WORDS: words_param(inputs.words(section.number).words),
        Q.TURNS: turns_param(inputs.turn_words(section.number)),
    }
    return {key: value for key, value in found.items() if value}


def scene_url(inputs: Inputs, section: PageSection, params: dict[Q, str]) -> str:
    """The local origin URL the recorder and the frozen frames open for one page section."""
    page = inputs.path(section.page)
    if not page.exists():
        raise InputError(
            f"section {section.number} plays {section.page}, which is not there.",
            hint="Write the page, or point the section at the file you meant.",
        )
    query: dict[Q, str] = {Q.SCENE: section.scene, **spoken_query(inputs, section), **params}
    query[Q.T0] = T0_SIGNAL
    return page_url(section.page, query)


def section_address(section: PageSection) -> str:
    """The address a section's scene and its own params name, with nothing a run resolves, which its key reads."""
    return page_url(section.page, {Q.SCENE: section.scene, **as_query(dict(section.params))})


@dataclass(frozen=True)
class Freeze:
    """One frozen state of a page: a slide, with its cues fired up to `cue`, or before `before`, or none.

    A frozen frame shows every reveal in its end state and nothing in motion, which is what makes two
    of them comparable and what makes one of them worth looking at.
    """

    slide: str
    cue: str | None = None
    before: str | None = None

    @classmethod
    def state(cls, slide: str, fired: Sequence[str], cue_ids: Sequence[str]) -> Freeze:
        """The state `slide` is in once `fired` has fired, which is before its first cue when nothing has."""
        if fired:
            return cls(slide, cue=fired[-1])
        return cls(slide, before=cue_ids[0]) if cue_ids else cls(slide)

    def query(self) -> dict[Q, str]:
        """What this state asks the page for, in the contract's own query keys and no others."""
        if self.cue is not None:
            return {Q.FREEZE: self.slide, Q.AFTER: self.cue}
        if self.before is not None:
            return {Q.FREEZE: self.slide, Q.BEFORE: self.before}
        return {Q.FREEZE: self.slide}

    @property
    def label(self) -> str:
        """This state as one file name, such as `freeze-4.1-after-4.1_expand`."""
        text = "-".join(part for key, value in self.query().items() for part in (key.value, value))
        return LABEL_SAFE.sub("_", text)


def freeze_query(section: PageSection, freeze: Freeze, spoken: Mapping[Q, str]) -> dict[Q, str]:
    """The query of one frozen state of one section, with the section's own params and its own words and turns."""
    return {**spoken, **as_query(section.freeze_params), **freeze.query()}


def freeze_url(section: PageSection, freeze: Freeze, spoken: Mapping[Q, str]) -> str:
    """The URL of one frozen state of one section, which is the name the still drawn at it is kept under."""
    return page_url(section.page, freeze_query(section, freeze, spoken))


__all__ = [
    "CAPTION_BAND",
    "Freeze",
    "as_query",
    "freeze_query",
    "freeze_url",
    "scene_params",
    "scene_url",
    "section_address",
    "spoken_query",
    "turns_param",
    "words_param",
]
