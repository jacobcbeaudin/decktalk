"""The take plan: which sections a run would voice, what it already holds, and what that costs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from decktalk.artifacts import TakeInputs, take_file, words_file
from decktalk.inputs import Inputs
from decktalk.inputs.script import parse_script
from decktalk.results import Layer, SpendState, TakeStatus
from decktalk.settings import VoiceConfig
from decktalk.stages.narrate.plan import (
    is_cached,
    placeholder_inputs,
    placeholder_plan,
    spend_of,
    voice_settings,
    voiced_plan,
)
from support.paths import DATA

from .conftest import TOML, VOICE_ID

GOLDEN = json.loads((DATA / "take_hash.json").read_text(encoding="utf-8"))
"""The two films the founder has really paid for, with the digest of every take he bought."""

ROWS = [(row["film"], row["section"], row["markdown"], row["hash"]) for row in GOLDEN["takes"]]
IDS = [f"{film}-{section}" for film, section, _markdown, _digest in ROWS]


def unvoiceable(make_inputs: Callable[..., Inputs]) -> Inputs:
    """A project read by the shipped provider on a machine with no credential for it."""
    root = make_inputs(toml=TOML.replace('provider = "test-voice"', 'provider = "elevenlabs"'), name="unvoiceable").root
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
        text=segment.tts_text,
    )
    assert made.digest == digest, f"{film} section {section} would be voiced again"


def test_the_frozen_settings_are_the_ones_this_project_would_send() -> None:
    """The plan renders `[voice]` under the provider's names, which is what the digests were over."""
    assert voice_settings(VoiceConfig()) == GOLDEN["inputs"]["settings"]


def test_the_speaker_boost_key_is_the_one_the_provider_reads() -> None:
    rendered = voice_settings(VoiceConfig(speaker_boost=False))
    assert rendered["use_speaker_boost"] is False
    assert "speaker_boost" not in rendered


def test_a_take_is_cached_only_when_its_audio_and_its_words_are_both_there(tmp_path: Path) -> None:
    (tmp_path / take_file("abc")).write_bytes(b"")
    assert not is_cached("abc", tmp_path)
    (tmp_path / words_file("abc")).write_text("{}", encoding="utf-8")
    assert is_cached("abc", tmp_path)


def test_a_placeholder_digest_moves_with_the_pace_it_was_sized_at(
    make_inputs: Callable[..., Inputs],
) -> None:
    faster = make_inputs(toml=TOML.replace("lead_seconds = 0.5", "silent_words_per_minute = 200"), name="faster")
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
    inputs.workspace.takes_dir.mkdir(parents=True, exist_ok=True)
    (inputs.workspace.takes_dir / take_file(first.digest)).write_bytes(b"")
    (inputs.workspace.takes_dir / words_file(first.digest)).write_text('{"words": []}', encoding="utf-8")
    assert placeholder_plan(inputs, targets)[0].status is TakeStatus.KEPT


def test_a_voiced_plan_with_no_credential_cannot_check_the_cache(make_inputs: Callable[..., Inputs]) -> None:
    """A plan that cannot ask the voice still prices what it would send, and says why it is unsure."""
    project = unvoiceable(make_inputs)
    plans, why = voiced_plan(project, list(project.spoken()), model="m", voice_id=VOICE_ID)
    assert why is not None
    assert "ELEVENLABS_API_KEY" in why
    assert all(plan.unchecked for plan in plans)
    assert all(plan.digest is None for plan in plans)


def test_a_voiced_plan_prices_what_it_will_send_and_what_it_can_cost(inputs: Inputs, fake_voice: object) -> None:
    assert fake_voice is not None
    plans, why = voiced_plan(inputs, list(inputs.spoken()), model="m", voice_id=VOICE_ID)
    assert why is None
    spend = spend_of(plans, inputs, state=SpendState.ESTIMATE)
    characters = sum(len(plan.segment.tts_text) for plan in plans)
    assert spend.characters == characters
    assert spend.dollars == pytest.approx(round(characters / 1000 * 0.30, 2))
    assert spend.ceiling_dollars == spend.dollars
    assert spend.price_per_1000_characters == pytest.approx(0.30)
    assert spend.price_layer is Layer.PROJECT
    assert spend.sections == (1, 2, 3)


def test_a_section_the_cache_could_not_be_checked_for_is_priced_into_the_ceiling_alone(
    make_inputs: Callable[..., Inputs],
) -> None:
    project = unvoiceable(make_inputs)
    plans, _why = voiced_plan(project, list(project.spoken()), model="m", voice_id=VOICE_ID)
    spend = spend_of(plans, project, state=SpendState.ESTIMATE)
    assert spend.characters == 0
    assert spend.dollars == pytest.approx(0.0)
    assert spend.ceiling_dollars > 0


def test_a_price_nobody_stated_is_reported_as_the_default(make_inputs: Callable[..., Inputs]) -> None:
    """`--max-cost` refuses while the price is the default, so the layer has to travel with it."""
    project = make_inputs(
        toml="[project]\nname = 't'\n\n[[section]]\nnumber = 1\npage = 'deck/index.html'\nscene = '1'\n",
        script="## 1. Open\n\nA bowl.\n",
        name="unpriced",
    )
    spend = spend_of(placeholder_plan(project, list(project.spoken())), project, state=SpendState.ESTIMATE)
    assert spend.price_layer is Layer.DEFAULT
    assert spend.price_per_1000_characters == pytest.approx(0.0)
