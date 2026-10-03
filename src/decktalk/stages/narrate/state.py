"""The take state: what the disk holds for each spoken section, and what one narrate run does about it.

A take is named by its digest, which is taken over the section's text, the voice, the model and the
voice settings, so whether a section's take is current is a question about which digests some place
holds. This module asks it once per reading. It reads the take index, the script and the settings,
asks `TakePlaces.find` once per distinct digest, and judges every spoken section of the script
against the rest, so one section reads the same whichever run asks. It sends nothing and writes
nothing.

Each section's state carries the clause that says why, which is the clause a `TAKE_MISSING` finding
says after "because" and the one `status` reports beside the state. narrate plans from the same
reading, check prices it and resolves its cues against the words it plans, and status and watch
report it, so the four of them never disagree about a take.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from decktalk.artifacts import EstimatedWords, ProviderWords, Take, on_section_clock
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.script import ScriptSection
from decktalk.inputs.take_places import TakeFiles
from decktalk.results import Cost, CostState, SectionTake, TakeOutcome, TakeState, counted
from decktalk.settings import PROJECT_FILE
from decktalk.speech import SpeechRequest, canonical_text, output_of
from decktalk.stages import voice_model
from decktalk.stages.cost import Buy, cost_of, is_free
from decktalk.stages.narrate.plan import (
    VOICE_ID_VARIABLE,
    named_voice,
    placeholder_inputs,
    take_identity,
    take_inputs,
)
from decktalk.stages.narrate.takes import estimated_words

CHANGED = "the text, the voice, the model or the voice settings changed"
"""Why a section's words are on disk under another digest, which only one of a take's inputs moving explains."""

NO_VOICED_TAKE = "it has no voiced take yet"
"""Why a section plays a placeholder with nothing changed, worded as a clause so the plan and the `TAKE_MISSING` finding
read it alike, and true of a section that has a placeholder already as much as of one that has nothing."""

SHARED = "another section of this run voices these words"
"""Why a section sends nothing for words an earlier section of the same run already plans to make."""

HELD = "a voiced take of the current text, voice, model and voice settings is held"
"""Why a section is voiced, when the take index names the take it holds."""

UNINDEXED = (
    "a voiced take of the current text, voice, model and voice settings is held, "
    "and the take index does not name it yet"
)
"""Why a section is voiced and a run that buys nothing would still rewrite its row of the take index."""

FILES_GONE = "the take or its words file is missing"
"""Why a section has no take when the take index names one that no place holds."""

WITHOUT_A_VOICE = f"no take on disk can be matched to it until [voice] id or {VOICE_ID_VARIABLE} names the voice"
"""Why a voiced take of a section's text is unchecked, which only naming the voice resolves."""

REPLACED = "this run was told to replace its voiced take"
"""Why a section's voiced take is made again or stood in for, held or damaged."""


@dataclass(frozen=True)
class SectionTakeState:
    """One spoken section's take state, with the clause that says why. Not a result row."""

    section: ScriptSection
    state: TakeState
    reason: str
    """The clause a `TAKE_MISSING` finding says after "because". A voiced section says `HELD` or `UNINDEXED`."""
    digest: str | None
    """The voiced take the current inputs name, which the next voiced run plays, or None when no voice is named."""
    takes_dir_gone: bool
    """Its index row was voiced, no place holds that take, and the takes directory holds none of the index's
    voiced takes, which is what a renamed or missing folder looks like. The state is then MISSING or
    PLACEHOLDER and the reason says the folder moved."""


@dataclass(frozen=True)
class TakePlan:
    """What one narrate run does with one section, and the take it then plays."""

    section: ScriptSection
    outcome: TakeOutcome
    """VOICED sends `request`, KEPT plays what is held or what an earlier section makes, PLACEHOLDER makes one."""
    digest: str | None
    """The take this section plays after the run: the voiced digest, the matched row's digest for a kept
    UNCHECKED section, the placeholder's, or None for a section a voiced run would send with no voice named."""
    reason: str
    """`SHARED` on a section kept for words an earlier section's VOICED plan sends, `REPLACED` on a held voiced
    take under `replace_voiced`, else the take state's reason."""
    request: SpeechRequest | None
    """What a voiced run sends, carrying the selected sections either side for prosody, and None unless VOICED."""
    buy: Buy | None = None
    """What sending `request` buys, counted every way a voice could bill it, and None unless VOICED. It is not
    certain for an UNCHECKED section, whose take may already be on disk."""

    @property
    def characters_sent(self) -> int:
        """How many characters of script this section would send, which a per-character bill counts."""
        return len(canonical_text(self.section.pieces))


@dataclass(frozen=True)
class NarratePlan:
    """Every selected section's plan for one narrate run, in script order, and what the run costs."""

    takes: tuple[TakePlan, ...]
    voiced: bool
    """The run calls the provider for any take it lacks: it may spend, or its voice bills nothing and is
    named. It may voice no section, when every take is held."""
    cost: Cost
    """Priced at the bill `[voice] provider` declares, with an UNCHECKED section counted into the ceiling alone."""


class _Reading:
    """One read of the take index, the script and the settings, with `TakePlaces.find` asked once per digest."""

    def __init__(
        self, inputs: Inputs, *, spoken: Sequence[ScriptSection], rows: Mapping[int, Take], replace_voiced: bool
    ) -> None:
        self.inputs = inputs
        self.replace_voiced = replace_voiced
        self.spoken = tuple(spoken)
        self.rows = dict(rows)
        self.voice = named_voice(inputs)
        self.model = voice_model(inputs)
        self.matched: dict[int, str] = {}
        """The digest of the voiced row each UNCHECKED section matched by its text."""
        self.stand_ins: dict[int, str] = {}
        """The placeholder digest of each judged section."""
        self.places = inputs.take_places
        self._files: dict[str, TakeFiles] = {}
        played = {row.digest for row in self.rows.values() if row.voiced}
        moved = bool(played) and not any(self.files(digest).in_takes_directory for digest in played)
        self.unheld = sum(not self.held(digest) for digest in played) if moved else 0
        """How many of the index's voiced takes no place holds, when the takes directory holds none of them."""

    @classmethod
    def of(cls, inputs: Inputs, *, replace_voiced: bool) -> _Reading:
        """The script's spoken sections and the take index, each read once."""
        index = inputs.takes()
        rows = {row.section: row for row in index.sections} if index is not None else {}
        return cls(inputs, spoken=inputs.spoken(), rows=rows, replace_voiced=replace_voiced)

    def files(self, digest: str) -> TakeFiles:
        """Where this take's files are, found once per reading."""
        if digest not in self._files:
            self._files[digest] = self.places.find(digest)
        return self._files[digest]

    def held(self, digest: str) -> bool:
        """True when some place holds a good copy of this take and its words file."""
        return self.files(digest).held

    def judge(self, section: ScriptSection) -> SectionTakeState:
        """One section's take state, against every other section of the script."""
        number = section.number
        self.stand_ins[number] = placeholder_inputs(self.inputs, section).digest
        here = self.rows.get(number)
        elsewhere = {other.spoken for other in self.spoken if other.number != number}
        digest = self.digest_of(section) if self.voice else None
        if digest is not None:
            state, reason = self._named(section, digest, here, elsewhere)
        else:
            state, reason = self._unnamed(section, here, elsewhere)
        gone = (
            self.unheld > 0
            and state in (TakeState.MISSING, TakeState.PLACEHOLDER)
            and reason != REPLACED
            and here is not None
            and here.voiced
            and not self.held(here.digest)
        )
        return SectionTakeState(section, state, _moved(self.inputs, self.unheld) if gone else reason, digest, gone)

    def digest_of(self, section: ScriptSection) -> str:
        """The voiced take this section's current text, voice, model and voice settings name."""
        provider = self.inputs.settings.voice.provider
        return take_inputs(self.inputs, section, provider=provider, voice_id=self.voice, model=self.model).digest

    def _named(
        self, section: ScriptSection, digest: str, here: Take | None, elsewhere: set[str]
    ) -> tuple[TakeState, str]:
        """The state of a section whose voiced take the named voice gives a digest."""
        if self.held(digest):
            return TakeState.VOICED, HELD if here is not None and here.digest == digest else UNINDEXED
        refused = self.files(digest).refusal(section.number)
        if refused is not None:
            if self.replace_voiced:
                return TakeState.MISSING, REPLACED
            raise refused
        older = [row for row in self.rows.values() if row.voiced and row.spoken == section.spoken]
        if here is not None and here.voiced and here.spoken not in elsewhere:
            older.append(here)
        if any(self.held(row.digest) for row in older):
            return TakeState.STALE, CHANGED
        if self.held(self.stand_ins[section.number]):
            return TakeState.PLACEHOLDER, NO_VOICED_TAKE
        named = older or any(row.digest == digest for row in self.rows.values())
        return TakeState.MISSING, FILES_GONE if named else NO_VOICED_TAKE

    def _unnamed(self, section: ScriptSection, here: Take | None, elsewhere: set[str]) -> tuple[TakeState, str]:
        """The state of a section with no voice named, so its voiced take is matched by its text alone."""
        number = section.number
        same = sorted(
            (row for row in self.rows.values() if row.voiced and row.spoken == section.spoken),
            key=lambda row: row.section != number,
        )
        found = next((row for row in same if self.held(row.digest)), None)
        if found is not None:
            self.matched[number] = found.digest
            return TakeState.UNCHECKED, WITHOUT_A_VOICE
        for row in same:
            refused = self.files(row.digest).refusal(number)
            if refused is not None and self.replace_voiced:
                return TakeState.MISSING, REPLACED
            if refused is not None:
                raise _unnamed_refusal(refused)
        older = here is not None and here.voiced and here.spoken not in elsewhere
        if here is not None and older and self.held(here.digest):
            return TakeState.STALE, CHANGED
        if self.held(self.stand_ins[number]):
            return TakeState.PLACEHOLDER, NO_VOICED_TAKE
        return TakeState.MISSING, FILES_GONE if older else NO_VOICED_TAKE


def _unnamed_refusal(refused: InputError) -> InputError:
    """A damaged take's refusal when no voice is named, whose hint names the voice before anything is bought."""
    hint = refused.hint or ""
    return InputError(
        str(refused),
        hint=(
            f"Name the voice with [voice] id in {PROJECT_FILE} or {VOICE_ID_VARIABLE} first. "
            f"{hint[:1].upper()}{hint[1:]}"
        ),
        location=refused.location,
    )


def _moved(inputs: Inputs, count: int) -> str:
    """The clause for a takes directory that holds none of the takes the project played before."""
    takes = inputs.relative(inputs.workspace.takes).as_posix()
    return (
        f"the takes directory {takes} holds none of the {counted(count, 'take')} this project played before, "
        "which is what a renamed or missing folder looks like"
    )


class TakeStates(Mapping[int, SectionTakeState]):
    """The take state of every selected spoken section, keyed by section number in script order, read once.

    The take index, the script and the settings are read when this is made and never again, and
    `TakePlaces.find` is asked once per distinct digest. Every spoken section of the script is
    judged against the rest, so a section reads the same whichever run asks, and the selection
    chooses which are returned, planned and refused. It sends nothing and writes nothing.
    """

    def __init__(self, inputs: Inputs, states: Sequence[SectionTakeState], reading: _Reading) -> None:
        self._inputs = inputs
        self._states = {state.section.number: state for state in states}
        self._reading = reading

    def __getitem__(self, number: int) -> SectionTakeState:
        return self._states[number]

    def __iter__(self) -> Iterator[int]:
        return iter(self._states)

    def __len__(self) -> int:
        return len(self._states)

    @property
    def settled(self) -> bool:
        """True when a run that buys nothing would change no take: every selected section's
        `plan(spend=False)` outcome is KEPT and the take index names the take it keeps."""
        rows = self._reading.rows
        return all(
            plan.outcome is TakeOutcome.KEPT
            and (row := rows.get(plan.section.number)) is not None
            and row.digest == plan.digest
            for plan in self.plan(spend=False).takes
        )

    def planned_words(self, number: int) -> ProviderWords | EstimatedWords:
        """The words section `number` will have after a voiced run, counted from its start.

        They are the held take's own words for a voiced section, the matched row's take's own words for
        an unchecked one, and estimated words at the placeholder pace for any other. The kind is the one
        read from disk, and never a placeholder's words passed off as a provider's.
        """
        state = self._states[number]
        section = state.section
        lead = self._inputs.lead_seconds(number)
        digest = state.digest if state.state is TakeState.VOICED else self._reading.matched.get(number)
        if digest is not None:
            found = self._inputs.take_words(digest)
            if isinstance(found, ProviderWords):
                return on_section_clock(found, lead)
        length = section.placeholder_seconds(self._inputs.settings.narration)
        return on_section_clock(EstimatedWords(words=tuple(estimated_words(section, length))), lead)

    def plan(self, *, spend: bool, force: bool = False) -> NarratePlan:
        """What one narrate run does with each selected section, sending nothing and writing nothing.

        A run that voices sends every section whose voiced take is not held. A run that does not
        voice keeps every held take, keeps each UNCHECKED section under its matched row's digest, and
        plays a placeholder for the rest. Under `replace_voiced` it plays a placeholder with `REPLACED`
        for every selected voiced take, held or damaged. Two sections share words when their canonical
        text is equal, with or without a voice named, and the second is kept for the first. After the
        selected sections come the unselected ones whose index row names a take this run voices, each
        KEPT with `SHARED`, so their rows are placed on the new take. `force` makes placeholders again,
        never touches a voiced take, and is ignored by a run that voices. Whether the run voices is
        read from `spend` and the voice's bill: a free named voice always voices.
        """
        inputs = self._inputs
        voiced = spend or (is_free(inputs) and bool(self._reading.voice))
        selected = [state.section for state in self._states.values()]
        requests = self._requests(selected) if voiced else {}
        plans: list[TakePlan] = []
        first: dict[str, TakePlan] = {}
        for section in selected:
            state = self._states[section.number]
            text = canonical_text(section.pieces)
            earlier = first.get(text)
            if earlier is not None:
                why = SHARED if earlier.outcome is TakeOutcome.VOICED else state.reason
                plans.append(TakePlan(section, TakeOutcome.KEPT, earlier.digest, why, None))
                continue
            made = self._voicing(state, requests) if voiced else self._standing_in(state, force=force)
            first[text] = made
            plans.append(made)
        plans += self._replaced(plans)
        return NarratePlan(takes=tuple(plans), voiced=voiced, cost=cost_of(inputs, self._buys(plans)))

    def _replacing(self, state: SectionTakeState) -> bool:
        """Whether this run was told to replace this section's held voiced take."""
        return self._reading.replace_voiced and state.state is TakeState.VOICED

    def _voicing(self, state: SectionTakeState, requests: Mapping[int, SpeechRequest]) -> TakePlan:
        """One section's plan in a run that voices: kept when its voiced take is held, else sent."""
        section = state.section
        if state.state is TakeState.VOICED and not self._replacing(state):
            return TakePlan(section, TakeOutcome.KEPT, state.digest, state.reason, None)
        reason = REPLACED if self._replacing(state) else state.reason
        buy = Buy(
            characters=len(canonical_text(section.pieces)),
            seconds=section.estimated_seconds(self._inputs.settings.narration),
            sections=(section.number,),
            certain=state.state is not TakeState.UNCHECKED,
        )
        return TakePlan(section, TakeOutcome.VOICED, state.digest, reason, requests[section.number], buy)

    def _standing_in(self, state: SectionTakeState, *, force: bool) -> TakePlan:
        """One section's plan in a run that does not voice: its held voiced take, else a placeholder."""
        section = state.section
        number = section.number
        matched = self._reading.matched.get(number)
        if state.state is TakeState.VOICED and not self._replacing(state):
            return TakePlan(section, TakeOutcome.KEPT, state.digest, state.reason, None)
        if state.state is TakeState.UNCHECKED and matched is not None:
            return TakePlan(section, TakeOutcome.KEPT, matched, state.reason, None)
        stand_in = self._reading.stand_ins[number]
        kept = self._reading.held(stand_in) and not force
        reason = REPLACED if self._replacing(state) else state.reason
        return TakePlan(section, TakeOutcome.KEPT if kept else TakeOutcome.PLACEHOLDER, stand_in, reason, None)

    def _replaced(self, plans: Sequence[TakePlan]) -> list[TakePlan]:
        """The unselected sections whose index row names a take these plans voice, each kept on the new take."""
        reading = self._reading
        made = {plan.digest for plan in plans if plan.outcome is TakeOutcome.VOICED and plan.digest is not None}
        if not made:
            return []
        return [
            TakePlan(section, TakeOutcome.KEPT, row.digest, SHARED, None)
            for section in reading.spoken
            if section.number not in self._states
            and (row := reading.rows.get(section.number)) is not None
            and row.digest in made
        ]

    def _requests(self, selected: Sequence[ScriptSection]) -> dict[int, SpeechRequest]:
        """One request per selected section, each carrying the selected sections either side of it for prosody."""
        inputs = self._inputs
        settings = take_identity(inputs)
        output_format = output_of(inputs.settings, inputs.settings.voice.provider).format
        return {
            section.number: SpeechRequest(
                pieces=section.pieces,
                voice_id=self._reading.voice,
                model=self._reading.model,
                voice_settings=settings,
                output_format=output_format,
                previous_text=selected[at - 1].spoken if at else None,
                next_text=selected[at + 1].spoken if at + 1 < len(selected) else None,
            )
            for at, section in enumerate(selected)
        }

    def charged(self, plan: NarratePlan, made: Sequence[SectionTake]) -> Cost:
        """What a voiced run paid for: the takes it voiced, each counted as bought.

        A section the run kept, because another run voiced its take first, or one that played a
        placeholder, because its free voice did not answer, was not paid for, so it is not counted.
        """
        voiced = {row.section for row in made if row.outcome is TakeOutcome.VOICED}
        bought = [p for p in plan.takes if p.section.number in voiced]
        return cost_of(self._inputs, self._buys(bought), state=CostState.CHARGED)

    @staticmethod
    def _buys(plans: Sequence[TakePlan]) -> list[Buy]:
        """What the voiced plans buy, in plan order, one take each, an UNCHECKED section's counted as possible."""
        return [p.buy for p in plans if p.outcome is TakeOutcome.VOICED and p.buy is not None]


def take_states(
    inputs: Inputs, sections: Sequence[ScriptSection] | None = None, *, replace_voiced: bool = False
) -> TakeStates:
    """The take state of each of these spoken sections, or of every spoken section when None.

    An empty selection reads nothing: no script, no index and no take. The voice, the model and the
    provider are read from the settings. A selected voiced take whose every copy is damaged is refused
    with `TakeFiles.refusal`, unless `replace_voiced`, under which it reads MISSING with the reason
    `REPLACED`: a run that voices buys it again, and one that does not plays a placeholder.
    """
    if sections is not None and not sections:
        return TakeStates(inputs, (), _Reading(inputs, spoken=(), rows={}, replace_voiced=replace_voiced))
    reading = _Reading.of(inputs, replace_voiced=replace_voiced)
    wanted = {section.number for section in sections} if sections is not None else None
    chosen = [section for section in reading.spoken if wanted is None or section.number in wanted]
    return TakeStates(inputs, [reading.judge(section) for section in chosen], reading)


__all__ = [
    "CHANGED",
    "FILES_GONE",
    "HELD",
    "NO_VOICED_TAKE",
    "REPLACED",
    "SHARED",
    "UNINDEXED",
    "WITHOUT_A_VOICE",
    "NarratePlan",
    "SectionTakeState",
    "TakePlan",
    "TakeStates",
    "take_states",
]
