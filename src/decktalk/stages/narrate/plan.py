"""The take plan: what a run would voice, what it already holds, and what that would cost.

A take is identified by its content hash and by nothing else, so two sections with the same words
share one take and renumbering or retitling a section moves no file and voices nothing. `TakeInputs`
is the whole of what that hash is taken over, so this module fills that model and never spells a
digest of its own.

The plan is also the approval stop. Nothing is bought until `Run.approve` has seen the price, and a
section whose cache could not be checked is priced apart, so the gate is given the figure the run
certainly spends and the figure it can reach, and never a small number that hides a large one.

A price needs no credential. The digest is over the provider's name, the voice id, the model, the
output format, the voice settings and the text, and none of those is a secret, so the plan reads
the provider's name from `[voice] provider` and never builds the provider to price a run. A
service can therefore price an edit on a machine that holds no key, and a one-section edit is priced
as one section rather than as the whole film.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decktalk.artifacts import PlaceholderInputs, TakeInputs, Takes, take_file, words_file
from decktalk.inputs import Inputs
from decktalk.inputs.script import Segment
from decktalk.results import Spend, SpendState, TakeStatus
from decktalk.settings import VoiceConfig
from decktalk.speech import SpeechProvider, SpeechRequest, get_provider
from decktalk.stages import DOLLAR_DIGITS, dollars_for, price_layer, voice_context

WITHOUT_A_VOICE = "no voice is named, so the cache cannot be checked"
"""Why a section's take is unknown, which is the one state a plan cannot resolve on its own."""


def voice_settings(voice: VoiceConfig) -> dict[str, Any]:
    """What `[voice]` asks the provider for, under the provider's own names.

    The names differ in one place, because the provider calls the speaker boost `use_speaker_boost`
    while the key an author writes is `speaker_boost`. The mapping lives here rather than in the
    provider, because these five values are also inputs of the take's digest.
    """
    return {
        "stability": voice.stability,
        "similarity_boost": voice.similarity_boost,
        "style": voice.style,
        "use_speaker_boost": voice.speaker_boost,
        "speed": voice.speed,
    }


def take_inputs(inputs: Inputs, segment: Segment, *, provider: str, voice_id: str, model: str) -> TakeInputs:
    """Everything that decides what one paid take sounds like, which is everything its name is over."""
    return TakeInputs.of(
        provider=provider,
        voice=voice_id,
        model=model,
        output_format=inputs.settings.narration.output_format,
        settings=voice_settings(inputs.settings.voice),
        text=segment.tts_text,
    )


def placeholder_inputs(inputs: Inputs, segment: Segment) -> PlaceholderInputs:
    """Everything that decides what one placeholder take sounds like, which is its pace and its beats."""
    cfg = inputs.settings.narration
    return PlaceholderInputs(
        words_per_minute=cfg.silent_words_per_minute,
        beat_seconds=cfg.silent_beat_seconds,
        text=segment.text,
    )


def is_cached(digest: str, takes_dir: Path) -> bool:
    """True when the take of this digest and its words file are both on disk, which is the whole cache."""
    return (takes_dir / take_file(digest)).exists() and (takes_dir / words_file(digest)).exists()


def speech_provider(inputs: Inputs) -> SpeechProvider:
    """The provider `[voice] provider` names, built from this project's tuning and its own `.env`."""
    return get_provider(inputs.document.voice.provider, voice_context(inputs))


def voice_id_of(inputs: Inputs) -> str:
    """The voice this project is read in, which is a published name and one of the take's own inputs.

    Two voices reading one sentence are two different takes, so the id names the file. It is read
    from `.env` because it belongs to the account whose key pays for the take, and it is revealed
    here because it is a name rather than a secret.
    """
    return inputs.env.require(VOICE_VARIABLE)[0].reveal()


VOICE_VARIABLE = "ELEVENLABS_VOICE_ID"
"""The variable that names the voice, which the starter's `.env.example` already writes."""


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
        """How many characters of script this section would send, which is what a provider bills."""
        return len(self.request.text) if self.request else len(self.segment.tts_text)


def requests_for(inputs: Inputs, targets: list[Segment], *, model: str, voice_id: str) -> dict[int, SpeechRequest]:
    """One request per target section, each carrying the sections either side of it for prosody."""
    settings = voice_settings(inputs.settings.voice)
    output_format = inputs.settings.narration.output_format
    return {
        segment.index: SpeechRequest(
            text=segment.tts_text,
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
    """Why this section's take is not on disk, in the words the plan and the payload both carry.

    A take is found by its content, so the index is searched by content before it is searched by
    section number. `elsewhere` is the spoken text of every other section this run is planning, which
    is what tells an inserted section from an edited one: a row whose words now belong to another
    section says nothing about the section that took its number.
    """
    rows = previous.sections if previous is not None else ()
    if any(row.hash == digest for row in rows):
        return "the take or its words file is missing"
    changed = "the text, the voice, the model or the voice settings changed"
    same_words = [row for row in rows if row.spoken == segment.spoken]
    if same_words:
        return "only a take without voice exists" if voiced and not any(r.voiced for r in same_words) else changed
    row = next((r for r in rows if r.section == segment.index), None)
    return changed if row is not None and row.spoken not in elsewhere else "no take yet"


def plan_takes(
    inputs: Inputs,
    targets: list[Segment],
    digests: dict[int, str] | None,
    *,
    requests: dict[int, SpeechRequest] | None = None,
    voiced: bool = True,
    force: bool = False,
) -> list[TakePlan]:
    """What a run would do with each target section, sending nothing and writing nothing.

    `digests` is None when the provider could not be set up, and a section already holding a paid
    take is then unchecked while every other section still needs one. The request is carried on
    every plan either way, so a run that cannot check the cache still prices what it would send.

    Two sections with the same words come to one digest, so the second of them is already covered by
    the first and is planned as kept rather than sent and paid for twice.
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
            shared = "another section of this run voices these words"
            plans.append(TakePlan(segment, TakeStatus.KEPT, shared, chapter, digest, request))
        elif force:
            plans.append(TakePlan(segment, wanted, "this run was told to make it again", chapter, digest, request))
            planned.add(digest)
        elif is_cached(digest, inputs.workspace.takes_dir):
            plans.append(TakePlan(segment, TakeStatus.KEPT, "", chapter, digest, request))
        else:
            elsewhere = {other.spoken for other in targets if other.index != segment.index}
            reason = miss_reason(previous, segment, digest, voiced=voiced, elsewhere=elsewhere)
            plans.append(TakePlan(segment, wanted, reason, chapter, digest, request))
            planned.add(digest)
    return plans


def _unchecked_plan(previous: Takes | None, segment: Segment, chapter: str, request: SpeechRequest | None) -> TakePlan:
    """The plan for one section when there is no voice to ask what its digest would be.

    Only a paid take is in doubt, because only a paid take could turn out to be the one this run
    would ask for. A section with no take, or with a take without voice, needs a paid take whatever
    the voice is, so it is priced as certain rather than counted into the ceiling alone.
    """
    row = previous.of(segment.index) if previous is not None else None
    if row is not None and row.voiced:
        return TakePlan(segment, TakeStatus.VOICED, WITHOUT_A_VOICE, chapter=chapter, request=request, unchecked=True)
    why = "only a take without voice exists" if row is not None else "no take yet"
    return TakePlan(segment, TakeStatus.VOICED, why, chapter=chapter, request=request)


def voiced_plan(
    inputs: Inputs, targets: list[Segment], *, model: str, voice_id: str | None, force: bool = False
) -> tuple[list[TakePlan], str | None]:
    """(what a voiced run would do, why the cache could not be checked), spending nothing.

    The provider is named by `[voice] provider` and is never built here, because building one needs
    the credential and a price does not. The one input a plan cannot do without is the voice id, and
    a project that names none is planned with every section it has paid for unchecked.
    """
    requests = requests_for(inputs, targets, model=model, voice_id=voice_id or "")
    if not voice_id:
        why = f"{WITHOUT_A_VOICE.capitalize()}, because {VOICE_VARIABLE} is not set."
        return plan_takes(inputs, targets, None, requests=requests, force=force), why
    provider = inputs.document.voice.provider
    digests = {
        segment.index: take_inputs(inputs, segment, provider=provider, voice_id=voice_id, model=model).digest
        for segment in targets
    }
    return plan_takes(inputs, targets, digests, requests=requests, force=force), None


def placeholder_plan(inputs: Inputs, targets: list[Segment], *, force: bool = False) -> list[TakePlan]:
    """What a run without voice does, whose placeholder takes are cached by content too."""
    digests = {segment.index: placeholder_inputs(inputs, segment).digest for segment in targets}
    return plan_takes(inputs, targets, digests, voiced=False, force=force)


def spend_of(plans: list[TakePlan], inputs: Inputs, *, state: SpendState) -> Spend:
    """What these plans cost at the stated rate, with what they can cost priced beside it.

    A section whose cache could not be checked may turn out to need a take, so its characters are
    counted into the ceiling and never into the price, and the gate is then given the figure the run
    certainly spends and the figure it can reach.
    """
    rate = inputs.settings.voice.price_per_1000_characters
    sending = [plan for plan in plans if plan.status is TakeStatus.VOICED and not plan.unchecked]
    maybe = [plan for plan in plans if plan.unchecked]
    characters = sum(plan.characters_sent for plan in sending)
    ceiling = characters + sum(plan.characters_sent for plan in maybe)
    return Spend(
        state=state,
        sections=tuple(plan.segment.index for plan in sending + maybe),
        characters=characters,
        dollars=round(dollars_for(characters, inputs), DOLLAR_DIGITS),
        ceiling_dollars=round(dollars_for(ceiling, inputs), DOLLAR_DIGITS),
        price_per_1000_characters=rate,
        price_layer=price_layer(inputs),
    )


__all__ = [
    "VOICE_VARIABLE",
    "TakePlan",
    "is_cached",
    "miss_reason",
    "placeholder_inputs",
    "placeholder_plan",
    "plan_takes",
    "requests_for",
    "speech_provider",
    "spend_of",
    "take_inputs",
    "voice_id_of",
    "voice_settings",
    "voiced_plan",
]
