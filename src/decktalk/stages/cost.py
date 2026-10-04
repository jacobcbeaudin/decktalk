"""What DeckTalk pays a provider for what a stage buys, priced once at the bill the provider declares.

A stage hands over what it buys, or would buy, as one `Buy` per take or sound request, counted every
way a provider could bill it: a take's characters and the seconds it is expected to run, a sound's
seconds. This module reads who is paid, the rate, the bill, the settings layer that stated the rate
and the units, so no stage works any of them out. A take is paid to `[voice] provider` at the bill
it declares, and a sound to `[score] provider` at its kind's rate per minute of audio.

`charge_of` is one request's exact figure, which a `take.charged` or `sound.charged` line carries.
`cost_of` prices one stage, or nothing: its price and its ceiling are its charges added as the
decimals they print as and rounded up to the cent, so a run that can pay anything never reads as
free and a host adding the charge lines comes to the same cent. `total` adds the stages of one run,
to the nearest cent, because each is rounded already. `is_free` says whether the voice bills at all,
which is the one place DeckTalk decides it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import cast

from decktalk.inputs import Inputs
from decktalk.page import SECOND_DIGITS
from decktalk.pipeline import Stage
from decktalk.results import (
    DOLLAR_DIGITS,
    BillingBasis,
    Cost,
    CostState,
    Layer,
    SectionNumber,
    SoundKind,
    StageCost,
    up_to_the_cent,
)
from decktalk.settings.layers import value_of
from decktalk.speech.sound import SOUND_DECLARED

SECONDS_PER_MINUTE = 60
"""Truth: every per-second bill, speech or sound, states its rate per minute of audio."""

SOUND_TABLES = {SoundKind.AMBIENCE: "ambience", SoundKind.EFFECT: "effects", SoundKind.MUSIC: "music"}
"""The `[score]` table each kind of sound is priced by."""

SOUND_RATE_KEY = "dollars_per_minute"
"""The key each kind's table states its rate under, whose layer decides whether a ceiling may refuse a run."""


@dataclass(frozen=True)
class Buy:
    """One take or one sound request a stage buys, or would buy, counted every way a provider could bill it.

    A buy with a `kind` is a sound, paid to `[score] provider`, and one without is a take, paid to
    `[voice] provider`. The stage counts both the characters and the seconds and never decides which is billed.
    """

    characters: int = 0
    seconds: float = 0.0
    sections: tuple[SectionNumber, ...] = ()
    certain: bool = True
    """False for a take that may already be on disk under a voice nobody named, which counts into the ceiling
    alone. A charged cost counts every buy."""
    kind: SoundKind | None = None
    """The kind of sound, whose `[score]` table states its rate, or None for a take."""

    def __post_init__(self) -> None:
        # loud: a count below zero is a programming error in a stage, never something an author typed.
        if self.characters < 0 or self.seconds < 0:
            raise ValueError("nothing bought is counted below zero.")


@dataclass(frozen=True)
class _Bill:
    """How one stage's provider bills, at what rate, under which key, and which layer stated it."""

    by: BillingBasis
    rate: float
    key: str | None
    layer: Layer


def _layer(inputs: Inputs, key: str | None) -> Layer:
    """Which layer stated the rate under `key`, because a ceiling may not guard a price nobody has stated."""
    if key is None:
        return Layer.DEFAULT
    try:
        return inputs.layers.winner(key).layer
    except KeyError:
        # silent: a price no layer states is the default's.
        return Layer.DEFAULT


def _bill(inputs: Inputs, kind: SoundKind | None) -> _Bill:
    """The bill a take, or a sound of `kind`, is priced at.

    A take is priced at the voice in force's bill, read at the key it names, and a sound at the score
    provider's.
    """
    if kind is None:
        voice = inputs.voice
        if voice.price_key is None:
            return _Bill(voice.billing, 0.0, None, Layer.DEFAULT)
        rate = float(cast("float", value_of(inputs.settings, voice.price_key)))
        return _Bill(voice.billing, rate, voice.price_key, _layer(inputs, voice.price_key))
    if inputs.settings.score.provider not in SOUND_DECLARED:
        return _Bill(BillingBasis.UNDECLARED, 0.0, None, Layer.DEFAULT)
    table = SOUND_TABLES[kind]
    key = f"score.{table}.{SOUND_RATE_KEY}"
    rate = float(getattr(getattr(inputs.settings.score, table), SOUND_RATE_KEY))
    return _Bill(BillingBasis.PER_SECOND, rate, key, _layer(inputs, key))


def charge_of(inputs: Inputs, buy: Buy) -> float | None:
    """What one request costs at its provider's bill, unrounded, or None when that provider bills nothing.

    The count and the rate are read as the decimals they print as and multiplied exactly, and the
    result is given as one float, the figure a `take.charged` or `sound.charged` line carries. It is
    never rounded, because a ledger that adds rounded cents per request drifts from the price the run
    was approved at. A bill nobody declared charges 0.0, and a free one None, because every charge
    line is money paid. Settling a sum of these to the nanodollar is exact while a rate has at most 4
    decimals, which `up_to_the_cent` relies on.
    """
    bill = _bill(inputs, buy.kind)
    if bill.by is BillingBasis.FREE:
        return None
    if bill.by is BillingBasis.PER_CHARACTER:
        return float(Decimal(repr(buy.characters)) * Decimal(repr(bill.rate)) / 1000)
    if bill.by is BillingBasis.PER_SECOND:
        return float(Decimal(repr(buy.seconds)) * Decimal(repr(bill.rate)) / SECONDS_PER_MINUTE)
    return 0.0


def is_free(inputs: Inputs) -> bool:
    """Whether the voice in force declares that it bills nothing, which lets a run that may not spend call it.

    Free is what the provider declares and never a rate of zero, because a zero rate on a voice that
    bills is somebody's statement about their plan, and a cap or a question still guards it.
    """
    return inputs.voice.billing is BillingBasis.FREE


def cost_of(inputs: Inputs, buys: Iterable[Buy] = (), *, state: CostState = CostState.ESTIMATE) -> Cost:
    """What one stage's buys cost at its provider's bill, or the price of nothing when it buys nothing.

    An estimate counts a buy that is not certain into the ceiling alone, and a charged cost counts
    every buy as bought. The price and the ceiling are the buys' `charge_of` figures added exactly and
    rounded up to the cent, so a price whose every buy is certain has a ceiling equal to it. The
    characters and the seconds count the buys the price is for. Several sound kinds bought at their
    own rates give the rate their exact sum comes to per minute, `averaged`, and the key and layer of
    the rate least surely stated. The price of nothing is billed the way the voice in force bills, so
    a reader always finds a rate, and has no stage row. Any other price is one stage's, and holds that
    stage's one row. Takes and sounds are two stages, so one cost never holds both.
    """
    bought = list(buys)
    if not bought:
        return _nothing(inputs, state)
    if len({buy.kind is None for buy in bought}) > 1:
        raise ValueError("one cost prices one stage, so takes and sounds are priced apart and added by total.")
    return _stage(inputs, bought, state)


def _nothing(inputs: Inputs, state: CostState) -> Cost:
    """The price of buying nothing, billed and rated the way the voice in force bills."""
    bill = _bill(inputs, None)
    return Cost(
        state=state,
        sections=(),
        characters=0,
        seconds=0.0,
        dollars=0.0,
        ceiling_dollars=0.0,
        billing=bill.by,
        dollars_per_1000_characters=bill.rate if bill.by is BillingBasis.PER_CHARACTER else 0.0,
        dollars_per_minute=bill.rate if bill.by is BillingBasis.PER_SECOND else 0.0,
        price_key=bill.key,
        averaged=False,
        price_layer=bill.layer,
        stages=(),
    )


def _stage(inputs: Inputs, bought: Sequence[Buy], state: CostState) -> Cost:
    """What one stage's buys cost, which `cost_of` has checked are all takes or all sounds."""
    charged = state is CostState.CHARGED
    certain = [buy for buy in bought if charged or buy.certain]
    possible = [buy for buy in bought if not charged and not buy.certain]
    sections = tuple(dict.fromkeys(number for buy in certain + possible for number in buy.sections))
    charges = {id(buy): charge_of(inputs, buy) or 0.0 for buy in bought}
    bills = {buy.kind: _bill(inputs, buy.kind) for buy in bought}
    bill = _named(list(bills.values()))
    rates = {each.rate for each in bills.values()}
    averaged = bill.by is BillingBasis.PER_SECOND and len(rates) > 1
    rate = _averaged(certain, charges) if averaged else bill.rate
    row = StageCost(
        stage=Stage.NARRATE if bought[0].kind is None else Stage.SCORE,
        state=state,
        sections=sections,
        characters=sum(buy.characters for buy in certain),
        seconds=round(sum(buy.seconds for buy in certain), SECOND_DIGITS),
        dollars=up_to_the_cent(charges[id(buy)] for buy in certain),
        ceiling_dollars=up_to_the_cent(charges[id(buy)] for buy in certain + possible),
        billing=bill.by,
        dollars_per_1000_characters=rate if bill.by is BillingBasis.PER_CHARACTER else 0.0,
        dollars_per_minute=rate if bill.by is BillingBasis.PER_SECOND else 0.0,
        price_key=bill.key,
        averaged=averaged,
        price_layer=bill.layer,
    )
    return Cost(**row.model_dump(exclude={"stage"}), stages=(row,))


def _named(bills: Sequence[_Bill]) -> _Bill:
    """The bill a stage is said to be priced at: the one whose rate is least surely stated, first on a tie."""
    order = list(Layer)
    return min(bills, key=lambda bill: order.index(bill.layer))


def _averaged(certain: Sequence[Buy], charges: dict[int, float]) -> float:
    """The rate per minute of audio that several kinds' charges come to together, from their exact sums."""
    exact = sum((Decimal(repr(charges[id(buy)])) for buy in certain), Decimal(0))
    seconds = sum((Decimal(repr(buy.seconds)) for buy in certain), Decimal(0))
    return float(exact / seconds * SECONDS_PER_MINUTE) if seconds else 0.0


def total(costs: Sequence[Cost]) -> Cost:
    """What the whole run costs, which is every stage's cost added together.

    It is the one way a run's price is summed, so the price a build asks about before it buys and
    the price its result reports after are the same sum of the same stages. A charged total counts
    only the stages that had leave to buy, so a stage that only priced what it would buy is never
    reported as spent. One stage counted is the total as it is. Every stage's row is kept in
    `stages`, counted or not, in pipeline order, so the price of a stage that was not counted is
    still there to read, and one stage in two of the costs is refused.

    The total is billed the way the counted stages that buy something at a price bill, so a free voice
    beside a paid score leaves the sound's bill and rate to the total. When those are one bill, its rate
    is the total's. When a bill per character meets a bill per second the total is `mixed` and carries
    each rate, and the rate it names is the first one no layer stated, or the first when every one is
    stated. A stage whose bill nobody declared makes the whole total undeclared, because no part of
    DeckTalk can price it. The total adds only what each stage is billed on: the characters of a
    per-character stage and the seconds of a per-second one, and a free or undeclared stage's counts as
    they are. Its figures are the stages' rounded figures added to the nearest cent and never rounded up
    again.
    """
    if not costs:
        raise ValueError("a total adds at least one cost.")
    rows = _rows(costs)
    state = CostState.CHARGED if any(cost.state is CostState.CHARGED for cost in costs) else CostState.ESTIMATE
    counted = [cost for cost in costs if cost.state is state]
    if len(counted) == 1:
        return counted[0].model_copy(update={"stages": rows})
    buying = [cost for cost in counted if cost.buys] or counted
    deciding = [cost for cost in buying if not cost.free] or buying
    bills = list(dict.fromkeys(cost.billing for cost in deciding))
    if BillingBasis.UNDECLARED in bills:
        bills = [BillingBasis.UNDECLARED]
    first = {bill: next(cost for cost in deciding if cost.billing is bill) for bill in bills}
    unstated = [cost for cost in deciding if cost.price_layer is Layer.DEFAULT]
    named = (unstated or deciding)[0]
    per_character, per_second = first.get(BillingBasis.PER_CHARACTER), first.get(BillingBasis.PER_SECOND)
    return Cost(
        state=state,
        sections=tuple(sorted({number for cost in counted for number in cost.sections})),
        characters=sum(cost.characters for cost in counted if cost.billing not in _SECONDS_ALONE),
        seconds=round(sum(cost.seconds for cost in counted if cost.billing not in _CHARACTERS_ALONE), SECOND_DIGITS),
        dollars=round(sum(cost.dollars for cost in counted), DOLLAR_DIGITS),
        ceiling_dollars=round(sum(cost.ceiling_dollars for cost in counted), DOLLAR_DIGITS),
        billing=bills[0] if len(bills) == 1 else BillingBasis.MIXED,
        dollars_per_1000_characters=per_character.dollars_per_1000_characters if per_character else 0.0,
        dollars_per_minute=per_second.dollars_per_minute if per_second else 0.0,
        price_key=named.price_key,
        averaged=any(cost.averaged for cost in deciding),
        price_layer=named.price_layer,
        stages=rows,
    )


def _rows(costs: Sequence[Cost]) -> tuple[StageCost, ...]:
    """Every stage row of these costs, counted or not, in pipeline order, each stage at most once."""
    found: dict[Stage, StageCost] = {}
    for row in (row for cost in costs for row in cost.stages):
        if row.stage in found:
            raise ValueError(f"{row.stage.value} is in two of the costs added, which would count it twice.")
        found[row.stage] = row
    return tuple(found[stage] for stage in Stage if stage in found)


_CHARACTERS_ALONE = frozenset({BillingBasis.PER_CHARACTER})
"""The bills a total counts only the characters of, because the seconds beside them are not billed."""

_SECONDS_ALONE = frozenset({BillingBasis.PER_SECOND})
"""The bills a total counts only the seconds of, because the characters beside them are not billed."""


__all__ = ["Buy", "charge_of", "cost_of", "is_free", "total"]
