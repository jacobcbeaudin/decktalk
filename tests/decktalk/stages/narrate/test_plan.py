"""The take plan: which sections a run would voice, what it already holds, and what that costs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from decktalk.artifacts import AudioPrint, ProviderWords, TakeInputs, Takes, take_file, words_file
from decktalk.inputs import Inputs
from decktalk.inputs.script import parse_script
from decktalk.inputs.workspace import Workspace
from decktalk.results import Billing, CostState, Layer, TakeStatus
from decktalk.speech import DECLARED, FREE, PROVIDERS, Bill, canonical_text
from decktalk.stages.narrate.plan import (
    cost_of,
    is_cached,
    placeholder_inputs,
    placeholder_plan,
    seconds_of,
    take_inputs,
    voiced_plan,
)
from support.paths import DATA
from support.projects import MINIMAL_TOML, load_project
from support.takes import TAKE_SUFFIX

from .conftest import TOML, VOICE_ID, a_paid_take

GOLDEN = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))
"""The two films the founder has really paid for, with the digest of every take he bought."""

ROWS = [(row["film"], row["section"], row["markdown"], row["hash"]) for row in GOLDEN["takes"]]
IDS = [f"{film}-{section}" for film, section, _markdown, _digest in ROWS]


def unvoiceable(make_inputs: Callable[..., Inputs]) -> Inputs:
    """A project read by the shipped provider on a machine with no credential for it."""
    root = make_inputs(name="unvoiceable").root
    return Inputs.load(root, environ={})


@pytest.mark.parametrize(("film", "section", "markdown", "digest"), ROWS, ids=IDS)
def test_the_digest_of_a_paid_take_is_the_one_its_film_was_billed_for(
    film: str, section: int, markdown: str, digest: str
) -> None:
    """A digest that moved means the next voiced run buys that take again.

    `tests/data/take_hash.json` is therefore never updated to make this pass. The whole path is
    measured, from the markdown an author wrote through the script parser to the inputs the plan
    hands the digest, because every one of those steps decides what a take costs.
    """
    (segment,) = parse_script(markdown)
    inputs = GOLDEN["inputs"]
    made = TakeInputs.of(
        provider=inputs["provider"],
        voice=inputs["voice"],
        model=inputs["model"],
        output_format=inputs["output_format"],
        settings=inputs["settings"],
        text=canonical_text(segment.pieces),
    )
    assert made.digest == digest, f"{film} section {section} would be voiced again"


def test_a_take_is_cached_only_when_its_audio_and_its_words_are_both_there_and_agree(tmp_path: Path) -> None:
    space = Workspace(
        root=tmp_path, build=tmp_path, name="demo", suffix=TAKE_SUFFIX, takes=tmp_path, score_dir=tmp_path
    )
    (tmp_path / take_file("abc", TAKE_SUFFIX)).write_bytes(b"take")
    assert not is_cached("abc", space)
    (tmp_path / words_file("abc")).write_text("{}", encoding="utf-8")
    assert is_cached("abc", space)
    ProviderWords(audio=AudioPrint.of(b"another take")).write(tmp_path / words_file("abc"))
    assert not is_cached("abc", space), "the audio does not hold the bytes its words recorded"
    ProviderWords(audio=AudioPrint.of(b"take")).write(tmp_path / words_file("abc"))
    assert is_cached("abc", space)
    (tmp_path / take_file("abc", TAKE_SUFFIX)).write_bytes(b"")
    assert not is_cached("abc", space), "an empty take is no take"


def test_a_placeholder_digest_moves_with_the_pace_it_was_sized_at(
    make_inputs: Callable[..., Inputs],
) -> None:
    faster = make_inputs(toml=TOML.replace("lead_seconds = 0.5", "placeholder_words_per_minute = 200"), name="faster")
    plain = make_inputs()
    (segment,) = [s for s in plain.spoken() if s.index == 1]
    assert placeholder_inputs(plain, segment).digest != placeholder_inputs(faster, segment).digest


def test_two_sections_with_the_same_words_share_one_take(make_inputs: Callable[..., Inputs]) -> None:
    """The second of them is kept rather than sent, so one set of words is never bought twice."""
    doubled = "## 1. Open\n\nA bowl.\n\n## 2. Middle\n\nA bowl.\n\n## 3. Close\n\nA ball.\n"
    project = make_inputs(script=doubled)
    plans = placeholder_plan(project, list(project.spoken()))
    assert [plan.status for plan in plans] == [TakeStatus.PLACEHOLDER, TakeStatus.KEPT, TakeStatus.PLACEHOLDER]
    assert plans[1].reason == "another section of this run voices these words"


def test_a_run_that_was_told_to_make_them_again_plans_every_section(inputs: Inputs) -> None:
    plans = placeholder_plan(inputs, list(inputs.spoken()), force=True)
    assert {plan.status for plan in plans} == {TakeStatus.PLACEHOLDER}
    assert {plan.reason for plan in plans} == {"this run was told to make it again"}


def test_a_section_whose_take_is_on_disk_is_kept(inputs: Inputs) -> None:
    targets = list(inputs.spoken())
    first = placeholder_plan(inputs, targets)[0]
    assert first.digest is not None
    inputs.workspace.narrate_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.narrate_dir / take_file(first.digest, TAKE_SUFFIX)).write_bytes(b"")
    (inputs.workspace.narrate_dir / words_file(first.digest)).write_text('{"words": []}', encoding="utf-8")
    assert placeholder_plan(inputs, targets)[0].status is TakeStatus.KEPT


def test_a_voiced_plan_prices_without_a_credential(
    make_inputs: Callable[..., Inputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A price is over names and text, none of them secret, so a machine with no key prices exactly."""

    def refuse(_context: object) -> object:
        raise AssertionError("a price built the provider")

    monkeypatch.setitem(PROVIDERS, "elevenlabs", refuse)
    project = unvoiceable(make_inputs)
    plans, why = voiced_plan(project, list(project.spoken()), model="m", voice_id=VOICE_ID)
    assert why is None
    assert not any(plan.unchecked for plan in plans)
    assert all(plan.digest is not None for plan in plans)


def test_a_keyless_plan_finds_the_take_a_voice_already_made(make_inputs: Callable[..., Inputs]) -> None:
    """A one-section edit on a machine with no key is priced as one section, not as the whole film."""
    project = unvoiceable(make_inputs)
    targets = list(project.spoken())
    first, *_rest = voiced_plan(project, targets, model="m", voice_id=VOICE_ID)[0]
    assert first.digest is not None
    project.workspace.takes.mkdir(parents=True, exist_ok=True)
    (project.workspace.takes / take_file(first.digest, TAKE_SUFFIX)).write_bytes(b"take")
    (project.workspace.takes / words_file(first.digest)).write_text('{"words": []}', encoding="utf-8")
    plans, _why = voiced_plan(project, targets, model="m", voice_id=VOICE_ID)
    assert [plan.status for plan in plans] == [TakeStatus.KEPT, TakeStatus.VOICED, TakeStatus.VOICED]
    spend = cost_of(plans, project, state=CostState.ESTIMATE)
    assert spend.sections == (2, 3)
    assert spend.dollars == spend.ceiling_dollars


def test_a_voiced_plan_with_no_voice_named_cannot_check_the_cache(make_inputs: Callable[..., Inputs]) -> None:
    """A plan that does not know the voice still prices what it would send, and says why it is unsure."""
    project = unvoiceable(make_inputs)
    plans, why = voiced_plan(project, list(project.spoken()), model="m", voice_id=None)
    assert why is not None
    assert why.startswith("No voice is named") and "[voice] id" in why and "DECKTALK_VOICE_ID" in why
    assert all(plan.digest is None for plan in plans)


@pytest.mark.usefixtures("fake_voice")
def test_a_voiced_plan_prices_what_it_will_send_and_what_it_can_cost(inputs: Inputs) -> None:
    plans, why = voiced_plan(inputs, list(inputs.spoken()), model="m", voice_id=VOICE_ID)
    assert why is None
    spend = cost_of(plans, inputs, state=CostState.ESTIMATE)
    characters = sum(len(canonical_text(plan.segment.pieces)) for plan in plans)
    assert spend.characters == characters
    assert spend.dollars == pytest.approx(round(characters / 1000 * 0.30, 2))
    assert spend.ceiling_dollars == spend.dollars
    assert spend.dollars_per_1000_characters == pytest.approx(0.30)
    assert spend.price_layer is Layer.PROJECT
    assert spend.sections == (1, 2, 3)


def test_a_paid_take_the_cache_could_not_be_checked_for_is_priced_into_the_ceiling_alone(
    make_inputs: Callable[..., Inputs], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a voiced take could turn out to be the one this run asks for, so only a voiced take is in doubt."""
    project = unvoiceable(make_inputs)
    first, *rest = project.spoken()
    held = Takes(script="script.md", model="m", output_format="mp3", sections=(a_paid_take(first.index),))
    monkeypatch.setattr(Inputs, "takes", lambda _self: held)
    plans, _why = voiced_plan(project, [first, *rest], model="m", voice_id=None)
    assert [plan.unchecked for plan in plans] == [True] + [False] * len(rest)
    spend = cost_of(plans, project, state=CostState.ESTIMATE)
    assert spend.characters == sum(plan.characters_sent for plan in plans[1:])
    assert spend.sections == (*(plan.segment.index for plan in plans[1:]), first.index)


def test_a_section_with_no_paid_take_is_priced_as_certain_when_no_voice_is_named(
    make_inputs: Callable[..., Inputs],
) -> None:
    """A fresh project needs every take whatever the voice is, so its price is not a ceiling alone."""
    project = unvoiceable(make_inputs)
    plans, _why = voiced_plan(project, list(project.spoken()), model="m", voice_id=None)
    assert not any(plan.unchecked for plan in plans)
    spend = cost_of(plans, project, state=CostState.ESTIMATE)
    assert spend.dollars == spend.ceiling_dollars > 0


def test_a_price_nobody_stated_is_reported_as_the_default(make_inputs: Callable[..., Inputs]) -> None:
    """`--max-cost` refuses while the price is the default, so the layer has to travel with it."""
    project = make_inputs(
        toml="[project]\nname = 't'\n\n[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n",
        script="## 1. Open\n\nA bowl.\n",
        name="unpriced",
    )
    spend = cost_of(placeholder_plan(project, list(project.spoken())), project, state=CostState.ESTIMATE)
    assert spend.price_layer is Layer.DEFAULT
    assert spend.dollars_per_1000_characters == pytest.approx(0.0)


def declare(monkeypatch: pytest.MonkeyPatch, bill: Bill) -> None:
    """Have the shipped voice declare this bill, which is the one thing a price reads about how it bills."""
    monkeypatch.setitem(DECLARED, "elevenlabs", replace(DECLARED["elevenlabs"], billing=bill))


@pytest.mark.usefixtures("fake_voice")
def test_a_voice_that_declares_itself_free_prices_at_nothing_whatever_its_table_states(
    inputs: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Free comes from the declaration, so the 0.30 this project states is never charged."""
    declare(monkeypatch, FREE)
    plans, _why = voiced_plan(inputs, list(inputs.spoken()), model="m", voice_id=VOICE_ID)
    spend = cost_of(plans, inputs, state=CostState.ESTIMATE)
    assert spend.free and spend.billing is Billing.FREE
    assert spend.dollars == spend.ceiling_dollars == 0
    assert spend.characters > 0 and spend.sections == (1, 2, 3)
    assert spend.price_key is None
    assert spend.sentence.endswith("for nothing, because the voice is free.")


@pytest.mark.usefixtures("fake_voice")
def test_a_voice_that_bills_per_second_is_priced_on_the_seconds_its_takes_will_run(
    inputs: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rate is read under the key the declaration names, per minute of audio rather than per character."""
    declare(monkeypatch, Bill(Billing.PER_SECOND, rate="dollars_per_1000_characters"))
    plans, _why = voiced_plan(inputs, list(inputs.spoken()), model="m", voice_id=VOICE_ID)
    spend = cost_of(plans, inputs, state=CostState.ESTIMATE)
    seconds = sum(seconds_of(inputs, plan) for plan in plans)
    assert spend.billing is Billing.PER_SECOND
    assert spend.seconds == pytest.approx(seconds)
    assert spend.dollars == pytest.approx(round(seconds * 0.30 / 60, 2))
    assert spend.dollars_per_minute == 0.30 and spend.dollars_per_1000_characters == 0
    assert spend.price_key == "elevenlabs.dollars_per_1000_characters"
    assert spend.price_layer is Layer.PROJECT
    assert "per minute of audio" in spend.sentence and "1,000 characters" not in spend.sentence


@pytest.mark.usefixtures("fake_voice")
def test_a_voice_that_bills_per_character_states_the_key_its_rate_is_set_under(inputs: Inputs) -> None:
    plans, _why = voiced_plan(inputs, list(inputs.spoken()), model="m", voice_id=VOICE_ID)
    spend = cost_of(plans, inputs, state=CostState.ESTIMATE)
    assert spend.billing is Billing.PER_CHARACTER
    assert spend.price_key == "elevenlabs.dollars_per_1000_characters"
    assert "per 1,000 characters" in spend.sentence


def test_a_voice_a_host_registered_declares_no_bill_and_prices_at_nothing_anybody_stated(
    make_inputs: Callable[..., Inputs],
) -> None:
    project = make_inputs(toml=TOML.replace('provider = "elevenlabs"', 'provider = "house"'), name="house")
    plans, _why = voiced_plan(project, list(project.spoken()), model="m", voice_id=VOICE_ID)
    spend = cost_of(plans, project, state=CostState.ESTIMATE)
    assert spend.billing is Billing.UNDECLARED
    assert spend.dollars == 0 and spend.price_key is None and spend.price_layer is Layer.DEFAULT
    assert not spend.free


@pytest.mark.parametrize(("film", "section", "markdown", "digest"), ROWS, ids=IDS)
def test_a_project_at_the_defaults_names_each_paid_take_by_what_elevenlabs_declares(
    tmp_path: Path, film: str, section: int, markdown: str, digest: str
) -> None:
    """What `[elevenlabs]` declares at its defaults, identity and format, is what every take was paid under."""
    golden = GOLDEN["inputs"]
    project = load_project(tmp_path, MINIMAL_TOML, environ={})
    (segment,) = parse_script(markdown)
    made = take_inputs(project, segment, provider="elevenlabs", voice_id=golden["voice"], model=golden["model"])
    assert made.output_format == golden["output_format"]
    assert made.digest == digest, f"{film} section {section} would be voiced again"
    assert project.workspace.take_file(digest) == f"{digest}.mp3"


def test_a_voice_a_host_registered_keeps_the_digest_its_takes_were_named_by(
    make_inputs: Callable[..., Inputs],
) -> None:
    """It declares no format, so it is asked for the one every take was asked for before adapters declared theirs."""
    project = make_inputs(toml=TOML.replace('provider = "elevenlabs"', 'provider = "house"'), name="house")
    (segment, *_rest) = project.spoken()
    made = take_inputs(project, segment, provider="house", voice_id=VOICE_ID, model="m")
    assert made.output_format == "mp3_44100_128"
    assert (
        made.digest
        == TakeInputs.of(
            provider="house",
            voice=VOICE_ID,
            model="m",
            output_format="mp3_44100_128",
            settings={"speed": 1.0},
            text=canonical_text(segment.pieces),
        ).digest
    )
    assert project.workspace.take_file(made.digest) == f"{made.digest}.mp3"
    spend = cost_of(voiced_plan(project, [segment], model="m", voice_id=VOICE_ID)[0], project, state=CostState.ESTIMATE)
    assert "declares no bill" in spend.sentence
