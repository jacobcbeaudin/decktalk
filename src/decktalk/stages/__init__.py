"""The pipeline, one package per stage and one module per call that reports or cuts.

    narrate/     the script becomes one take per section, with a time for every word
    cue/         every cue phrase becomes a second on its own section's clock
    record/      each page section is recorded against those seconds
    score/       the music, the ambience bed and the effects are generated
    assemble/    the recordings, the narration and the score become one film
    verify/      the finished film is measured against the clock it was promised
    build.py     the six stages in order, or the span of them a caller named
    check.py     what a build would spend and show, judged before anything is spent
    status.py    what is written, what is built, what is stale and what to do next
    words.py     every spoken word with its span, which is how a cue phrase is written
    storyboard.py  every slide at every cue, frozen onto one page
    clip.py      a span of one built section, cut into its own file

Every one of them satisfies the same convention: the module named after the call holds a function
of that name, taking the project's `Inputs` and the `Run` the facade opened, and returning the
result model named after it. That is the whole seam between `project.py` and the stages, so a test
fakes a stage by replacing one attribute and no stage ever sees a project, a machine or a run
opener.

A stage therefore cannot read the environment and cannot print. It reports through the run: one
sentence with `run.note`, one judgement with `run.found`, one count with `run.progress`, one file
with `run.wrote`, and one price with `run.approve` before anything is bought. It asks `run.check`
between sections, so a caller that cancelled a run stops it inside the section it was in rather
than at the end.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from decktalk.inputs import Inputs
from decktalk.results import Billing, Layer
from decktalk.speech import DECLARED, VoiceContext, base_of, billing_of, table_of

CHARACTERS_PER_PRICE = 1000
"""Truth: a per-character rate is stated per thousand characters, which is how a declared rate key reads."""

SECONDS_PER_PRICE = 1
"""Truth: a per-second rate is stated per second of audio, which is how a declared rate key reads."""

SECTION_START_SECONDS = 0.0
"""Where a section's own clock begins, which is when its first slide is already on screen."""


def voice_model(inputs: Inputs) -> str:
    """The model that reads this project, which is `[voice] model` or the provider's own default.

    The default is read from the provider's own table, so changing `[voice] provider` never sends one
    vendor's model id to another, and a provider with no table is sent no model it did not ask for.
    """
    voice = inputs.settings.voice
    table = table_of(inputs.settings, voice.provider)
    return voice.model or (table.model if table is not None else "")


def price_key(provider: str) -> str | None:
    """The dotted key that states `provider`'s rate, or None for a provider whose bill has no rate to state."""
    declared, rate = DECLARED.get(provider), billing_of(provider).rate
    return f"{declared.table}.{rate}" if declared is not None and rate is not None else None


def rate_of(inputs: Inputs, provider: str | None = None) -> float:
    """What `provider` charges per unit it bills by, which is `[voice] provider`'s rate unless one is named.

    The unit is the one its adapter declares: 1,000 characters for a per-character bill and a second
    of audio for a per-second one. A free voice, and a provider that declares no bill, charge nothing.
    """
    name = provider or inputs.settings.voice.provider
    rate, table = billing_of(name).rate, table_of(inputs.settings, name)
    return float(getattr(table, rate)) if rate is not None and table is not None else 0.0


def dollars_for(amount: float, inputs: Inputs, provider: str | None = None) -> float:
    """What this much of what the provider bills by costs at its stated rate, unrounded.

    `amount` is counted in what its adapter declares it bills by: characters for a per-character
    bill, seconds of audio for a per-second one. One take's charge is stated at full precision,
    because a ledger that adds rounded cents per take drifts from the run's own total, which is
    rounded once, after the sum.
    """
    name = provider or inputs.settings.voice.provider
    per = CHARACTERS_PER_PRICE if billing_of(name).by is Billing.PER_CHARACTER else SECONDS_PER_PRICE
    return amount / per * rate_of(inputs, name)


def billed(characters: int, seconds: float, provider: str) -> float:
    """Which of a take's characters and seconds `provider`'s bill counts, which is nothing for a voice with no rate."""
    by = billing_of(provider).by
    return characters if by is Billing.PER_CHARACTER else seconds if by is Billing.PER_SECOND else 0.0


def price_layer(inputs: Inputs, provider: str | None = None) -> Layer:
    """Which layer stated the rate, because a ceiling may not guard a price nobody has stated."""
    key = price_key(provider or inputs.settings.voice.provider)
    if key is None:
        # silent: a provider whose bill has no rate to state has the default's rate of nothing.
        return Layer.DEFAULT
    try:
        return inputs.layers.winner(key).layer
    except KeyError:
        # silent: a price no layer states is the default's.
        return Layer.DEFAULT


def rate_fields(inputs: Inputs, provider: str | None = None) -> dict[str, Any]:
    """The fields of a `Spend` that say how `provider` bills, at what rate, and who stated it."""
    name = provider or inputs.settings.voice.provider
    by, rate = billing_of(name).by, rate_of(inputs, name)
    return {
        "billing": by,
        "price_per_1000_characters": rate if by is Billing.PER_CHARACTER else 0.0,
        "price_per_second": rate if by is Billing.PER_SECOND else 0.0,
        "price_key": price_key(name),
        "price_layer": price_layer(inputs, name),
    }


def voice_context(inputs: Inputs, provider: str | None = None) -> VoiceContext:
    """What a speech provider is built from, taken from its own table, this project's tuning and its own `.env`.

    The base URL is read from the provider's own table under the key its adapter declares, so a
    provider with no table is handed none.
    """
    settings = inputs.settings
    return VoiceContext(
        secrets=inputs.env,
        api_base=base_of(settings, provider or settings.voice.provider),
        context_chars=settings.narration.context_chars,
        speech_timeout_seconds=settings.narration.timeout_seconds,
    )


def selects(only: Sequence[int] | None) -> Callable[[int], bool]:
    """Whether one section number is in this run's selection, which is every section when it names none."""
    numbers = set(only or ())
    return lambda number: not numbers or number in numbers


__all__ = [
    "CHARACTERS_PER_PRICE",
    "SECONDS_PER_PRICE",
    "SECTION_START_SECONDS",
    "billed",
    "dollars_for",
    "price_key",
    "price_layer",
    "rate_fields",
    "rate_of",
    "selects",
    "voice_context",
    "voice_model",
]
