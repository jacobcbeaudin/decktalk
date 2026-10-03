"""What every stage that buys costs: the bill, the rate, the layer, the rounding and the total, held in one place.

Every case prices real inputs loaded from a project file, so a rate is read the way a run reads it.
A free bill is read by naming the voice DeckTalk ships that bills nothing, an undeclared one by naming a
host's voice, and a per-second one, which no shipped voice declares, by replacing the voice in force on one
project's inputs. The invariants are held over many runs at once with Hypothesis, each bill named outside
the search.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from decktalk.inputs import Inputs
from decktalk.pipeline import Stage
from decktalk.results import BillingBasis, Cost, CostState, Layer, SoundKind, money, rate_money, up_to_the_cent
from decktalk.stages.cost import Buy, charge_of, cost_of, is_free, total
from support.costs import a_cost
from support.fakes import FREE_VOICE_NAME
from support.projects import MINIMAL_TOML, load_project

SPEECH = MINIMAL_TOML + "\n[elevenlabs]\ndollars_per_1000_characters = 0.3\n"
"""A project whose voice states 30 cents per 1,000 characters."""

SOUND = SPEECH + "\n[score.effects]\ndollars_per_minute = 0.12\n"
"""The same project with effects stated at twelve cents a minute of audio."""

SCORE = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"
with_ambience = true

[[section]]
number = 2
page = "deck/index.html"
scene = "2"

[mix]
music = "build/score/music.mp3"

[[mix.effect]]
file = "score/chime.mp3"
section = 1
cue = "1.1:open"

[score.ambience]
prompt = "a quiet room"

[score.effects.chime]
prompt = "a bright chime"

[score.music]
prompt = "warm strings"
duration_seconds = 30
"""
"""The score test project: a bed, one effect and thirty seconds of music."""

RATED = (
    SCORE.replace(
        "[score.ambience]\n",
        "[score.ambience]\ndollars_per_minute = 0.6\n",
    )
    .replace(
        "[score.effects.chime]",
        "[score.effects]\ndollars_per_minute = 1.2\n\n[score.effects.chime]",
    )
    .replace(
        "duration_seconds = 30\n",
        "duration_seconds = 30\ndollars_per_minute = 0.3\n",
    )
)
"""The score project with a rate stated for each kind: 60 cents, $1.20 and 30 cents a minute."""

EFFECT = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[score.effects]
dollars_per_minute = 0.12

[score.effects.whoosh]
prompt = "a whoosh"
duration_seconds = 10
"""
"""One ten-second effect at twelve cents a minute, which is two cents."""

HOUSE_VOICE = '\n[voice]\nprovider = "house"\n'
"""What names a voice DeckTalk does not ship, which a host registers itself."""

HOUSE_SOUND = '\n[score]\nprovider = "house"\n'
"""What names a sound provider DeckTalk does not ship, which a host registers itself."""

THE_RATED_SCORE = (
    Buy(seconds=25.0, sections=(1, 2), kind=SoundKind.AMBIENCE),
    Buy(seconds=0.5, sections=(1, 2), kind=SoundKind.EFFECT),
    Buy(seconds=30.0, sections=(1, 2), kind=SoundKind.MUSIC),
)
"""What the score project asks for: 25 s of bed, half a second of effect and thirty seconds of music."""

TAKES = (Buy(characters=39, seconds=3.8, sections=(1,)), Buy(characters=21, seconds=2.0, sections=(2,)))
"""Two takes of 39 and 21 characters, which run 3.8 and 2 seconds."""


def project(tmp_path: Path, toml: str) -> Inputs:
    """A project of this file, loaded the way a stage is handed one."""
    return load_project(tmp_path, toml, environ={})


FREE_VOICE = f'\n[voice]\nprovider = "{FREE_VOICE_NAME}"\n'
"""What names the voice DeckTalk ships that bills nothing."""


def an_effect(seconds: float, *, sections: tuple[int, ...] = (1,)) -> Buy:
    """One effect request of this many seconds."""
    return Buy(seconds=seconds, sections=sections, kind=SoundKind.EFFECT)


# ---- the bill and the rate ----------------------------------------------------------------------


def test_a_buy_counted_below_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="nothing bought is counted below zero"):
        Buy(characters=-1)
    with pytest.raises(ValueError, match="nothing bought is counted below zero"):
        Buy(seconds=-0.5, kind=SoundKind.EFFECT)


def test_takes_and_sounds_are_priced_apart(tmp_path: Path) -> None:
    """A take and a sound are two stages, two providers and two bills, which `total` adds."""
    with pytest.raises(ValueError, match="one cost prices one stage"):
        cost_of(project(tmp_path, SOUND), [Buy(characters=10), an_effect(1.0)])


@pytest.mark.parametrize(
    ("toml", "take", "sound"),
    [
        (SOUND + HOUSE_VOICE, BillingBasis.UNDECLARED, BillingBasis.PER_SECOND),
        (SOUND + HOUSE_SOUND, BillingBasis.PER_CHARACTER, BillingBasis.UNDECLARED),
    ],
    ids=["a-host-voice", "a-host-sound-provider"],
)
def test_a_take_is_paid_to_the_voice_and_a_sound_to_the_scores_provider(
    tmp_path: Path, toml: str, take: BillingBasis, sound: BillingBasis
) -> None:
    inputs = project(tmp_path, toml)
    assert cost_of(inputs, [Buy(characters=10)]).billing is take
    assert cost_of(inputs, [an_effect(10.0)]).billing is sound


VOICE_BILLS = (BillingBasis.PER_CHARACTER, BillingBasis.PER_SECOND, BillingBasis.FREE, BillingBasis.UNDECLARED)
"""Every bill a voice can declare, which a host's own voice leaves undeclared."""


def a_voice_billed(tmp_path: Path, bill: BillingBasis) -> Inputs:
    """The speech project with its voice billed this way.

    A free voice is the one DeckTalk ships that bills nothing and an undeclared one is a host's own. No
    shipped voice bills per second, so that bill replaces the one the voice in force declares, at the rate
    its own table states, read per minute.
    """
    if bill is BillingBasis.PER_SECOND:
        inputs = project(tmp_path, SPEECH)
        return replace(inputs, voice=replace(inputs.voice, billing=BillingBasis.PER_SECOND))
    if bill is BillingBasis.FREE:
        return project(tmp_path, SPEECH + FREE_VOICE)
    return project(tmp_path, SPEECH + HOUSE_VOICE if bill is BillingBasis.UNDECLARED else SPEECH)


@pytest.mark.parametrize("bill", VOICE_BILLS)
def test_a_take_is_priced_at_the_bill_its_provider_declares(tmp_path: Path, bill: BillingBasis) -> None:
    spend = cost_of(a_voice_billed(tmp_path, bill), TAKES)
    assert spend.billing is bill
    assert spend.state is CostState.ESTIMATE
    assert spend.sections == (1, 2)
    assert (spend.characters, spend.seconds) == (60, 5.8)
    assert spend.ceiling_dollars == spend.dollars
    if bill is BillingBasis.PER_CHARACTER:
        assert spend.dollars == up_to_the_cent([39 / 1000 * 0.30, 21 / 1000 * 0.30]) == 0.02
        assert spend.price_key == "elevenlabs.dollars_per_1000_characters"
        assert spend.price_layer is Layer.PROJECT
        assert "per 1,000 characters" in spend.sentence
    elif bill is BillingBasis.PER_SECOND:
        assert spend.dollars == up_to_the_cent([3.8 * 0.30 / 60, 2.0 * 0.30 / 60]) == 0.03
        assert spend.dollars_per_minute == 0.30 and spend.dollars_per_1000_characters == 0
        assert "per minute of audio" in spend.sentence and "1,000 characters" not in spend.sentence
    elif bill is BillingBasis.FREE:
        assert spend.free
        assert spend.dollars == 0 and spend.price_key is None
        assert spend.sentence.endswith("for nothing, because the voice is free.")
    else:
        assert spend.dollars == 0 and spend.price_key is None and spend.price_layer is Layer.DEFAULT
        assert not spend.free
        assert "declares no bill" in spend.sentence


def test_the_rate_is_the_one_the_providers_own_table_states(tmp_path: Path) -> None:
    inputs = project(tmp_path, SPEECH)
    assert charge_of(inputs, Buy(characters=2000)) == 0.6
    assert cost_of(inputs).price_layer is Layer.PROJECT


def test_a_voice_with_no_table_is_priced_at_a_rate_nobody_stated(tmp_path: Path) -> None:
    """Changing `[voice] provider` never carries ElevenLabs's rate into another voice's price."""
    inputs = project(tmp_path, MINIMAL_TOML + HOUSE_VOICE)
    nothing = cost_of(inputs)
    assert nothing.dollars_per_1000_characters == nothing.dollars_per_minute == 0.0
    assert nothing.price_layer is Layer.DEFAULT
    assert charge_of(inputs, Buy(characters=100)) == 0.0


def test_a_price_nobody_stated_is_reported_as_the_default(tmp_path: Path) -> None:
    """`--max-cost` refuses while the price is the default, so the layer has to travel with it."""
    spend = cost_of(project(tmp_path, MINIMAL_TOML), [Buy(characters=7, sections=(1,))])
    assert spend.price_layer is Layer.DEFAULT
    assert spend.price_key == "elevenlabs.dollars_per_1000_characters"
    assert spend.dollars_per_1000_characters == pytest.approx(0.0)


def test_a_ten_second_effect_is_priced_per_second_at_the_rate_its_table_states(tmp_path: Path) -> None:
    priced = cost_of(project(tmp_path, EFFECT), [an_effect(10.0)])
    assert priced.dollars == priced.ceiling_dollars == 0.02
    assert (priced.billing, priced.seconds, priced.characters) == (BillingBasis.PER_SECOND, 10.0, 0)
    assert priced.dollars_per_minute == 0.12
    assert (priced.price_key, priced.price_layer, priced.averaged) == (
        "score.effects.dollars_per_minute",
        Layer.PROJECT,
        False,
    )
    assert priced.sections == (1,)
    assert priced.sentence == "This run costs $0.02 for about 10 seconds of audio at $0.12 per minute of audio."


@pytest.mark.parametrize("typed", [0.12, 0.3, 0.07, 1.2, 0.005, 99.99])
def test_the_rate_a_cost_prints_is_the_rate_the_author_typed(tmp_path: Path, typed: float) -> None:
    """A rate is typed and printed in dollars per minute, so a reader can check one against the other."""
    toml = EFFECT.replace("dollars_per_minute = 0.12", f"dollars_per_minute = {typed}")
    priced = cost_of(project(tmp_path, toml), [an_effect(10.0)])
    assert priced.dollars_per_minute == typed
    assert priced.dollars == up_to_the_cent([10 * typed / 60])
    assert priced.rate == f"{rate_money(typed)} per minute of audio"
    assert f'"dollars_per_minute":{typed}' in priced.model_dump_json()


def test_kinds_bought_at_different_rates_are_priced_exactly_and_said_to_average(tmp_path: Path) -> None:
    """The sum is exact, and the one rate a price states is what that sum comes to per minute, said as an average."""
    priced = cost_of(project(tmp_path, RATED), THE_RATED_SCORE)
    assert priced.seconds == 55.5
    assert priced.averaged
    assert priced.dollars_per_minute * priced.seconds / 60 == pytest.approx(0.41)
    assert priced.sentence == (
        "This run costs $0.41 for about 56 seconds of audio at an average of $0.44 per minute of audio."
    )


def test_the_price_is_every_requested_second_at_its_own_kinds_rate(tmp_path: Path) -> None:
    """25 s of ambience at a cent, 0.5 s of effect at two cents and 30 s of music at half a cent."""
    priced = cost_of(project(tmp_path, RATED), THE_RATED_SCORE)
    assert priced.dollars == priced.ceiling_dollars == round(0.25 + 0.01 + 0.15, 2)
    assert priced.state is CostState.ESTIMATE
    assert priced.sections == (1, 2)


def test_what_speech_costs_prices_no_sound(tmp_path: Path) -> None:
    """Sound is billed by the second of audio, so the speech rate per character is never read for it."""
    toml = SCORE.replace("[score.ambience]", "[elevenlabs]\ndollars_per_1000_characters = 100.0\n\n[score.ambience]")
    priced = cost_of(project(tmp_path, toml), THE_RATED_SCORE)
    assert priced.dollars == 0
    assert priced.price_layer is Layer.DEFAULT


def test_a_rate_one_kind_leaves_unstated_is_a_price_nobody_stated(tmp_path: Path) -> None:
    """A cap may not guard a run whose every rate is not stated, so one default rate makes the price a default."""
    stated = RATED.replace("dollars_per_minute = 0.3\n", "")
    assert cost_of(project(tmp_path / "unstated", stated), THE_RATED_SCORE).price_layer is Layer.DEFAULT
    assert cost_of(project(tmp_path / "rated", RATED), THE_RATED_SCORE).price_layer is Layer.PROJECT


def test_a_price_no_layer_records_is_the_default_price_and_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The layer table may hold no row for the price, which every stage reads as the default."""
    inputs = project(tmp_path, SOUND)

    def unstated(_layers: object, key: str) -> None:
        raise KeyError(key)

    monkeypatch.setattr(type(inputs.layers), "winner", unstated)
    assert cost_of(inputs, TAKES).price_layer is Layer.DEFAULT
    assert cost_of(inputs, [an_effect(10.0)]).price_layer is Layer.DEFAULT


def test_a_sound_from_a_provider_a_host_registered_is_undeclared(tmp_path: Path) -> None:
    """DeckTalk knows no rate for a sound provider it does not ship, so it prices nothing and states no key."""
    inputs = project(tmp_path, SOUND + HOUSE_SOUND)
    priced = cost_of(inputs, [an_effect(10.0)])
    assert priced.billing is BillingBasis.UNDECLARED
    assert (priced.dollars, priced.dollars_per_minute, priced.price_key) == (0.0, 0.0, None)
    assert (priced.price_layer, priced.averaged, priced.seconds) == (Layer.DEFAULT, False, 10.0)
    assert charge_of(inputs, an_effect(10.0)) == 0.0


# ---- the shape of a price -----------------------------------------------------------------------


def test_a_cost_of_nothing_names_the_voices_rate_and_layer(tmp_path: Path) -> None:
    """A reader always finds a rate, so the price of nothing is billed the way the voice bills."""
    inputs = project(tmp_path, SPEECH)
    nothing = cost_of(inputs)
    assert not nothing.buys
    assert (nothing.dollars, nothing.ceiling_dollars, nothing.characters, nothing.seconds) == (0, 0, 0, 0)
    assert nothing.billing is BillingBasis.PER_CHARACTER
    assert nothing.dollars_per_1000_characters == 0.3
    assert (nothing.price_key, nothing.price_layer) == ("elevenlabs.dollars_per_1000_characters", Layer.PROJECT)
    assert nothing.sentence == "This run buys nothing."
    assert cost_of(inputs, state=CostState.CHARGED).sentence == "This run bought nothing."


def test_a_possible_take_moves_the_ceiling_and_never_the_price(tmp_path: Path) -> None:
    """A take that may already be on disk counts into the ceiling alone, and its section after the certain ones."""
    possible = Buy(characters=100, seconds=8.0, sections=(1,), certain=False)
    priced = cost_of(project(tmp_path, SPEECH), [Buy(characters=39, seconds=3.8, sections=(3,)), possible])
    assert priced.dollars == 0.02
    assert priced.ceiling_dollars == up_to_the_cent([0.0117, 0.03]) == 0.05
    assert (priced.characters, priced.seconds) == (39, 3.8)
    assert priced.sections == (3, 1)


def test_a_charged_cost_counts_every_buy_as_bought(tmp_path: Path) -> None:
    possible = Buy(characters=100, seconds=8.0, sections=(1,), certain=False)
    charged = cost_of(
        project(tmp_path, SPEECH), [Buy(characters=39, seconds=3.8, sections=(3,)), possible], state=CostState.CHARGED
    )
    assert charged.state is CostState.CHARGED
    assert charged.dollars == charged.ceiling_dollars == 0.05
    assert (charged.characters, charged.sections) == (139, (3, 1))


# ---- the total ----------------------------------------------------------------------------------


def test_a_total_of_speech_and_sound_says_it_is_mixed_and_counts_both(tmp_path: Path) -> None:
    """Characters and seconds are two bills, so the total names neither one's rate as the whole run's."""
    inputs = project(tmp_path, SOUND)
    whole = total(
        [cost_of(inputs, [Buy(characters=1000, seconds=70.0, sections=(1,))]), cost_of(inputs, [an_effect(120.0)])]
    )
    assert whole.billing is BillingBasis.MIXED
    assert (whole.characters, whole.seconds) == (1000, 120.0)
    assert (whole.dollars_per_1000_characters, whole.dollars_per_minute) == (0.3, 0.12)
    assert whole.sentence == (
        "This run costs $0.54: $0.30 for the narration's 1,000 characters at $0.30 per 1,000 characters, and $0.24 "
        "for the score's about 120 seconds of audio at $0.12 per minute of audio."
    )


def test_a_total_that_buys_only_sound_is_billed_the_way_sound_is(tmp_path: Path) -> None:
    """A narration with every take on disk buys nothing, so the sound alone decides how the total bills."""
    inputs = project(tmp_path, SOUND)
    whole = total([cost_of(inputs), cost_of(inputs, [an_effect(60.0)])])
    assert whole.billing is BillingBasis.PER_SECOND
    assert whole.dollars_per_minute == 0.12
    assert whole.sentence.endswith("per minute of audio.")


def test_a_free_voice_beside_paid_sound_leaves_the_sound_to_bill_the_total(tmp_path: Path) -> None:
    """The free takes cost nothing and state no rate, so the total names the rate somebody stated."""
    inputs = project(tmp_path, SOUND + FREE_VOICE)
    whole = total([cost_of(inputs, TAKES), cost_of(inputs, [an_effect(60.0)])])
    assert whole.billing is BillingBasis.PER_SECOND
    assert (whole.price_key, whole.price_layer) == ("score.effects.dollars_per_minute", Layer.PROJECT)


def test_a_voice_that_declares_no_bill_leaves_the_whole_total_undeclared(tmp_path: Path) -> None:
    inputs = project(tmp_path, SOUND + HOUSE_VOICE)
    whole = total([cost_of(inputs, TAKES), cost_of(inputs, [an_effect(60.0)])])
    assert whole.billing is BillingBasis.UNDECLARED
    assert "cannot price it" in whole.sentence


def test_a_total_of_costs_that_buy_nothing_still_names_a_rate(tmp_path: Path) -> None:
    inputs = project(tmp_path, SOUND)
    whole = total([cost_of(inputs), cost_of(inputs)])
    assert not whole.buys
    assert (whole.dollars_per_1000_characters, whole.price_key) == (0.3, "elevenlabs.dollars_per_1000_characters")


@pytest.mark.parametrize(
    ("voice", "dollars", "said"),
    [
        (FREE_VOICE, 0.0, "This run voiced 39 characters for nothing, because the voice is free."),
        ("", 0.30, "This run spent $0.30"),
    ],
    ids=["free-voice", "paid-voice"],
)
def test_a_charged_total_counts_only_the_stages_that_had_leave_to_buy(
    tmp_path: Path, voice: str, dollars: float, said: str
) -> None:
    """A score that only priced what it would buy is never reported as spent beside a narration that bought."""
    inputs = project(tmp_path, SOUND + voice)
    characters = 39 if voice else 1000
    narrated = cost_of(inputs, [Buy(characters=characters, seconds=3.9, sections=(1,))], state=CostState.CHARGED)
    whole = total([narrated, cost_of(inputs, [an_effect(60.0)])])
    assert whole.state is CostState.CHARGED
    assert whole.dollars == whole.ceiling_dollars == dollars
    assert whole.sentence.startswith(said)


def test_a_total_of_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="a total adds at least one cost"):
        total([])


def test_a_total_adds_only_billed_seconds(tmp_path: Path) -> None:
    """A per-character narration's expected seconds are not billed, so they are not sound the total counts."""
    inputs = project(tmp_path, SOUND)
    narrated = cost_of(inputs, [Buy(characters=39, seconds=3.8, sections=(1,))])
    assert narrated.seconds == 3.8
    whole = total([narrated, cost_of(inputs, [an_effect(60.0)])])
    assert (whole.characters, whole.seconds) == (39, 60.0)


# ---- free and one request's charge ---------------------------------------------------------------


def test_free_is_what_the_voice_declares_and_never_a_rate_of_zero(tmp_path: Path) -> None:
    """A zero rate on a voice that bills is somebody's statement about their plan, stated or not."""
    assert not a_cost(0.0, 0.0, layer=Layer.DEFAULT).model_copy(update={"dollars_per_1000_characters": 0.0}).free
    assert not a_cost(0.0, 0.0).model_copy(update={"dollars_per_1000_characters": 0.0}).free
    assert a_cost(0.0, 0.0, billing=BillingBasis.FREE, layer=Layer.DEFAULT).free
    zero_rate = MINIMAL_TOML + "\n[elevenlabs]\ndollars_per_1000_characters = 0.0\n"
    zero = project(tmp_path / "a-paid-voice", zero_rate)
    assert not is_free(zero) and not cost_of(zero, TAKES).free
    free = project(tmp_path / "a-free-voice", zero_rate + FREE_VOICE)
    assert is_free(free) and cost_of(free, TAKES).free


@pytest.mark.parametrize("bill", VOICE_BILLS)
def test_is_free_charge_of_and_the_costs_billing_read_one_declaration(tmp_path: Path, bill: BillingBasis) -> None:
    inputs = a_voice_billed(tmp_path, bill)
    free = is_free(inputs)
    assert free is (charge_of(inputs, TAKES[0]) is None)
    assert free is (cost_of(inputs, TAKES).billing is BillingBasis.FREE)
    assert free is (bill is BillingBasis.FREE)


def test_charge_of_is_the_exact_decimal_of_one_request(tmp_path: Path) -> None:
    """A charge is worked out in decimals, so 7 characters at 30 cents per 1,000 is $0.0021 and not a float near it."""
    assert charge_of(project(tmp_path / "speech", SPEECH), Buy(characters=7)) == 0.0021
    assert charge_of(project(tmp_path / "sound", SOUND), an_effect(35.0)) == 0.07


# ---- money --------------------------------------------------------------------------------------


def test_a_certain_price_rounds_up_to_the_cent_with_its_ceiling(tmp_path: Path) -> None:
    """39 characters at 30 cents per 1,000 is $0.0117, which a run can pay, so it is never stated as a cent less."""
    priced = cost_of(project(tmp_path, SPEECH), [Buy(characters=39, seconds=3.8, sections=(1,))])
    assert priced.dollars == priced.ceiling_dollars == 0.02
    free = cost_of(project(tmp_path, SPEECH + FREE_VOICE), [Buy(sections=(1,))])
    assert free.dollars == free.ceiling_dollars == 0.0
    assert money(free.dollars) == "$0.00"


def test_each_stage_rounds_once_and_the_total_adds_the_rounded_stages(tmp_path: Path) -> None:
    """Two stages of exactly $0.0149 are a cent each rounded up, $0.04 together, and never rounded up again."""
    toml = (
        MINIMAL_TOML
        + "\n[elevenlabs]\ndollars_per_1000_characters = 0.1\n\n[score.effects]\ndollars_per_minute = 0.149\n"
    )
    inputs = project(tmp_path, toml)
    narrated, scored = cost_of(inputs, [Buy(characters=149, sections=(1,))]), cost_of(inputs, [an_effect(6.0)])
    assert narrated.dollars == scored.dollars == 0.02
    whole = total([narrated, scored])
    assert whole.dollars == whole.ceiling_dollars == 0.04


@pytest.fixture(scope="module")
def priced(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Inputs]]:
    """One project per rate the invariants are searched at, loaded once for every example."""
    rates = {"speech-0.3": SPEECH, "speech-0.005": SPEECH.replace("= 0.3", "= 0.005"), "sound": RATED}
    yield {name: project(tmp_path_factory.mktemp(name), toml) for name, toml in rates.items()}


TAKE_BUYS = st.lists(
    st.builds(
        Buy,
        characters=st.integers(min_value=0, max_value=5000),
        seconds=st.integers(min_value=0, max_value=1200).map(lambda tenths: tenths / 10),
        sections=st.integers(min_value=1, max_value=40).map(lambda number: (number,)),
        certain=st.booleans(),
    ),
    max_size=40,
)
"""Up to forty takes, each a count of characters and the seconds the script gives it, to a tenth."""

SOUND_BUYS = st.lists(
    st.builds(
        Buy,
        seconds=st.integers(min_value=1, max_value=300_000).map(lambda ms: ms / 1000),
        sections=st.just((1,)),
        kind=st.sampled_from(list(SoundKind)),
    ),
    max_size=12,
)
"""Up to twelve sound requests, each to the millisecond, of any kind."""

PROJECTS = ["speech-0.3", "speech-0.005", "sound"]
"""The rates each invariant is held at: two per-character voices and a score of three per-minute kinds."""


def buys_for(name: str) -> st.SearchStrategy[list[Buy]]:
    """The buys a project of this name is searched over: takes for a voice, sounds for a score."""
    return SOUND_BUYS if name == "sound" else TAKE_BUYS


def exact(inputs: Inputs, buys: Sequence[Buy]) -> Fraction:
    """What these buys truly cost, as a fraction, from each count and rate as the author wrote them."""
    tables = {SoundKind.AMBIENCE: "ambience", SoundKind.EFFECT: "effects", SoundKind.MUSIC: "music"}
    owed = Fraction(0)
    for buy in buys:
        if buy.kind is None:
            rate = inputs.settings.elevenlabs.dollars_per_1000_characters
            owed += Fraction(buy.characters) * Fraction(Decimal(repr(rate))) / 1000
        else:
            rate = getattr(inputs.settings.score, tables[buy.kind]).dollars_per_minute
            owed += Fraction(Decimal(repr(buy.seconds))) * Fraction(Decimal(repr(rate))) / 60
    return owed


@pytest.mark.parametrize("name", PROJECTS)
def test_a_stages_charges_add_up_to_its_charged_cost_to_the_cent(priced: dict[str, Inputs], name: str) -> None:
    """A host that adds a stage's charge lines and rounds up gets the stage's charged price, whatever it bought."""
    inputs = priced[name]

    @given(buys=buys_for(name))
    def holds(buys: list[Buy]) -> None:
        charged = cost_of(inputs, buys, state=CostState.CHARGED)
        assert charged.dollars == up_to_the_cent(charge_of(inputs, buy) or 0.0 for buy in buys)

    holds()


@pytest.mark.parametrize("name", PROJECTS)
def test_a_charged_cost_never_exceeds_the_ceiling_it_was_approved_at(priced: dict[str, Inputs], name: str) -> None:
    """Whatever part of an approved plan a run bought, its charged price is under the ceiling it was approved at."""
    inputs = priced[name]

    @given(buys=buys_for(name), data=st.data())
    def holds(buys: list[Buy], data: st.DataObject) -> None:
        bought = data.draw(st.lists(st.sampled_from(buys), unique_by=id)) if buys else []
        approved = cost_of(inputs, buys)
        assert cost_of(inputs, bought, state=CostState.CHARGED).dollars <= approved.ceiling_dollars

    holds()


@pytest.mark.parametrize("name", PROJECTS)
def test_a_ceiling_is_never_below_what_the_buys_can_cost(priced: dict[str, Inputs], name: str) -> None:
    """The ceiling is rounded up from the exact bill, so it is at or above it and less than a cent over."""
    inputs = priced[name]

    @given(buys=buys_for(name))
    def holds(buys: list[Buy]) -> None:
        owed = exact(inputs, buys)
        ceiling = Fraction(Decimal(repr(cost_of(inputs, buys).ceiling_dollars)))
        assert owed <= ceiling < owed + Fraction(1, 100)

    holds()


def test_a_totals_figures_are_its_counted_stages_figures_added(tmp_path: Path) -> None:
    """A reader who adds the stages a total counts gets the total, and a stage it does not count is left out."""
    inputs = project(tmp_path, SOUND)
    narrated = cost_of(inputs, [Buy(characters=39, seconds=3.8, sections=(1,))])
    scored = cost_of(inputs, [an_effect(10.0), an_effect(25.0)])
    whole = total([narrated, scored])
    assert whole.dollars == round(narrated.dollars + scored.dollars, 2)
    assert whole.ceiling_dollars == round(narrated.ceiling_dollars + scored.ceiling_dollars, 2)
    charged = cost_of(inputs, [Buy(characters=39, seconds=3.8, sections=(1,))], state=CostState.CHARGED)
    assert total([charged, scored]).dollars == charged.dollars


# ---- one row per stage ----------------------------------------------------------------------------


def test_each_stage_that_buys_has_one_row_in_pipeline_order(tmp_path: Path) -> None:
    """A stage's own cost is its one row, and a total holds every stage's row in pipeline order."""
    inputs = project(tmp_path, SOUND)
    narrated, scored = cost_of(inputs, TAKES), cost_of(inputs, [an_effect(60.0)])
    assert [row.stage for row in narrated.stages] == [Stage.NARRATE]
    assert [row.stage for row in scored.stages] == [Stage.SCORE]
    assert narrated.stages[0].dollars == narrated.dollars
    assert cost_of(inputs).stages == ()
    assert [row.stage for row in total([scored, narrated]).stages] == [Stage.NARRATE, Stage.SCORE]


def test_a_stage_row_keeps_its_own_state_in_a_charged_total(tmp_path: Path) -> None:
    """A free voice voiced and the score only priced, so the score's row stays an estimate and is not spent."""
    inputs = project(tmp_path, SOUND + FREE_VOICE)
    narrated = cost_of(inputs, [Buy(characters=39, seconds=3.9, sections=(1,))], state=CostState.CHARGED)
    whole = total([narrated, cost_of(inputs, [an_effect(60.0)])])
    assert [(row.stage, row.state) for row in whole.stages] == [
        (Stage.NARRATE, CostState.CHARGED),
        (Stage.SCORE, CostState.ESTIMATE),
    ]
    assert whole.stages[1].dollars == 0.12
    assert whole.state is CostState.CHARGED and whole.dollars == 0
    assert whole.sentence == "This run voiced 39 characters for nothing, because the voice is free."


def test_one_stage_in_two_costs_is_refused(tmp_path: Path) -> None:
    inputs = project(tmp_path, SPEECH)
    with pytest.raises(ValueError, match="narrate is in two of the costs added, which would count it twice"):
        total([cost_of(inputs, TAKES), cost_of(inputs, TAKES)])


def two_stages(tmp_path: Path, state: CostState = CostState.ESTIMATE, voice: str = "") -> Cost:
    """39 characters of narration at 30 cents per 1,000 beside 60 seconds of score at twelve cents a minute.

    `voice` is what the project adds to name its voice, which is the paid one when it adds nothing.
    """
    inputs = project(tmp_path, SOUND + voice)
    narrated = cost_of(inputs, [Buy(characters=39, seconds=3.9, sections=(1,))], state=state)
    return total([narrated, cost_of(inputs, [an_effect(60.0)], state=state)])


def test_the_total_names_each_stage_that_buys_at_a_price(tmp_path: Path) -> None:
    assert two_stages(tmp_path / "priced").sentence == (
        "This run costs $0.14: $0.02 for the narration's 39 characters at $0.30 per 1,000 characters, and $0.12 "
        "for the score's about 60 seconds of audio at $0.12 per minute of audio."
    )
    assert two_stages(tmp_path / "spent", CostState.CHARGED).sentence == (
        "This run spent $0.14: $0.02 on the narration's 39 characters at $0.30 per 1,000 characters, and $0.12 "
        "on the score's about 60 seconds of audio at $0.12 per minute of audio."
    )


@pytest.mark.parametrize(
    ("state", "said"),
    [
        (
            CostState.ESTIMATE,
            "This run costs $0.12 for the score's about 60 seconds of audio at $0.12 per minute of audio, and voices "
            "39 characters for nothing, because the voice is free.",
        ),
        (
            CostState.CHARGED,
            "This run spent $0.12 on the score's about 60 seconds of audio at $0.12 per minute of audio, and voiced "
            "39 characters for nothing, because the voice is free.",
        ),
    ],
)
def test_a_free_voice_beside_a_paid_score_names_both(tmp_path: Path, state: CostState, said: str) -> None:
    whole = two_stages(tmp_path, state, voice=FREE_VOICE)
    assert (whole.billing, whole.free, whole.seconds) == (BillingBasis.PER_SECOND, False, 63.9)
    assert whole.sentence == said


def test_takes_nobody_could_match_beside_a_score_name_the_whole_ceiling(tmp_path: Path) -> None:
    """The narration may cost nothing or up to $0.14, so the sentence names the run's whole ceiling of $0.26."""
    inputs = project(tmp_path, SOUND)
    unmatched = cost_of(inputs, [Buy(characters=466, seconds=39.0, sections=(1,), certain=False)])
    assert (unmatched.dollars, unmatched.ceiling_dollars) == (0.0, 0.14)
    whole = total([unmatched, cost_of(inputs, [an_effect(60.0)])])
    assert (whole.dollars, whole.ceiling_dollars) == (0.12, 0.26)
    assert whole.sentence == (
        "This run costs $0.12 for the score's about 60 seconds of audio at $0.12 per minute of audio, and up to "
        "$0.26 if the takes on disk that could not be matched to a voice need making again, at $0.30 per 1,000 "
        "characters."
    )


def test_a_score_is_never_called_takes_on_disk(tmp_path: Path) -> None:
    """Only takes are matched to a voice, so no clause about the score borrows the words of an unmatched take."""
    inputs = project(tmp_path, SOUND)
    unmatched = cost_of(inputs, [Buy(characters=466, seconds=39.0, sections=(1,), certain=False)])
    scored = cost_of(inputs, [an_effect(2.0)])
    for said in (scored.sentence, two_stages(tmp_path).sentence, total([unmatched, scored]).sentence):
        assert all("take" not in clause for clause in said.split(", and ") if "score" in clause or "audio" in clause)
