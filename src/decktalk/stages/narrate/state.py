"""The take state: what the disk holds for each turn of each spoken section, and what one narrate run does about it.

A take is named by its digest, which is taken over a turn's text and the values of the voice that
reads it: its provider, its id, its model and its settings, so whether a turn's take is current is a
question about which digests some place holds. This module asks it once per reading. It reads the
take index, the script and the settings, asks `TakePlaces.find` once per distinct digest, and judges
every turn of every spoken section of the script against the rest, so one turn reads the same
whichever run asks. A section's state is the worst of its turns'. It sends nothing and writes
nothing.

Each state carries the clause that says why, which is the clause a `TAKE_MISSING` finding says after
"because" and the one `status` reports beside the state. narrate plans from the same reading, check
prices it and resolves its cues against the words it plans, and status and watch report it, so the
four of them never disagree about a take.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace

from decktalk.artifacts import (
    EstimatedWords,
    ProviderWords,
    Take,
    Takes,
    TurnKey,
    UnreadableCache,
    UnreadablePaidRecord,
    Words,
    on_section_clock,
)
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.inputs.script import ScriptSection, Turn, estimated_words, spoken_words
from decktalk.inputs.take_places import TakeFiles
from decktalk.results import Cost, CostState, TakeOutcome, TakeState, counted
from decktalk.settings import PROJECT_FILE
from decktalk.speech import SpeechRequest, VoiceInForce, canonical_text
from decktalk.stages.cost import Buy, cost_of
from decktalk.stages.narrate.align import kept_alignment
from decktalk.stages.narrate.plan import (
    id_variable,
    placeholder_inputs,
    take_inputs,
)
from decktalk.stages.narrate.takes import gap_after, sound_end_of

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


def without_a_voice(voice: VoiceInForce) -> str:
    """Why a voiced take of a section's text is unchecked, which only naming its voice resolves."""
    return (
        f"no take on disk can be matched to it until [voices.{voice.name}] id or {id_variable(voice.name)} names "
        "the voice"
    )


REPLACED = "this run was told to replace its voiced take"
"""Why a turn's voiced take is made again or stood in for, held or damaged."""


WORST_FIRST = (TakeState.MISSING, TakeState.PLACEHOLDER, TakeState.STALE, TakeState.UNCHECKED, TakeState.VOICED)
"""The take states from worst to best, which is the order a section's state is the worst of its turns' by."""


@dataclass(frozen=True)
class TurnTakeState:
    """One turn's take state, with the voice that reads it and the clause that says why. Not a result row."""

    turn: Turn
    voice: VoiceInForce
    state: TakeState
    reason: str
    """The clause a `TAKE_MISSING` finding says after "because". A voiced turn says `HELD` or `UNINDEXED`."""
    digest: str | None
    """The voiced take the current inputs name, which the next voiced run plays, or None when its voice has no id."""
    takes_dir_gone: bool
    """Its index row was voiced, no place holds that take, and the takes directory holds none of the index's
    voiced takes, which is what a renamed or missing folder looks like. The state is then MISSING or
    PLACEHOLDER and the reason says the folder moved."""


@dataclass(frozen=True)
class SectionTakeState:
    """One spoken section's take state, which is the worst of its turns', with the clause that says why.

    Not a result row.
    """

    section: ScriptSection
    turns: tuple[TurnTakeState, ...]

    @property
    def worst(self) -> TurnTakeState:
        """The turn whose state the section reports, which is the worst, the first of them on a tie."""
        return min(self.turns, key=lambda turn: WORST_FIRST.index(turn.state))

    @property
    def state(self) -> TakeState:
        return self.worst.state

    @property
    def reason(self) -> str:
        """The worst turn's clause, which for a section of several turns names that turn and its voice."""
        worst = self.worst
        if len(self.turns) == 1:
            return worst.reason
        return f"{worst.reason}, in turn {worst.turn.number}, which {worst.voice.name} reads"

    @property
    def digest(self) -> str | None:
        """The voiced take a section of one turn names, or None for a section of several or a voice with no id."""
        return self.turns[0].digest if len(self.turns) == 1 else None

    @property
    def takes_dir_gone(self) -> bool:
        """Whether any turn's take is missing because the takes directory moved."""
        return any(turn.takes_dir_gone for turn in self.turns)


@dataclass(frozen=True)
class TakePlan:
    """What one narrate run does with one turn of one section, and the take it then plays."""

    section: ScriptSection
    turn: Turn
    voice: VoiceInForce
    outcome: TakeOutcome
    """VOICED sends `request`, KEPT plays what is held or what an earlier turn makes, PLACEHOLDER makes one."""
    digest: str | None
    """The take this turn plays after the run: the voiced digest, the matched row's digest for a kept
    UNCHECKED turn, the placeholder's, or None for a turn a voiced run would send with no voice id."""
    reason: str
    """`SHARED` on a turn kept for a take another turn's VOICED plan sends, `REPLACED` on a held voiced
    take under `replace_voiced` or a voiced turn named to be made again, else the take state's reason."""
    request: SpeechRequest | None
    """What a voiced run sends, carrying the selected turns either side for prosody, and None unless VOICED."""
    buy: Buy | None = None
    """What sending `request` buys, counted every way a voice could bill it, and None unless VOICED. It is not
    certain for an UNCHECKED turn, whose take may already be on disk."""

    @property
    def key(self) -> TurnKey:
        """The turn this plan is for, by its section and its number."""
        return TurnKey(self.section.number, self.turn.number)

    @property
    def characters_sent(self) -> int:
        """How many characters of script this turn would send, which a per-character bill counts."""
        return len(canonical_text(self.turn.pieces))


@dataclass(frozen=True)
class NarratePlan:
    """Every selected turn's plan for one narrate run, in script order, and what the run costs."""

    takes: tuple[TakePlan, ...]
    voiced: bool
    """The run calls a provider for any take it lacks: it may spend, or some turn's voice bills nothing and is
    named. It may voice no turn, when every take is held."""
    cost: Cost
    """Each take priced at the bill of the voice that reads it, with an UNCHECKED turn counted into the ceiling
    alone."""


class _Reading:
    """One read of the take index, the script and the settings, with `TakePlaces.find` asked once per digest."""

    def __init__(
        self,
        inputs: Inputs,
        *,
        spoken: Sequence[ScriptSection],
        rows: Mapping[TurnKey, Take],
        replace_voiced: bool,
        retake: Collection[TurnKey] = (),
        reread: Collection[int] = (),
    ) -> None:
        self.inputs = inputs
        self.replace_voiced = replace_voiced
        self.retake = frozenset(retake)
        """The turns this run was told to make again, whatever the other turns of their sections hold."""
        self.reread = frozenset(reread)
        """The sections this run reads again without being told to, which it never voices or replaces."""
        self.spoken = tuple(spoken)
        self.rows = dict(rows)
        self.matched: dict[TurnKey, str] = {}
        """The digest of the voiced row each UNCHECKED turn matched by its voice and its text."""
        self.stand_ins: dict[TurnKey, str] = {}
        """The placeholder digest of each judged turn."""
        self.places = inputs.take_places
        self._files: dict[str, TakeFiles] = {}
        played = {row.digest for row in self.rows.values() if row.voiced}
        moved = bool(played) and not any(self.files(digest).in_takes_directory for digest in played)
        self.unheld = sum(not self.held(digest) for digest in played) if moved else 0
        """How many of the index's voiced takes no place holds, when the takes directory holds none of them."""

    @classmethod
    def of(
        cls, inputs: Inputs, *, replace_voiced: bool, retake: Collection[TurnKey] = (), reread: Collection[int] = ()
    ) -> _Reading:
        """The script's spoken sections and the take index, each read once."""
        index = inputs.takes()
        rows = {row.turn_key: row for row in index.sections} if index is not None else {}
        return cls(
            inputs, spoken=inputs.spoken(), rows=rows, replace_voiced=replace_voiced, retake=retake, reread=reread
        )

    def replaces(self, key: TurnKey) -> bool:
        """Whether this run was told to replace this turn's voiced take, by its section or by its own name."""
        return key.section not in self.reread and (self.replace_voiced or key in self.retake)

    def files(self, digest: str) -> TakeFiles:
        """Where this take's files are, found once per reading."""
        if digest not in self._files:
            self._files[digest] = self.places.find(digest)
        return self._files[digest]

    def held(self, digest: str) -> bool:
        """True when some place holds a good copy of this take and its words file."""
        return self.files(digest).held

    def voice_of(self, turn: Turn) -> VoiceInForce:
        """The voice that reads one turn."""
        return self.inputs.voice_of(turn)

    def judge(self, section: ScriptSection) -> SectionTakeState:
        """One section's take state, turn by turn, against every other turn of the script."""
        return SectionTakeState(section, tuple(self._judge(section, turn) for turn in section.turns))

    def _judge(self, section: ScriptSection, turn: Turn) -> TurnTakeState:
        """One turn's take state, against every other turn of the script."""
        key = TurnKey(section.number, turn.number)
        self.stand_ins[key] = placeholder_inputs(self.inputs, turn).digest
        here = self.rows.get(key)
        elsewhere = {
            other.spoken for one in self.spoken for other in one.turns if TurnKey(one.number, other.number) != key
        }
        voice = self.voice_of(turn)
        own = [
            row
            for row in self.rows.values()
            if row.section == section.number and row.voiced and row.voice == voice.name and row.spoken not in elsewhere
        ]
        digest = take_inputs(turn, voice).digest if voice.named else None
        if digest is not None:
            state, reason = self._named(key, turn, voice, digest, here, own)
        else:
            state, reason = self._unnamed(key, turn, voice, own)
        gone = (
            self.unheld > 0
            and state in (TakeState.MISSING, TakeState.PLACEHOLDER)
            and reason != REPLACED
            and here is not None
            and here.voiced
            and not self.held(here.digest)
        )
        why = _moved(self.inputs, self.unheld) if gone else reason
        return TurnTakeState(turn, voice, state, why, digest, gone)

    def _named(
        self, key: TurnKey, turn: Turn, voice: VoiceInForce, digest: str, here: Take | None, own: list[Take]
    ) -> tuple[TakeState, str]:
        """The state of a turn whose voiced take its named voice gives a digest."""
        if self.held(digest):
            return TakeState.VOICED, HELD if here is not None and here.digest == digest else UNINDEXED
        refused = self.files(digest).refusal(key.section)
        if refused is not None:
            if self.replaces(key):
                return TakeState.MISSING, REPLACED
            raise refused
        older = [
            row for row in self.rows.values() if row.voiced and row.spoken == turn.spoken and row.voice == voice.name
        ]
        older += own
        if any(self.held(row.digest) for row in older):
            return TakeState.STALE, CHANGED
        if self.held(self.stand_ins[key]):
            return TakeState.PLACEHOLDER, NO_VOICED_TAKE
        named = older or any(row.digest == digest for row in self.rows.values())
        return TakeState.MISSING, FILES_GONE if named else NO_VOICED_TAKE

    def _unnamed(self, key: TurnKey, turn: Turn, voice: VoiceInForce, own: list[Take]) -> tuple[TakeState, str]:
        """The state of a turn whose voice has no id, so its voiced take is matched by that voice and its text.

        The match is never by text alone, because another voice's take of the same words is not this
        voice's, and playing it would put the wrong voice in the film with nothing to say so.
        """
        same = sorted(
            (row for row in self.rows.values() if row.voiced and row.spoken == turn.spoken and row.voice == voice.name),
            key=lambda row: row.turn_key != key,
        )
        found = next((row for row in same if self.held(row.digest)), None)
        if found is not None:
            self.matched[key] = found.digest
            return TakeState.UNCHECKED, without_a_voice(voice)
        for row in same:
            refused = self.files(row.digest).refusal(key.section)
            if refused is not None and self.replaces(key):
                return TakeState.MISSING, REPLACED
            if refused is not None:
                raise _unnamed_refusal(refused, voice)
        if any(self.held(row.digest) for row in own):
            return TakeState.STALE, CHANGED
        if self.held(self.stand_ins[key]):
            return TakeState.PLACEHOLDER, NO_VOICED_TAKE
        return TakeState.MISSING, FILES_GONE if own else NO_VOICED_TAKE


def _unnamed_refusal(refused: InputError, voice: VoiceInForce) -> InputError:
    """A damaged take's refusal when its voice has no id, whose hint names the voice before anything is bought."""
    hint = refused.hint or ""
    return InputError(
        str(refused),
        hint=(
            f"Name the voice with [voices.{voice.name}] id in {PROJECT_FILE} or {id_variable(voice.name)} first. "
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
    `TakePlaces.find` is asked once per distinct digest. Every turn of every spoken section of the
    script is judged against the rest, so a turn reads the same whichever run asks, and the selection
    chooses which sections are returned, planned and refused. It sends nothing and writes nothing.
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
        """True when a run that buys nothing would change no take: every selected turn's
        `plan(spend=False)` outcome is KEPT and the take index names the take it keeps."""
        rows = self._reading.rows
        return all(
            plan.outcome is TakeOutcome.KEPT and (row := rows.get(plan.key)) is not None and row.digest == plan.digest
            for plan in self.plan(spend=False).takes
        )

    def planned_words(self, number: int) -> Words:
        """The words section `number` will have after a voiced run, counted from its start.

        Each turn's are the held take's own words for a voiced turn, the matched row's take's own words
        for an unchecked one, and estimated words at the placeholder pace for any other. A take whose
        voice sent no word times plays its kept aligned words, read with no take index and no server, or
        words estimated over its sound, which the row names when it names the take and is measured only
        when no row does. A section of one turn answers that turn's words, as the kind read from disk,
        and never a placeholder's words passed off as a provider's. A section of several answers every
        turn's words on one clock, each turn after the lead, every earlier turn's length and its gap, as
        `EstimatedWords` when any turn's are estimated.
        """
        state = self._states[number]
        lead = self._inputs.lead_seconds(number)
        if len(state.turns) == 1:
            return on_section_clock(self._turn_words(number, state.turns[0]), lead)
        joined: list = []
        at = lead
        estimated = False
        for one in state.turns:
            said = self._turn_words(number, one)
            estimated = estimated or said.estimated
            joined += said.shifted(at)
            gap = gap_after(self._inputs, state.section, one.turn)
            at += self._length(number, one, said) + (gap or 0.0)
        kind = EstimatedWords if estimated else Words
        return on_section_clock(kind(words=tuple(joined)), 0.0)

    def _turn_words(self, number: int, one: TurnTakeState) -> Words:
        """One turn's planned words on its take's own clock, a held take's cut at its sound end as the join cuts it."""
        key = TurnKey(number, one.turn.number)
        digest = one.digest if one.state is TakeState.VOICED else self._reading.matched.get(key)
        if digest is not None:
            found = self._inputs.take_words(digest)
            if isinstance(found, ProviderWords):
                said = found if found.timed else self._untimed_words(key, digest, found, one.turn.spoken)
                return said.cut_at(self._sound_end(key, digest))
        length = one.turn.placeholder_seconds(self._inputs.settings.narration)
        return EstimatedWords(words=tuple(one.turn.estimated_words(length)))

    def _length(self, number: int, one: TurnTakeState, said: Words) -> float:
        """How long one turn's take plays before its gap: its row's sound when the row names the take, else its
        words'."""
        row = self._reading.rows.get(TurnKey(number, one.turn.number))
        if row is not None and row.digest in (one.digest, self._reading.matched.get(row.turn_key)):
            return row.sound_seconds
        return said.end or 0.0

    def _untimed_words(self, key: TurnKey, digest: str, said: ProviderWords, spoken: str) -> Words:
        """The words a take whose voice sent no word times plays: its kept aligned words, or estimated words."""
        row = self._reading.rows.get(key)
        named = row if row is not None and row.digest == digest else None
        heard = named.spoken if named is not None else spoken
        if said.audio is not None:
            prefer = named.alignment if named is not None else None
            aligned = kept_alignment(self._inputs, said.audio.blake3, spoken_words(heard), prefer=prefer)
            if aligned is not None:
                return aligned
        return EstimatedWords(words=tuple(estimated_words(heard, self._sound_end(key, digest))))

    def _sound_end(self, key: TurnKey, digest: str) -> float:
        """Where a held take's sound ends: as its row says when the row names the take, else measured from its bytes."""
        row = self._reading.rows.get(key)
        if row is not None and row.digest == digest and row.sound_end_seconds is not None:
            return row.sound_end_seconds
        return sound_end_of(self._inputs, self._inputs.take_places.find(digest).audio)

    def plan(self, *, spend: bool, force: bool = False) -> NarratePlan:
        """What one narrate run does with each turn of each selected section, sending nothing and writing nothing.

        A turn whose voice is called sends its request when its voiced take is not held. A turn whose
        voice is not called keeps its held take, keeps an UNCHECKED take under its matched row's digest,
        and plays a placeholder otherwise. Under `replace_voiced` it plays a placeholder with `REPLACED`
        for every selected voiced take, held or damaged. Two turns share a take when their digest is
        equal, or, with no id on their voice, when their voice and their canonical text are, and the
        second is kept for the first, so it is bought and priced once, and when only the second voices it
        is the one that sends. After the selected sections come the turns of unselected ones whose index
        row names a take this run voices, each KEPT with `SHARED`, so their rows are placed on the new
        take. `force` makes placeholders again, never touches a voiced take, and is ignored for a turn its
        voice voices. Whether a turn voices is read from `spend` and its own voice's bill: a free named
        voice always voices, and a paid one only when the run may spend. A run told to make named turns
        again voices those turns alone, held or not, and every other turn of their sections plays what it
        holds as a run that voices nothing would. A section read again without being selected is planned
        as a run that voices nothing would plan it.
        """
        inputs = self._inputs
        retake, reread = self._reading.retake, self._reading.reread
        selected = [(state.section, one) for state in self._states.values() for one in state.turns]
        voicing = {
            key
            for section, one in selected
            if ((key := TurnKey(section.number, one.turn.number)) in retake or not retake)
            and section.number not in reread
            and (spend or (one.voice.free and one.voice.named))
        }
        requests = self._requests(selected, voicing)
        made = [
            self._voicing(section, one, requests)
            if TurnKey(section.number, one.turn.number) in voicing
            else self._standing_in(section, one, force=force)
            for section, one in selected
        ]
        sameness = [
            plan.digest or (one.voice.name, canonical_text(one.turn.pieces))
            for (_section, one), plan in zip(selected, made, strict=True)
        ]
        first: dict[object, TakePlan] = {}
        for same, plan in zip(sameness, made, strict=True):
            earlier = first.get(same)
            if earlier is None or (plan.outcome is TakeOutcome.VOICED and earlier.outcome is not TakeOutcome.VOICED):
                first[same] = plan
        plans: list[TakePlan] = []
        for (section, one), same, plan in zip(selected, sameness, made, strict=True):
            sender = first[same]
            if sender is plan:
                plans.append(plan)
                continue
            why = SHARED if sender.outcome is TakeOutcome.VOICED else one.reason
            plans.append(TakePlan(section, one.turn, one.voice, TakeOutcome.KEPT, sender.digest, why, None))
        plans += self._replaced(plans)
        return NarratePlan(takes=tuple(plans), voiced=bool(voicing), cost=cost_of(inputs, self._buys(plans)))

    def _replacing(self, section: ScriptSection, one: TurnTakeState) -> bool:
        """Whether this run was told to replace this turn's held voiced take."""
        return self._reading.replaces(TurnKey(section.number, one.turn.number)) and one.state is TakeState.VOICED

    def _voicing(
        self, section: ScriptSection, one: TurnTakeState, requests: Mapping[TurnKey, SpeechRequest]
    ) -> TakePlan:
        """One turn's plan when its voice is called: kept when its voiced take is held, else sent."""
        if one.state is TakeState.VOICED and not self._replacing(section, one):
            return TakePlan(section, one.turn, one.voice, TakeOutcome.KEPT, one.digest, one.reason, None)
        reason = REPLACED if self._replacing(section, one) else one.reason
        key = TurnKey(section.number, one.turn.number)
        buy = Buy(
            characters=len(canonical_text(one.turn.pieces)),
            seconds=one.turn.estimated_seconds(self._inputs.settings.narration),
            sections=(section.number,),
            certain=one.state is not TakeState.UNCHECKED,
            voice=one.voice,
        )
        return TakePlan(section, one.turn, one.voice, TakeOutcome.VOICED, one.digest, reason, requests[key], buy)

    def _standing_in(self, section: ScriptSection, one: TurnTakeState, *, force: bool) -> TakePlan:
        """One turn's plan when its voice is not called: its held voiced take, else a placeholder.

        A turn named to be made again is replaced only by a run that voices it, so here it keeps its take.
        """
        key = TurnKey(section.number, one.turn.number)
        matched = self._reading.matched.get(key)
        replacing = self._reading.replaces(key) and key not in self._reading.retake and one.state is TakeState.VOICED
        if one.state is TakeState.VOICED and not replacing:
            return TakePlan(section, one.turn, one.voice, TakeOutcome.KEPT, one.digest, one.reason, None)
        if one.state is TakeState.UNCHECKED and matched is not None:
            return TakePlan(section, one.turn, one.voice, TakeOutcome.KEPT, matched, one.reason, None)
        stand_in = self._reading.stand_ins[key]
        kept = self._reading.held(stand_in) and not force
        reason = REPLACED if replacing else one.reason
        outcome = TakeOutcome.KEPT if kept else TakeOutcome.PLACEHOLDER
        return TakePlan(section, one.turn, one.voice, outcome, stand_in, reason, None)

    def _replaced(self, plans: Sequence[TakePlan]) -> list[TakePlan]:
        """The turns of unselected sections whose index row names a take these plans voice, each kept on the new
        take."""
        reading = self._reading
        made = {plan.digest for plan in plans if plan.outcome is TakeOutcome.VOICED and plan.digest is not None}
        if not made:
            return []
        return [
            TakePlan(section, turn, reading.voice_of(turn), TakeOutcome.KEPT, row.digest, SHARED, None)
            for section in reading.spoken
            if section.number not in self._states
            for turn in section.turns
            if (row := reading.rows.get(TurnKey(section.number, turn.number))) is not None and row.digest in made
        ]

    def _requests(
        self, selected: Sequence[tuple[ScriptSection, TurnTakeState]], voicing: Collection[TurnKey]
    ) -> dict[TurnKey, SpeechRequest]:
        """One request per turn whose voice is called, each carrying for prosody the neighbouring selected turns
        that voice reads, in script order."""
        requests: dict[TurnKey, SpeechRequest] = {}
        for section, one in selected:
            key = TurnKey(section.number, one.turn.number)
            if key not in voicing:
                continue
            same = [other.turn for _section, other in selected if other.voice.name == one.voice.name]
            at = next(place for place, turn in enumerate(same) if turn is one.turn)
            requests[key] = SpeechRequest(
                pieces=one.turn.pieces,
                voice_id=one.voice.id,
                model=one.voice.model,
                voice_settings=dict(one.voice.identity),
                output_format=one.voice.output.format,
                previous_text=same[at - 1].spoken if at else None,
                next_text=same[at + 1].spoken if at + 1 < len(same) else None,
            )
        return requests

    def charged(self, plan: NarratePlan, charged: Collection[TurnKey], possibly: Collection[TurnKey] = ()) -> Cost:
        """What a voiced run paid for: the turns whose charge ran, as bought, and the ones it may have paid for.

        A turn counts as bought once its voice answered and its charge ran, whether or not its take
        then reached the disk. A turn the run kept, because another run voiced its take first, or one
        that played a placeholder, because its free voice did not answer, was not paid for, so it is not
        counted. A turn in `possibly` sent its request and has no charge, because its reply broke or the
        run left without it, so the provider may have billed it, and it counts toward the ceiling and
        never toward the dollars.
        """
        bought = [replace(buy, certain=True) for buy in self._buys([p for p in plan.takes if p.key in charged])]
        maybe = [replace(buy, certain=False) for buy in self._buys([p for p in plan.takes if p.key in possibly])]
        return cost_of(self._inputs, bought + maybe, state=CostState.CHARGED)

    @staticmethod
    def _buys(plans: Sequence[TakePlan]) -> list[Buy]:
        """What the voiced plans buy, in plan order, one take each, an UNCHECKED turn's counted as possible."""
        return [p.buy for p in plans if p.outcome is TakeOutcome.VOICED and p.buy is not None]


def take_states(
    inputs: Inputs,
    sections: Sequence[ScriptSection] | None = None,
    *,
    replace_voiced: bool = False,
    retake: Collection[TurnKey] = (),
    reread: Collection[int] = (),
) -> TakeStates:
    """The take state of each of these spoken sections, or of every spoken section when None.

    An empty selection reads nothing: no script, no index and no take. The voices, their models and
    their providers are read from the settings. A selected voiced take whose every copy is damaged is
    refused with `TakeFiles.refusal`, unless `replace_voiced` or `retake` names its turn, under which it
    reads MISSING with the reason `REPLACED`: a run that voices buys it again, and one that does not
    plays a placeholder. `retake` is the turns a run was told to make again, which `retake_turns` reads
    off the names a caller gave, and a plan of it voices those turns alone. `reread` is the sections
    among these that a run reads again without having been told to, which it never voices or replaces.
    """
    if sections is not None and not sections:
        return TakeStates(inputs, (), _Reading(inputs, spoken=(), rows={}, replace_voiced=replace_voiced))
    reading = _Reading.of(inputs, replace_voiced=replace_voiced, retake=retake, reread=reread)
    wanted = {section.number for section in sections} if sections is not None else None
    chosen = [section for section in reading.spoken if wanted is None or section.number in wanted]
    return TakeStates(inputs, [reading.judge(section) for section in chosen], reading)


def retake_turns(inputs: Inputs, turns: Sequence[tuple[int, int]], takes: Sequence[str]) -> tuple[TurnKey, ...]:
    """The turns a run is told to make again, named by number in `turns` and by the take they play in `takes`.

    A turn is a section's number and the turn's number in that section, counted from one, so a section
    one voice reads is the one turn `N.1`. A take is named by its digest or by a prefix of it that names
    exactly one take, which is the take the take index or the current text and voice in force give the
    turn. Every turn playing that take is named, because turns that share a take share its voicing
    again. A turn the script does not have, a name no take answers to and a prefix that names several
    takes are each refused before anything is read further. The turns come back in script order, once
    each.
    """
    spoken = {section.number: section for section in inputs.spoken()}
    every = [TurnKey(number, turn.number) for number, section in spoken.items() for turn in section.turns]
    found: list[TurnKey] = []
    for section, turn in turns:
        key = TurnKey(section, turn)
        if key not in every:
            raise InputError(
                f"the script has no spoken turn {section}.{turn}, so there is nothing to voice again.",
                hint=f"The spoken turns are {', '.join(f'{one.section}.{one.turn}' for one in every)}.",
                location=at(inputs.script_path, inputs.root),
            )
        found.append(key)
    if takes:
        plays = _plays(inputs, spoken)
        for name in takes:
            wanted = name.strip().lower()
            digests = sorted({digest for named in plays.values() for digest in named if digest.startswith(wanted)})
            if len(digests) != 1:
                what = "no take" if not digests else f"{len(digests)} takes ({', '.join(digests)})"
                raise InputError(
                    f"{name!r} names {what} a spoken turn plays, so there is nothing to voice again.",
                    hint="Name one take by its digest, or enough of its start to tell it from the rest, as "
                    f"{inputs.relative(inputs.workspace.takes_path).as_posix()} names each turn's take, or name the "
                    "turn with --turn.",
                    location=at(inputs.script_path, inputs.root),
                )
            found += [key for key, named in plays.items() if digests[0] in named]
    return tuple(sorted(set(found)))


def sections_off_their_audio(
    inputs: Inputs, previous: Takes | None, targets: Sequence[ScriptSection]
) -> list[ScriptSection]:
    """The spoken sections this run did not select that hold a voiced take other than the audio their row was
    measured on.

    A take's digest names its inputs, and a take bought again on request keeps it, so the row's audio
    print is what says whether its length, its sound end and its words are this take's. A take whose
    words file does not read is left to the run that keeps it, which refuses it naming its section.
    """
    if previous is None:
        return []
    spoken = {section.number: section for section in inputs.spoken()}
    selected = {section.number for section in targets}
    moved: set[int] = set()
    for row in previous.sections:
        if not row.voiced or row.section in selected | moved or row.section not in spoken:
            continue
        if not inputs.take_places.find(row.digest).held:
            continue
        try:
            said = inputs.take_words(row.digest)
        except (UnreadablePaidRecord, UnreadableCache):  # silent: keeping the take refuses it, naming its section
            continue
        heard = said.audio.blake3 if isinstance(said, ProviderWords) and said.audio is not None else None
        if heard != row.audio_blake3:
            moved.add(row.section)
    return [spoken[number] for number in sorted(moved)]


def _plays(inputs: Inputs, spoken: Mapping[int, ScriptSection]) -> dict[TurnKey, set[str]]:
    """Every take each spoken turn could be named by: its row's in the take index, and its current voiced one."""
    index = inputs.takes()
    plays: dict[TurnKey, set[str]] = {}
    for row in index.sections if index is not None else ():
        if row.section in spoken and row.turn <= len(spoken[row.section].turns):
            plays.setdefault(row.turn_key, set()).add(row.digest)
    for number, section in spoken.items():
        for turn in section.turns:
            voice = inputs.voice_of(turn)
            if voice.named:
                plays.setdefault(TurnKey(number, turn.number), set()).add(take_inputs(turn, voice).digest)
    return plays


__all__ = [
    "CHANGED",
    "FILES_GONE",
    "HELD",
    "NO_VOICED_TAKE",
    "REPLACED",
    "SHARED",
    "UNINDEXED",
    "WORST_FIRST",
    "NarratePlan",
    "SectionTakeState",
    "TakePlan",
    "TakeStates",
    "TurnTakeState",
    "retake_turns",
    "sections_off_their_audio",
    "take_states",
    "without_a_voice",
]
