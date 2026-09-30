"""Every result is one flat frozen object that round-trips, and the committed schemas are its own."""

from __future__ import annotations

import subprocess
import sys
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from decktalk import errors, events, findings, results
from decktalk.results import RESULTS, Result
from support.samples import sample

ROOT = Path(__file__).resolve().parents[2]
RESERVED = ("schema", "ok", "findings", "error")

# The commands that open a run, and the commands that write a file. Both directions are asserted, so
# a result that gains one of the two declared fields without being one of these fails here.
OPENS_A_RUN = {
    "init",
    "install",
    "doctor",
    "status",
    "check",
    "words",
    "storyboard",
    "serve",
    "narrate",
    "cue",
    "record",
    "soundscape",
    "assemble",
    "verify",
    "build",
    "clip",
    "apply",
}
WRITES_A_FILE = {
    "init",
    "doctor",
    "check",
    "storyboard",
    "config-set",
    "config-unset",
    "narrate",
    "cue",
    "record",
    "soundscape",
    "assemble",
    "build",
    "clip",
    "apply",
}


def models() -> list[type[BaseModel]]:
    """Every model this track declares, which is what the shared rules are asserted over."""
    found: list[type[BaseModel]] = []
    for module in (findings, errors, results, events):
        for name in dir(module):
            member = getattr(module, name)
            if isinstance(member, type) and issubclass(member, BaseModel) and member.__module__ == module.__name__:
                found.append(member)
    return found


def test_there_is_one_result_per_command_and_the_names_are_its_own() -> None:
    assert set(RESULTS) == set(OPENS_A_RUN | WRITES_A_FILE | {"config-list", "config-get", "config-explain", "error"})


def test_the_base_reserves_exactly_four_keys() -> None:
    assert [field.alias or name for name, field in Result.model_fields.items()] == list(RESERVED)


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_a_result_round_trips_through_its_own_model(name: str) -> None:
    model = RESULTS[name]
    built = sample(model)
    assert type(built).model_validate_json(built.model_dump_json()) == built


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_a_result_writes_its_reserved_keys_under_their_published_names(name: str) -> None:
    written = sample(RESULTS[name]).model_dump(mode="json")
    assert list(written)[: len(RESERVED)] == list(RESERVED)


def test_the_two_command_facts_are_class_facts_and_never_fields() -> None:
    """The command line derives its shared flags from these, and a caller never meets them in the JSON."""
    for model in RESULTS.values():
        assert isinstance(model.reports_findings, bool)
        assert isinstance(model.spends, bool)
        assert not {"reports_findings", "spends"} & set(model.model_fields)
    assert results.InitResult.reports_findings is False


def test_every_result_that_spends_also_reports_what_it_judged() -> None:
    """A command that buys something judges what it bought, so spending is a narrowing of judging."""
    spending = {name for name, model in RESULTS.items() if model.spends}
    judging = {name for name, model in RESULTS.items() if model.reports_findings}
    assert spending and spending <= judging


def test_a_volatile_field_says_so_in_its_own_schema() -> None:
    build = RESULTS["build"].model_json_schema()
    assert build["properties"]["run"]["volatile"] is True
    assert build["properties"]["seconds"]["volatile"] is True
    assert "volatile" not in build["properties"]["film"]


def test_every_model_uses_the_one_config() -> None:
    for model in models():
        assert {key: model.model_config[key] for key in findings.MODEL} == dict(findings.MODEL), model.__name__


def test_every_field_publishes_one_sentence() -> None:
    for model in models():
        for name, field in model.model_fields.items():
            assert field.description, f"{model.__name__}.{name}"
            assert field.description.endswith("."), f"{model.__name__}.{name}"


def test_no_collection_field_is_a_list() -> None:
    for model in models():
        for name, field in model.model_fields.items():
            assert typing.get_origin(field.annotation) is not list, f"{model.__name__}.{name}"


def test_the_only_alias_in_the_package_is_schema() -> None:
    aliased = {field.alias for model in models() for field in model.model_fields.values() if field.alias}
    assert aliased == {"schema"}


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
        built.ok = False  # type: ignore[misc]


def test_the_committed_schemas_are_what_the_generator_writes() -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_result_schemas.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_committed_api_is_what_the_generator_writes() -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_api.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def _spend(dollars: float, ceiling: float, *, state: results.SpendState = results.SpendState.ESTIMATE) -> results.Spend:
    """A price as a stage states one, at thirty cents a thousand characters."""
    return results.Spend(
        state=state,
        sections=(1,),
        characters=int(dollars / 0.30 * 1000),
        dollars=dollars,
        ceiling_dollars=ceiling,
        price_per_1000_characters=0.30,
        price_layer=results.Layer.PROJECT,
    )


def test_a_price_that_is_certain_is_stated_once() -> None:
    assert _spend(0.14, 0.14).sentence == "This run costs $0.14 for 466 characters at $0.30 per 1,000 characters."


def test_a_price_the_cache_could_not_check_is_stated_as_a_ceiling() -> None:
    """The smoke test read "about $0.00, up to $0.14" as a contradiction, so the ceiling says why it is one."""
    said = _spend(0.0, 0.14).sentence
    assert said == (
        "The takes on disk could not be matched to a voice, so this run costs up to $0.14 at $0.30 per 1,000 "
        "characters."
    )
    assert "$0.00" not in said


def test_a_price_with_a_certain_part_and_a_ceiling_names_both() -> None:
    said = _spend(0.03, 0.18).sentence
    assert "$0.03 for the sections that certainly need a take" in said
    assert "up to $0.18" in said


def test_a_charged_price_is_stated_as_spent() -> None:
    charged = _spend(0.14, 0.14, state=results.SpendState.CHARGED)
    assert charged.sentence.startswith("This run spent $0.14")


def test_a_price_of_nothing_says_the_run_buys_nothing() -> None:
    assert _spend(0.0, 0.0).sentence == "This run buys nothing."
