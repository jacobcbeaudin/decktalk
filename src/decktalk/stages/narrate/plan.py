"""What a take is named by, and the refusals a run meets before it voices one.

A take is identified by its input digest and by nothing else, so two sections with the same words
share one take and renumbering or retitling a section moves no file and voices nothing. `TakeInputs`
is the whole of what that digest is taken over, so this module fills that model and never spells a
digest of its own.

The digest is over the provider's name, the voice id, the model, the output format and the take
identity its adapter declares, and the text, and none of those is a secret, so a take is named, and
a run priced, from each voice's provider without building the provider or reading its key. What a run
refuses before it voices anything is here too: a timed pause the model would drop, and a voiced run
with no voice named. A voiced take damaged in every place that holds it is refused by the take
state.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from decktalk.artifacts import PlaceholderInputs, TakeInputs
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.inputs.script import Reading, ScriptSection, Turn
from decktalk.machine.run import Run
from decktalk.settings import PROJECT_FILE, VOICES_TABLE
from decktalk.speech import SpeechProvider, VoiceInForce, canonical_text
from decktalk.stages import speech_context
from decktalk.tomlmap import variable


def take_inputs(turn: Reading, voice: VoiceInForce) -> TakeInputs:
    """Everything that decides what one voiced take of a turn sounds like, which is everything its name is over.

    It is the voice's values and the turn's text, and never the voice's name, so renaming a voice
    voices nothing and two names with one set of values share their takes.
    """
    return TakeInputs.of(
        provider=voice.provider,
        voice=voice.id,
        model=voice.model,
        output_format=voice.output.format,
        settings=dict(voice.identity),
        text=canonical_text(turn.pieces),
    )


def placeholder_inputs(inputs: Inputs, turn: Reading) -> PlaceholderInputs:
    """Everything that decides what one placeholder take sounds like, which is its pace and its beats."""
    cfg = inputs.settings.narration
    return PlaceholderInputs(
        words_per_minute=cfg.placeholder_words_per_minute,
        beat_seconds=cfg.placeholder_beat_seconds,
        text=canonical_text(turn.pieces),
    )


def speech_provider(run: Run, inputs: Inputs, voice: VoiceInForce) -> SpeechProvider:
    """The provider one voice names on the machine, built from the project's tuning and `.env`."""
    return run.machine.speech_providers.provider(voice.provider, speech_context(inputs, voice), voice=voice.name)


DROPPED_PAUSE_HINT = (
    "Pick a model that renders a timed pause, or take the [pause N] directions and break tags out of those sections."
)
"""What clears a timed pause the model would drop, which the refusal and the `check` finding both say."""


class Dropped(NamedTuple):
    """One turn holding a timed pause the voice that reads it would drop, with its section and that voice."""

    section: ScriptSection
    turn: Turn
    voice: VoiceInForce


def refuse_dropped_pauses(inputs: Inputs, buying: Sequence[tuple[ScriptSection, Turn]]) -> None:
    """Refuse a run that would buy a take whose timed pause its voice drops, before the price is asked for.

    `buying` is the turns the run would voice, each with its section, so a turn already on disk, or
    one this run leaves alone, never stops it. The refusal names the first voice that drops one and
    every section it would drop one in.
    """
    sending = {(section.number, turn.number) for section, turn in buying}
    sections = list({section.number: section for section, _turn in buying}.values())
    dropped = [one for one in dropped_pauses(inputs, sections) if (one.section.number, one.turn.number) in sending]
    if dropped:
        voice = dropped[0].voice
        numbers = list(dict.fromkeys(one.section.number for one in dropped if one.voice.name == voice.name))
        raise InputError(
            f"the voice {voice.name} on {voice.provider!r} renders no timed pause on model {voice.model!r}, so the "
            f"pauses in sections {numbers} would be dropped.",
            hint=DROPPED_PAUSE_HINT,
            location=at(inputs.script_path, inputs.root),
        )


def dropped_pauses(inputs: Inputs, sections: list[ScriptSection]) -> list[Dropped]:
    """Every turn holding a timed pause the voice that reads it would drop on its model.

    The provider declares which of its models render a timed pause. A beat is a dash every model
    reads, so only a turn with a timed pause can lose one.
    """
    return [
        Dropped(section, turn, voice)
        for section in sections
        for turn in section.turns
        if not (voice := inputs.voice_of(turn)).renders_pauses and any(piece.timed for piece in turn.pieces)
    ]


def voice_id_of(inputs: Inputs, voice: VoiceInForce) -> str:
    """The id one voice is read in, which is a published name and one of its takes' own inputs.

    Two voices reading one sentence are two different takes, so the id names the file. It is
    `[voices.NAME] id` in `decktalk.toml`, which `DECKTALK_VOICES_<NAME>_ID` overrides, and a voiced
    run that names neither is refused here before anything is bought.
    """
    if not voice.named:
        variable_name = id_variable(voice.name)
        raise InputError(
            f"no voice id is named for the voice {voice.name}, because neither [voices.{voice.name}] id in "
            f"{PROJECT_FILE} nor {variable_name} is set.",
            hint=(
                f'Add id = "<its voice id>" under [voices.{voice.name}] in {PROJECT_FILE}, or export {variable_name}, '
                "which is read from the environment and never from .env."
            ),
            location=at(inputs.root / PROJECT_FILE, inputs.root),
        )
    return voice.id


def id_variable(name: str) -> str:
    """The variable that overrides one voice's `[voices.NAME] id`, for anyone who keeps the id out of the file."""
    return variable(f"{VOICES_TABLE}.{name}.id")


def no_voice_note(voice: VoiceInForce) -> str:
    """The line a run that prices or plays takes says once for a voice with no id, naming where its id is set."""
    return (
        f"The voice {voice.name} has no id, so no take on disk can be matched to its turns, because "
        f"[voices.{voice.name}] id is empty and {id_variable(voice.name)} is not set."
    )


__all__ = [
    "DROPPED_PAUSE_HINT",
    "Dropped",
    "dropped_pauses",
    "id_variable",
    "no_voice_note",
    "placeholder_inputs",
    "refuse_dropped_pauses",
    "speech_provider",
    "take_inputs",
    "voice_id_of",
]
