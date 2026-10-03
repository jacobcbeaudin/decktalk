"""What a take is named by, and the refusals a run meets before it voices one.

A take is identified by its input digest and by nothing else, so two sections with the same words
share one take and renumbering or retitling a section moves no file and voices nothing. `TakeInputs`
is the whole of what that digest is taken over, so this module fills that model and never spells a
digest of its own.

The digest is over the provider's name, the voice id, the model, the output format and the take
identity its adapter declares, and the text, and none of those is a secret, so a take is named, and
a run priced, from `[voice] provider` without building the provider or reading its key. What a run
refuses before it voices anything is here too: a voiced take damaged in every place that holds it, a
timed pause the model would drop, and a purchase with no voice named.
"""

from __future__ import annotations

from typing import Any

from decktalk.artifacts import PlaceholderInputs, TakeInputs
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.inputs.script import ScriptSection
from decktalk.machine.run import Run
from decktalk.settings import BY_ID, PROJECT_FILE
from decktalk.speech import DECLARED, SpeechProvider, canonical_text, output_of, renders_pauses, table_of
from decktalk.stages import speech_context


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


def take_inputs(inputs: Inputs, section: ScriptSection, *, provider: str, voice_id: str, model: str) -> TakeInputs:
    """Everything that decides what one voiced take sounds like, which is everything its name is over."""
    return TakeInputs.of(
        provider=provider,
        voice=voice_id,
        model=model,
        output_format=output_of(inputs.settings, provider).format,
        settings=take_identity(inputs),
        text=canonical_text(section.pieces),
    )


def placeholder_inputs(inputs: Inputs, section: ScriptSection) -> PlaceholderInputs:
    """Everything that decides what one placeholder take sounds like, which is its pace and its beats."""
    cfg = inputs.settings.narration
    return PlaceholderInputs(
        words_per_minute=cfg.placeholder_words_per_minute,
        beat_seconds=cfg.placeholder_beat_seconds,
        text=canonical_text(section.pieces),
    )


def speech_provider(run: Run, inputs: Inputs) -> SpeechProvider:
    """The provider `[voice] provider` names on the machine, built from the project's tuning and `.env`."""
    return run.machine.speech_providers.provider(inputs.settings.voice.provider, speech_context(inputs))


DROPPED_PAUSE_HINT = (
    "Pick a model that renders a timed pause, or take the [pause N] directions and break tags out of those sections."
)
"""What clears a timed pause the model would drop, which the refusal and the `check` finding both say."""


def refuse_dropped_pauses(inputs: Inputs, buying: list[ScriptSection], *, model: str) -> None:
    """Refuse a run that would buy a take whose timed pause the model drops, before the price is asked for.

    `buying` is the sections the run would voice, so a section already on disk, or one this run
    leaves alone, never stops it.
    """
    dropped = dropped_pauses(inputs, buying, model=model)
    if dropped:
        raise InputError(
            f"[voice] provider {inputs.settings.voice.provider!r} renders no timed pause on model {model!r}, so "
            f"the pauses in sections {[section.number for section in dropped]} would be dropped.",
            hint=DROPPED_PAUSE_HINT,
            location=at(inputs.script_path, inputs.root),
        )


def dropped_pauses(inputs: Inputs, sections: list[ScriptSection], *, model: str) -> list[ScriptSection]:
    """Every section holding a timed pause that `[voice] provider` would drop on this model.

    The provider declares which of its models render a timed pause. A beat is a dash every model
    reads, so only a section with a timed pause can lose one.
    """
    if renders_pauses(inputs.settings.voice.provider, model):
        return []
    return [section for section in sections if any(piece.timed for piece in section.pieces)]


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

NO_VOICE_NOTE = (
    f"No voice is named, so no take on disk can be matched, because [voice] id is empty and {VOICE_ID_VARIABLE} "
    "is not set."
)
"""The line a run that prices or plays takes with no voice named says once, naming where the voice is set."""


__all__ = [
    "NO_VOICE_NOTE",
    "UNNAMED",
    "VOICE_ID_VARIABLE",
    "DROPPED_PAUSE_HINT",
    "dropped_pauses",
    "refuse_dropped_pauses",
    "named_voice",
    "placeholder_inputs",
    "speech_provider",
    "take_inputs",
    "voice_id_of",
    "take_identity",
]
