"""Every result is one flat frozen object that round-trips, and the committed schemas are its own."""

from __future__ import annotations

import subprocess
import sys
import typing

import pytest
from pydantic import BaseModel

from decktalk import errors, events, findings, results
from decktalk.results import RESULTS, Result
from support.commands import RESERVED_KEYS
from support.costs import a_cost
from support.paths import REPO
from support.samples import sample


def models() -> list[type[BaseModel]]:
    """Every model this track declares, which is what the shared rules are asserted over."""
    found: list[type[BaseModel]] = []
    for module in (findings, errors, results, events):
        for name in dir(module):
            member = getattr(module, name)
            if isinstance(member, type) and issubclass(member, BaseModel) and member.__module__ == module.__name__:
                found.append(member)
    return found


def test_the_base_reserves_exactly_four_keys() -> None:
    assert [field.alias or name for name, field in Result.model_fields.items()] == list(RESERVED_KEYS)


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_a_result_round_trips_through_its_own_model(name: str) -> None:
    model = RESULTS[name]
    built = sample(model)
    assert type(built).model_validate_json(built.model_dump_json()) == built


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_a_result_writes_its_reserved_keys_under_their_published_names(name: str) -> None:
    written = sample(RESULTS[name]).model_dump(mode="json")
    assert list(written)[: len(RESERVED_KEYS)] == list(RESERVED_KEYS)


def test_the_two_command_facts_are_class_facts_and_never_fields() -> None:
    """The command line derives its shared flags from these, and a caller never meets them in the JSON."""
    for model in RESULTS.values():
        assert isinstance(model.reports_findings, bool)
        assert isinstance(model.spends, bool)
        assert not {"reports_findings", "spends"} & set(model.model_fields)
    assert results.InitResult.reports_findings is False


def test_every_result_that_can_buy_says_whether_it_could_and_what_it_cost() -> None:
    """`spend` is the decision a caller made and `cost` is the price, on every result whose command can buy."""
    for model in RESULTS.values():
        if model.spends:
            assert model.model_fields["spend"].annotation is bool, model.__name__
            assert model.model_fields["cost"].annotation is results.Cost, model.__name__
    assert results.ScoreResult.spends


def test_doctor_names_the_api_key_as_set_missing_or_not_needed() -> None:
    """A voice that takes no key is reported as needing none, never as a key that is set."""
    doctor = sample(results.DoctorResult).model_copy(update={"api_key_state": results.ApiKeyState.NOT_NEEDED})
    written = doctor.model_dump(mode="json")
    assert written["api_key"] == "not_needed"
    assert "api_key_state" not in written


def test_every_result_that_spends_also_reports_what_it_judged() -> None:
    """A command that buys something judges what it bought, so buying is a narrowing of judging."""
    buying = {name for name, model in RESULTS.items() if model.spends}
    judging = {name for name, model in RESULTS.items() if model.reports_findings}
    assert buying and buying <= judging


def test_a_volatile_field_says_so_in_its_own_schema() -> None:
    build = RESULTS["build"].model_json_schema()
    assert build["properties"]["run"]["volatile"] is True
    assert build["properties"]["elapsed_seconds"]["volatile"] is True
    assert "volatile" not in build["properties"]["film"]


def test_an_elapsed_time_is_read_to_the_millisecond() -> None:
    """The type rounds, so no emitter rounds for itself and a result and an event read one clock alike."""
    row = results.StageRun(stage=results.Stage.CUE, outcome=results.Outcome.RAN, elapsed_seconds=10.3456789)
    assert row.elapsed_seconds == 10.346


def test_every_model_uses_the_one_config() -> None:
    for model in models():
        assert findings.MODEL.items() <= model.model_config.items(), model.__name__


def test_every_field_publishes_one_sentence() -> None:
    for model in models():
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name}"
            assert field.description.endswith("."), f"{model.__name__}.{name}"


def test_no_collection_field_is_a_list() -> None:
    for model in models():
        for name, field in model.model_fields.items():
            assert typing.get_origin(field.annotation) is not list, f"{model.__name__}.{name}"


def test_the_only_aliases_in_the_package_are_schema_and_api_key() -> None:
    """`schema` shadows a pydantic name, and `api_key` is a state in Python that must never read as the key itself."""
    aliased = {field.alias for model in models() for field in model.model_fields.values() if field.alias}
    assert aliased == {"schema", "api_key"}


def test_model_class_names_are_unique() -> None:
    names = [model.__name__ for model in models()]
    assert len(names) == len(set(names))


def test_no_model_declares_a_computed_field() -> None:
    for model in models():
        assert not model.model_computed_fields, model.__name__


def test_every_path_is_written_with_forward_slashes() -> None:
    written = sample(RESULTS["assemble"]).model_dump(mode="json")
    assert written["film"].startswith("build/film")


def test_a_result_is_frozen() -> None:
    built = sample(RESULTS["status"])
    with pytest.raises(Exception, match="frozen"):
        built.ok = False


@pytest.mark.parametrize("generator", ["build_result_schemas.py", "build_api.py"])
def test_the_committed_schemas_and_api_are_what_their_generator_writes(generator: str) -> None:
    done = subprocess.run(
        [sys.executable, str(REPO / "scripts" / generator), "--check"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_a_price_that_is_certain_is_stated_once() -> None:
    assert a_cost(0.14, 0.14).sentence == "This run costs $0.14 for 466 characters at $0.30 per 1,000 characters."


def test_a_price_the_cache_could_not_check_is_stated_as_a_ceiling() -> None:
    """The smoke test read "about $0.00, up to $0.14" as a contradiction, so the ceiling says why it is one."""
    said = a_cost(0.0, 0.14).sentence
    assert said == (
        "The takes on disk could not be matched to a voice, so this run costs up to $0.14 at $0.30 per 1,000 "
        "characters."
    )
    assert "$0.00" not in said


def test_a_price_with_a_certain_part_and_a_ceiling_names_both() -> None:
    said = a_cost(0.03, 0.18).sentence
    assert "$0.03 for the sections that certainly need a take" in said
    assert "up to $0.18" in said


def test_a_charged_price_is_stated_as_spent() -> None:
    charged = a_cost(0.14, 0.14, state=results.CostState.CHARGED)
    assert charged.sentence.startswith("This run spent $0.14")


def test_a_price_of_nothing_says_the_run_buys_nothing() -> None:
    assert a_cost(0.0, 0.0).sentence == "This run buys nothing."


def test_a_price_for_seconds_of_sound_alone_buys_something() -> None:
    """A sound-only price covers no section and no character, so its seconds are what it buys."""
    sound = a_cost(0.0, 0.0, sections=(), billing=results.Billing.UNDECLARED).model_copy(update={"seconds": 12.0})
    assert sound.characters == 0 and sound.buys
    assert sound.sentence == (
        "This run makes about 12 seconds of audio on a provider that declares no bill, so DeckTalk cannot price it."
    )


def test_a_free_price_for_sound_names_the_provider_rather_than_a_voice() -> None:
    """A sound is made by a provider and never spoken by a voice, so its free price says provider."""
    sound = a_cost(0.0, 0.0, sections=(), billing=results.Billing.FREE).model_copy(update={"seconds": 12.0})
    assert sound.sentence == "This run makes about 12 seconds of audio for nothing, because the provider is free."
    speech = a_cost(0.0, 0.0, billing=results.Billing.FREE).model_copy(update={"characters": 476})
    assert speech.sentence == "This run voices 476 characters for nothing, because the voice is free."


def test_a_price_that_covers_nothing_buys_nothing() -> None:
    assert not a_cost(0.0, 0.0, sections=()).buys
