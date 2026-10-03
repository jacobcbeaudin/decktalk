"""The walk over the parser, and the join with what the library publishes about itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import decktalk
from decktalk.cli import catalog
from decktalk.errors import ErrorCode
from decktalk.findings import Code
from decktalk.pipeline import PIPELINE
from decktalk.results import RESULTS, Scope
from decktalk.settings import BY_ID, KEYS

from .conftest import commands

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "v1"
"""Where the committed schemas sit, which the rendered settings document is held equal to."""

ROW_KEYS = ("command", "group", "purpose", "result", "params")
"""What one command row publishes, which is what an agent reads before it writes a command line."""

PARAM_KEYS = ("opts", "type", "metavar", "default", "repeatable", "envvar", "help", "hidden")
"""What one parameter row publishes, which is what an agent writes after the flag."""


def test_the_walk_finds_every_command_with_its_group_and_its_params() -> None:
    rows = catalog.walk()
    assert rows
    assert all(set(ROW_KEYS) == set(row) for row in rows)


def test_every_purpose_is_a_whole_sentence() -> None:
    for row in catalog.walk():
        assert str(row["purpose"]).endswith("."), row["command"]
        assert "..." not in str(row["purpose"]), row["command"]


def test_every_parameter_publishes_what_an_agent_writes_after_it() -> None:
    for row in catalog.walk():
        for param in row["params"]:
            assert set(param) >= set(PARAM_KEYS)


def test_an_argument_says_whether_it_is_required_and_whether_it_repeats() -> None:
    rows = commands()
    (paths,) = [param for param in rows["check"]["params"] if param["opts"] == ["paths"]]
    (key,) = [param for param in rows["config get"]["params"] if param["opts"] == ["key"]]
    assert (paths["required"], paths["repeatable"]) == (False, True)
    assert (key["required"], key["repeatable"]) == (True, False)


def test_the_globals_are_walked_from_the_root_callback() -> None:
    flags = {opt for param in catalog.globals_() for opt in param["opts"]}
    assert {"-p", "--json", "--events", "--color", "--no-input", "-v", "-q", "--version"} <= flags


def test_the_exit_table_is_the_five_codes_the_product_publishes() -> None:
    assert [row["exit"] for row in catalog.exits()] == [0, 1, 2, 3, 130]


def test_the_document_joins_the_two_halves() -> None:
    written = catalog.document()
    assert {row["code"] for row in written["errors"]} == {code.value for code in ErrorCode}
    assert {row["code"] for row in written["findings"]} == {code.value for code in Code}


def test_a_finding_names_the_keys_that_declare_they_decide_it() -> None:
    """The relation is declared once, on the keys, so every finding row reads it in reverse."""
    rows = {row["code"]: row["decides"] for row in catalog.findings()}
    for key in KEYS:
        for code in key.decides:
            assert key.id in rows[code.value], f"{key.id} decides {code.value} and the row omits it"
    for code, keys in rows.items():
        for key in keys:
            assert Code(code) in BY_ID[key].decides, f"{code} names {key}, which does not decide it"


def test_every_result_name_is_answerable() -> None:
    for name in RESULTS:
        assert catalog.named(name)["title"]


@pytest.mark.parametrize("name", ["finding", "error", "event", "settings", "page", "cues"])
def test_every_other_contract_name_is_answerable(name: str) -> None:
    assert catalog.named(name)


def test_a_name_that_is_not_a_contract_raises() -> None:
    with pytest.raises(KeyError):
        catalog.named("nope")


def test_the_rendered_settings_document_names_the_committed_schema_s_keys() -> None:
    committed = json.loads((SCHEMA_DIR / "decktalk.json").read_text(encoding="utf-8"))
    published = {key["id"] for key in catalog.settings_schema()["keys"]}
    assert published == set(_ids(committed["properties"]))


def _ids(properties: dict[str, object], prefix: str = "") -> list[str]:
    """Every dotted key the committed schema declares, which is what the rendered one is held to."""
    found: list[str] = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        inside = schema.get("properties")
        if isinstance(inside, dict):
            found += _ids(inside, f"{prefix}{name}.")
        elif "x-scope" in schema:
            found.append(f"{prefix}{name}")
    return found


def test_the_machine_document_is_the_machine_scoped_keys() -> None:
    machine = catalog.settings_schema(scope=Scope.MACHINE)["keys"]
    assert machine
    assert all(key["scope"] == "machine" for key in machine)


def test_nothing_outside_the_command_line_imports_the_application() -> None:
    source = (Path(__file__).resolve().parents[3] / "src" / "decktalk").rglob("*.py")
    offenders = [
        path for path in source if "cli" not in path.parts and "decktalk.cli" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_nothing_named_catalog_is_published() -> None:
    assert not [name for name in decktalk.__all__ if "catalog" in name.lower()]
    assert not hasattr(decktalk, "catalog_")


def test_every_schema_the_library_owns_is_named_once() -> None:
    assert set(catalog.SCHEMAS) == {*RESULTS, "event", "finding"}


def test_every_result_schema_names_its_own_model() -> None:
    for name in RESULTS:
        assert catalog.SCHEMAS[name]()["title"] == RESULTS[name].__name__


def test_every_finding_code_is_a_row_with_its_sentence_and_its_page() -> None:
    rows = catalog.finding_codes()
    assert [row["code"] for row in rows] == [code.value for code in Code]
    for row, code in zip(rows, Code, strict=True):
        assert row["sentence"] == code.sentence
        assert row["severity"] == code.severity.value
        assert row["raised_by"] == code.raised_by.value
        assert row["docs"] == code.url


def test_every_error_code_is_a_row_with_the_exit_it_takes() -> None:
    rows = catalog.error_codes()
    assert [row["code"] for row in rows] == [code.value for code in ErrorCode]
    assert [row["exit"] for row in rows] == [code.exit_code for code in ErrorCode]


def test_every_stage_is_a_row_with_what_it_reads_and_writes() -> None:
    rows = catalog.stages()
    assert [row["stage"] for row in rows] == [spec.stage.value for spec in PIPELINE]
    for row, spec in zip(rows, PIPELINE, strict=True):
        assert row["reads"] == [artifact.value for artifact in spec.reads]
        assert row["writes"] == [artifact.value for artifact in spec.writes]


def test_the_finding_schema_carries_the_whole_code_list() -> None:
    schema = catalog.SCHEMAS["finding"]()
    assert set(schema["$defs"]["Code"]["enum"]) == {code.value for code in Code}


def test_the_error_schema_carries_the_whole_code_list() -> None:
    schema = catalog.SCHEMAS["error"]()
    assert set(schema["$defs"]["ErrorCode"]["enum"]) == {code.value for code in ErrorCode}


def test_the_event_schema_is_discriminated_by_the_event_name() -> None:
    schema = catalog.SCHEMAS["event"]()
    assert schema["discriminator"]["propertyName"] == "event"
