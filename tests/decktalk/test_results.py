"""Every result is one flat frozen object that round-trips, and the committed schemas are its own."""

from __future__ import annotations

import subprocess
import sys
import types
import typing
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import pytest
from pydantic import BaseModel

from decktalk import errors, events, findings, results
from decktalk.findings import Code, Finding, Location
from decktalk.results import RESULTS, SCHEMA, Result

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

# A finding fills its own certainty and page from its code, so the sampler is handed one ready made.
EXAMPLES: dict[type[BaseModel], BaseModel] = {
    Finding: Finding(code=Code.CUE_OFF, message="It lands 340 ms late.", location=Location(where="2.1:formula")),
}


SCALARS: dict[object, object] = {
    bool: True,
    int: 1,
    float: 1.5,
    str: "one",
    datetime: datetime(2026, 9, 24, 3, 0, tzinfo=UTC),
}
"""One value per type that stands on its own, which is every field a sampler fills without recursing."""


def value(annotation: object) -> object:
    """One value of the given type, which is all a round-trip needs the field to hold."""
    if hasattr(annotation, "__metadata__"):
        return value(typing.get_args(annotation)[0])
    if annotation in EXAMPLES:
        return EXAMPLES[annotation]  # type: ignore[index]
    if annotation in SCALARS:
        return SCALARS[annotation]  # type: ignore[index]
    if isinstance(annotation, type):
        return of_class(annotation)
    return of_generic(annotation)


def of_class(annotation: type) -> object:
    """One value of a class the package declares, which is a path, a member or a model of its own."""
    if issubclass(annotation, Path):
        return Path("build/final/demo.mp4")
    if issubclass(annotation, Enum):
        return next(iter(annotation))
    if issubclass(annotation, BaseModel):
        return sample(annotation)
    return "one"


def of_generic(annotation: object) -> object:
    """One value of an annotation that names other annotations, filled from the first one it names."""
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if origin is typing.Literal:
        return arguments[0]
    if origin is tuple:
        return (value(arguments[0]),)
    if origin is dict:
        return {value(arguments[0]): value(arguments[1])}
    if origin in (typing.Union, types.UnionType):
        return None if type(None) in arguments else value(arguments[0])
    return "one"


def sample(model: type[BaseModel]) -> BaseModel:
    """One instance of a model with every field filled, built from the field types alone."""
    return model(**{name: value(field.annotation) for name, field in model.model_fields.items()})


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


def test_the_shape_version_is_two_on_every_result() -> None:
    for name, model in RESULTS.items():
        assert model.model_fields["schema_"].default == SCHEMA, name


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_a_result_round_trips_through_its_own_model(name: str) -> None:
    model = RESULTS[name]
    built = sample(model)
    assert type(built).model_validate_json(built.model_dump_json()) == built


@pytest.mark.parametrize("name", sorted(RESULTS))
def test_a_result_writes_its_reserved_keys_under_their_published_names(name: str) -> None:
    written = sample(RESULTS[name]).model_dump(mode="json")
    assert list(written)[: len(RESERVED)] == list(RESERVED)


def test_run_is_declared_by_exactly_the_commands_that_open_one() -> None:
    assert {name for name, model in RESULTS.items() if "run" in model.model_fields} == OPENS_A_RUN


def test_written_is_declared_by_exactly_the_commands_that_write_a_file() -> None:
    assert {name for name, model in RESULTS.items() if "written" in model.model_fields} == WRITES_A_FILE


def test_the_two_command_facts_are_class_facts_and_never_fields() -> None:
    """The command line derives its shared flags from these, and a caller never meets them in the JSON."""
    for model in RESULTS.values():
        assert isinstance(model.reports_findings, bool)
        assert isinstance(model.spends, bool)
        assert not {"reports_findings", "spends"} & set(model.model_fields)


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
    assert written["film"] == "build/final/demo.mp4"


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
