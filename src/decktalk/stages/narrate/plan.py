"""The take plan: what a run would voice, what it already holds, and what that would cost.

A take is identified by its input digest and by nothing else, so two sections with the same words
share one take and renumbering or retitling a section moves no file and voices nothing. `TakeInputs`
is the whole of what that digest is taken over, so this module fills that model and never spells a
digest of its own.

The plan is also the approval stop. Nothing is bought until `Run.approve` has seen the price, and a
section whose cache could not be checked is priced apart, so the gate is given the figure the run
certainly spends and the figure it can reach, and never a small number that hides a large one.

A price needs no credential. The digest is over the provider's name, the voice id, the model, the
output format and the take identity its adapter declares, and the text, and none of those is a
secret, so the plan reads the provider's name from `[voice] provider` and never builds the provider
to price a run. The price is the bill its adapter declares: per character, per second of audio, or
free. A
service can therefore price an edit on a machine that holds no key, and a one-section edit is priced
as one section rather than as the whole film.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decktalk.artifacts import PlaceholderInputs, TakeInputs, Takes, is_placeholder, words_file
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.inputs.script import Segment
from decktalk.inputs.workspace import Workspace
from decktalk.machine import Run
from decktalk.page import SECOND_DIGITS
from decktalk.results import DOLLAR_DIGITS, Cost, CostState, TakeStatus
from decktalk.settings import BY_ID, PROJECT_FILE
from decktalk.speech import DECLARED, SpeechProvider, SpeechRequest, canonical_text, output_of, renders_pauses, table_of
from decktalk.stages import billed, dollars_for, rate_fields, voice_context

WITHOUT_A_VOICE = "no voice is named, so the cache cannot be checked"
"""Why a section's take is unknown, which is the one state a plan cannot resolve on its own."""

CHANGED = "the text, the voice, the model or the voice settings changed"
"""Why a section's words are on disk under another digest, which only one of a take's inputs moving explains."""

NO_TAKE_YET = "it has no take yet"
"""Why a section never had a take, worded as a clause so the plan and the `TAKE_MISSING` finding read it alike."""

SHARED = "another section of this run voices these words"
"""Why a section sends nothing for words an earlier section of the same run already plans to make."""


def take_identity(inputs: Inputs) -> dict[str, Any]:
    """The settings `[voice] provider` is sent and a take's digest is taken over, under the provider's own names.

    A shipped provider declares the mapping from its own table and `[voice] speed`. A provider a
    host registered owns no table, so `speed`, which every voice has, is the whole of it.
    """
    settings = inputs.settings
    declared = DECLARED.get(settings.voice.provider)
    table = table_of(settings, settings.voice.provider)
    if declared is None or table is None:
        return {"speed": settings.voice.speed}
    return declared.identity(table, settings.voice.speed)


def take_inputs(inputs: Inputs, segment: Segment, *, provider: str, voice_id: str, model: str) -> TakeInputs:
    """Everything that decides what one voiced take sounds like, which is everything its name is over."""
    return TakeInputs.of(
        provider=provider,
        voice=voice_id,
        model=model,
        output_format=output_of(inputs.settings, provider).format,
        settings=take_identity(inputs),
        text=canonical_text(segment.pieces),
    )


def placeholder_inputs(inputs: Inputs, segment: Segment) -> PlaceholderInputs:
    """Everything that decides what one placeholder take sounds like, which is its pace and its beats."""
    cfg = inputs.settings.narration
    return PlaceholderInputs(
        words_per_minute=cfg.placeholder_words_per_minute,
        beat_seconds=cfg.placeholder_beat_seconds,
        text=canonical_text(segment.pieces),
    )


def is_cached(digest: str, workspace: Workspace) -> bool:
    """True when some place a take is looked for holds a good copy of this take and its words file.

    The places are the project's takes directory, then the machine's take store, so a clone that committed
    its takes and a laptop that keeps them for every project both find them. A placeholder is looked
    for under the build alone, because a build makes it again for nothing.
    """
    return workspace.holding(digest) is not None


def refuse_damaged(inputs: Inputs, number: int, digest: str) -> None:
    """Refuse a voiced take whose every copy on disk is damaged, before a run plans to make it.

    A take some place holds a good copy of is found and never reaches here. One that no place holds
    at all is simply missing. One that is there and damaged everywhere is a paid record only voicing it
    again gives back, so the run stops rather than buying it again or playing a placeholder over it,
    and the damaged copy is left where it is.
    """
    if is_placeholder(digest):
        return
    damaged = inputs.workspace.damaged(digest)
    if damaged:
        raise damaged_refusal(inputs, number, digest, *damaged[0])


def damaged_refusal(inputs: Inputs, number: int, digest: str, place: Path, fault: str) -> InputError:
    """The sentence a voiced take is refused with when no place holds a good copy and `place` holds a damaged one."""
    takes = inputs.relative(inputs.workspace.takes).as_posix()
    return InputError(
        f"{fault} No place holds a good copy of the take section {number} plays, and only voicing it again "
        "gives it back, so DeckTalk neither buys it again nor deletes it.",
        hint=f"Put a good copy of {inputs.workspace.take_file(digest)} and {words_file(digest)} in {takes}, or run "
        f"decktalk narrate --section {number} --replace-voiced --spend knowing that it buys the take again.",
        location=at(place / words_file(digest), inputs.root, section=number),
    )


def speech_provider(run: Run, inputs: Inputs) -> SpeechProvider:
    """The provider `[voice] provider` names in the run's voices, built from the project's tuning and `.env`."""
    return run.voices.provider(inputs.settings.voice.provider, voice_context(inputs))


DROPPED_PAUSE_HINT = (
    "Pick a model that renders a timed pause, or take the [pause N] directions and break tags out of those sections."
)
"""What clears a timed pause the model would drop, which the refusal and the `check` finding both say."""


def refuse_dropped_pauses(inputs: Inputs, buying: list[Segment], *, model: str) -> None:
    """Refuse a run that would buy a take whose timed pause the model drops, before the price is asked for.

    `buying` is the sections the run would voice, so a section already on disk, or one this run
    leaves alone, never stops it.
    """
    dropped = dropped_pauses(inputs, buying, model=model)
    if dropped:
        raise InputError(
            f"[voice] provider {inputs.settings.voice.provider!r} renders no timed pause on model {model!r}, so "
            f"the pauses in sections {[segment.index for segment in dropped]} would be dropped.",
            hint=DROPPED_PAUSE_HINT,
            location=at(inputs.script_path, inputs.root),
        )


def dropped_pauses(inputs: Inputs, segments: list[Segment], *, model: str) -> list[Segment]:
    """Every section holding a timed pause that `[voice] provider` would drop on this model.

    The provider declares which of its models render a timed pause. A beat is a dash every model
    reads, so only a section with a timed pause can lose one.
    """
    if renders_pauses(inputs.settings.voice.provider, model):
        return []
    return [segment for segment in segments if any(piece.timed for piece in segment.pieces)]


def voice_id_of(inputs: Inputs) -> str:
    """The voice this project is read in, which is a published name and one of the take's own inputs.

    Two voices reading one sentence are two different takes, so the id names the file. It is
    `[voice] id` in `decktalk.toml`, which `DECKTALK_VOICE_ID` overrides, and a voiced run that
    names neither is refused here before anything is bought.
    """
    named = inputs.settings.voice.id
    if not named:
        raise InputError(
            f"no voice is named, because neither [voice] id in {PROJECT_FILE} nor {VOICE_ID_VARIABLE} is set.",
            hint=(
                f'Add id = "<your voice id>" under [voice] in {PROJECT_FILE}, or export {VOICE_ID_VARIABLE}, '
                "which is read from the environment and never from .env."
            ),
            location=at(inputs.root / PROJECT_FILE, inputs.root),
        )
    return named


def named_voice(inputs: Inputs) -> str:
    """The voice this project is read in, or nothing when the project has not named one yet.

    A run that buys nothing and a price both plan without a voice, with every voiced take unchecked,
    so neither is refused for a name only a purchase needs.
    """
    return inputs.settings.voice.id


VOICE_ID_VARIABLE = BY_ID["voice.id"].environment
"""The variable that overrides `[voice] id`, for anyone who keeps the id out of the file."""

UNNAMED = f"[voice] id is empty and {VOICE_ID_VARIABLE} is not set"
"""Why no voice is named, which the plan, the run's line and the finding all say in these words."""


@dataclass(frozen=True)
class TakePlan:
    """What a run would do with one section, and why."""

    segment: Segment
    status: TakeStatus
    reason: str = ""
    chapter: str = ""
    digest: str | None = None
    request: SpeechRequest | None = None
    unchecked: bool = False
    """The cache could not be checked at all, because there was no voice to ask what the digest is."""

    @property
    def cached(self) -> bool:
        """The take this section plays is on disk already, so the run sends nothing for it."""
        return self.status is TakeStatus.KEPT

    @property
    def characters_sent(self) -> int:
        """How many characters of script this section would send, which a per-character bill counts."""
        return len(canonical_text(self.segment.pieces))


def requests_for(inputs: Inputs, targets: list[Segment], *, model: str, voice_id: str) -> dict[int, SpeechRequest]:
    """One request per target section, each carrying the sections either side of it for prosody."""
    settings = take_identity(inputs)
    output_format = output_of(inputs.settings, inputs.settings.voice.provider).format
    return {
        segment.index: SpeechRequest(
            pieces=segment.pieces,
            voice_id=voice_id,
            model=model,
            voice_settings=settings,
            output_format=output_format,
            previous_text=targets[at - 1].spoken if at else None,
            next_text=targets[at + 1].spoken if at + 1 < len(targets) else None,
        )
        for at, segment in enumerate(targets)
    }


def miss_reason(previous: Takes | None, segment: Segment, digest: str, *, voiced: bool, elsewhere: set[str]) -> str:
    """Why this section's take is not on disk, as a clause the plan, the log and the `TAKE_MISSING` finding all carry.

    A take is found by its content, so the index is searched by content before it is searched by
    section number. `elsewhere` is the spoken text of every other section this run is planning, which
    is what tells an inserted section from an edited one: a row whose words now belong to another
    section says nothing about the section that took its number.
    """
    rows = previous.sections if previous is not None else ()
    if any(row.digest == digest for row in rows):
        return "the take or its words file is missing"
    same_words = [row for row in rows if row.spoken == segment.spoken]
    if same_words:
        return "only a take without voice exists" if voiced and not any(r.voiced for r in same_words) else CHANGED
    row = next((r for r in rows if r.section == segment.index), None)
    return CHANGED if row is not None and row.spoken not in elsewhere else NO_TAKE_YET


def plan_takes(
    inputs: Inputs,
    targets: list[Segment],
    digests: dict[int, str] | None,
    *,
    requests: dict[int, SpeechRequest] | None = None,
    voiced: bool = True,
    again: bool = False,
) -> list[TakePlan]:
    """What a run would do with each target section, sending nothing and writing nothing.

    `digests` is None when the provider could not be set up, and a section already holding a paid
    take is then unchecked while every other section still needs one. The request is carried on
    every plan either way, so a run that cannot check the cache still prices what it would send.

    Two sections with the same words come to one digest, so the second of them is already covered by
    the first and is planned as kept rather than sent and paid for twice. `again` makes each section
    whatever its cache holds, and its callers decide it, because making a voiced take again buys it.
    """
    previous = inputs.takes()
    chapters = inputs.chapters()
    wanted = TakeStatus.VOICED if voiced else TakeStatus.PLACEHOLDER
    plans: list[TakePlan] = []
    planned: set[str] = set()
    for segment in targets:
        chapter = chapters.get(segment.index, segment.title)
        request = (requests or {}).get(segment.index)
        if digests is None:
            plans.append(_unchecked_plan(previous, segment, chapter, request))
            continue
        digest = digests[segment.index]
        if digest in planned:
            plans.append(TakePlan(segment, TakeStatus.KEPT, SHARED, chapter, digest, request))
        elif again:
            plans.append(TakePlan(segment, wanted, "this run was told to make it again", chapter, digest, request))
            planned.add(digest)
        elif is_cached(digest, inputs.workspace):
            plans.append(TakePlan(segment, TakeStatus.KEPT, "", chapter, digest, request))
        else:
            refuse_damaged(inputs, segment.index, digest)
            elsewhere = {other.spoken for other in targets if other.index != segment.index}
            reason = miss_reason(previous, segment, digest, voiced=voiced, elsewhere=elsewhere)
            plans.append(TakePlan(segment, wanted, reason, chapter, digest, request))
            planned.add(digest)
    return plans


def _unchecked_plan(previous: Takes | None, segment: Segment, chapter: str, request: SpeechRequest | None) -> TakePlan:
    """The plan for one section when there is no voice to ask what its digest would be.

    Only a voiced take is in doubt, because only a voiced take could turn out to be the one this run
    would ask for. A section with no take, or with a take without voice, needs a voiced take whatever
    the voice is, so it is priced as certain rather than counted into the ceiling alone.
    """
    row = previous.of(segment.index) if previous is not None else None
    if row is not None and row.voiced:
        return TakePlan(segment, TakeStatus.VOICED, WITHOUT_A_VOICE, chapter=chapter, request=request, unchecked=True)
    why = "only a take without voice exists" if row is not None else NO_TAKE_YET
    return TakePlan(segment, TakeStatus.VOICED, why, chapter=chapter, request=request)


def voiced_plan(
    inputs: Inputs, targets: list[Segment], *, model: str, voice_id: str | None, replace: bool = False
) -> tuple[list[TakePlan], str | None]:
    """(what a voiced run would do, why the cache could not be checked), buying nothing.

    The provider is named by `[voice] provider` and is never built here, because building one needs
    the credential and a price does not. The one input a plan cannot do without is the voice id, and
    a project that names none is planned with every section it has paid for unchecked. `replace`
    plans every take again, which buys it again, so it is the run's `replace_voiced` and never `force`.
    """
    requests = requests_for(inputs, targets, model=model, voice_id=voice_id or "")
    if not voice_id:
        why = f"{WITHOUT_A_VOICE.capitalize()}, because {UNNAMED}."
        return plan_takes(inputs, targets, None, requests=requests, again=replace), why
    provider = inputs.settings.voice.provider
    digests = {
        segment.index: take_inputs(inputs, segment, provider=provider, voice_id=voice_id, model=model).digest
        for segment in targets
    }
    return plan_takes(inputs, targets, digests, requests=requests, again=replace), None


def placeholder_plan(inputs: Inputs, targets: list[Segment], *, force: bool = False) -> list[TakePlan]:
    """The placeholder each of these sections plays in place of a missing take, cached by content too.

    A placeholder costs nothing, so it is the one take `force` makes again.
    """
    digests = {segment.index: placeholder_inputs(inputs, segment).digest for segment in targets}
    return plan_takes(inputs, targets, digests, voiced=False, again=force)


def seconds_of(inputs: Inputs, plan: TakePlan) -> float:
    """How long this section's take is expected to run, which a per-second bill is priced on before it exists."""
    return plan.segment.estimated_seconds(inputs.settings.narration)


def cost_of(plans: list[TakePlan], inputs: Inputs, *, state: CostState) -> Cost:
    """What these plans cost at the bill `[voice] provider` declares, with what they can cost priced beside it.

    A per-character bill counts the characters each take sends, a per-second bill counts the seconds
    each take is expected to run, and a free voice counts nothing. A section whose cache could not be
    checked may turn out to need a take, so it is counted into the ceiling and never into the price,
    and the gate is then given the figure the run certainly spends and the figure it can reach.
    """
    provider = inputs.settings.voice.provider
    sending = [plan for plan in plans if plan.status is TakeStatus.VOICED and not plan.unchecked]
    maybe = [plan for plan in plans if plan.unchecked]
    characters = sum(plan.characters_sent for plan in sending)
    seconds = sum(seconds_of(inputs, plan) for plan in sending)
    certain = billed(characters, seconds, provider)
    reach = certain + sum(billed(p.characters_sent, seconds_of(inputs, p), provider) for p in maybe)
    return Cost(
        state=state,
        sections=tuple(plan.segment.index for plan in sending + maybe),
        characters=characters,
        seconds=round(seconds, SECOND_DIGITS),
        dollars=round(dollars_for(certain, inputs), DOLLAR_DIGITS),
        ceiling_dollars=round(dollars_for(reach, inputs), DOLLAR_DIGITS),
        **rate_fields(inputs),
    )


__all__ = [
    "CHANGED",
    "NO_TAKE_YET",
    "SHARED",
    "UNNAMED",
    "VOICE_ID_VARIABLE",
    "TakePlan",
    "DROPPED_PAUSE_HINT",
    "dropped_pauses",
    "damaged_refusal",
    "refuse_damaged",
    "refuse_dropped_pauses",
    "is_cached",
    "miss_reason",
    "named_voice",
    "placeholder_inputs",
    "placeholder_plan",
    "plan_takes",
    "requests_for",
    "seconds_of",
    "speech_provider",
    "cost_of",
    "take_inputs",
    "voice_id_of",
    "take_identity",
    "voiced_plan",
]
