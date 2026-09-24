"""The walk over the parser, and the join with what the library publishes about itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.cli import catalog
from decktalk.errors import ErrorCode
from decktalk.findings import Code
from decktalk.results import RESULTS

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
        for param in row["params"]:  # ty: ignore[not-iterable]
            assert set(param) >= set(PARAM_KEYS)


def test_the_globals_are_walked_from_the_root_callback() -> None:
    flags = {opt for param in catalog.globals_() for opt in param["opts"]}
    assert {"-p", "--json", "--events", "--color", "--no-input", "-v", "-q", "--version"} <= flags


def test_the_exit_table_is_the_five_codes_the_product_publishes() -> None:
    assert [row["exit"] for row in catalog.exits()] == [0, 1, 2, 3, 130]


def test_the_document_joins_the_two_halves() -> None:
    written = catalog.document()
    assert {row["code"] for row in written["errors"]} == {code.value for code in ErrorCode}
    assert {row["code"] for row in written["findings"]} == {code.value for code in Code}


def test_every_result_name_is_answerable() -> None:
    for name in RESULTS:
        assert catalog.named(name)["title"]


@pytest.mark.parametrize("name", ["finding", "error", "event", "settings", "page", "project"])
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
    machine = catalog.settings_schema(machine=True)["keys"]
    assert machine
    assert all(key["scope"] == "machine" for key in machine)


def test_nothing_outside_the_command_line_imports_the_application() -> None:
    source = (Path(__file__).resolve().parents[3] / "src" / "decktalk").rglob("*.py")
    offenders = [
        path for path in source if "cli" not in path.parts and "decktalk.cli" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
