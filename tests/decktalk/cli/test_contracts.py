"""`decktalk schema`, which is the one command that prints a contract rather than a result."""

from __future__ import annotations

import json

import pytest

from decktalk.cli import catalog, contracts
from decktalk.page import T0_SIGNAL, Q

KEY_KEYS = ("id", "description", "default", "bounds", "scope")
"""What every published setting carries, which is the parameter list an agent changes."""


def test_the_schema_command_is_registered() -> None:
    assert callable(contracts.schema)


def test_bare_schema_prints_the_whole_instruction_set(run) -> None:
    ran = run("schema")
    assert ran.exit_code == 0
    written = json.loads(ran.out)
    assert set(written) == {"commands", "globals", "exits", "errors", "findings", "stages"}


def test_a_schema_document_carries_no_reserved_keys(run) -> None:
    written = json.loads(run("schema", "finding").out)
    assert written["title"] == "Finding"
    assert "ok" not in written


def test_the_names_list_the_results_first_and_each_name_once() -> None:
    assert catalog.names()[-5:] == ("event", "finding", "cues", "page", "setting")
    assert len(set(catalog.names())) == len(catalog.names())


@pytest.mark.parametrize("name", catalog.names())
def test_every_published_name_answers(run, name: str) -> None:
    ran = run("schema", name)
    assert ran.exit_code == 0
    assert json.loads(ran.out)


def test_the_settings_document_publishes_every_setting_with_its_range(run) -> None:
    written = json.loads(run("schema", "setting").out)
    assert written["keys"]
    assert all(set(KEY_KEYS) <= set(key) for key in written["keys"])
    assert written["numbers"]


def test_the_machine_filter_is_a_subset_of_the_whole(run) -> None:
    everything = {key["id"] for key in json.loads(run("schema", "setting").out)["keys"]}
    machine = {key["id"] for key in json.loads(run("schema", "setting", "--scope", "machine").out)["keys"]}
    assert machine < everything


def test_the_two_scopes_split_the_keys_between_the_two_files(run) -> None:
    everything = {key["id"] for key in json.loads(run("schema", "setting").out)["keys"]}
    scoped = {
        scope: {key["id"] for key in json.loads(run("schema", "setting", "--scope", scope).out)["keys"]}
        for scope in ("project", "machine")
    }
    assert scoped["project"] | scoped["machine"] == everything
    assert not scoped["project"] & scoped["machine"]


def test_the_page_document_publishes_every_attribute(run) -> None:
    written = json.loads(run("schema", "page").out)
    assert {"data-in", "data-slide"} <= {row["name"] for row in written["attributes"]}
    assert written["measurable_span_seconds"] > 0


def test_the_page_document_publishes_every_query_key_and_the_clock_signal(run) -> None:
    """A tool that opens a page by URL reads the keys it may pass from here rather than from the runtime."""
    written = json.loads(run("schema", "page").out)
    assert set(written["query"]) == {key.value for key in Q}
    assert all(written["query"].values())
    assert written["t0_signal"] == T0_SIGNAL


def test_the_cues_document_publishes_the_cue_row(run) -> None:
    written = json.loads(run("schema", "cues").out)
    assert written["file"] == "cues.json"
    rows = {row["key"]: row for row in written["sections"]["cues"]}
    assert {"id", "phrase"} <= rows.keys()
    assert "line" not in rows, "the loader fills the line, so an author never writes it"
    assert rows["offset_seconds"] == {"key": "offset_seconds", "type": "number", "default": 0.0}


def test_the_set_parameter_points_at_the_key_space(run) -> None:
    written = json.loads(run("schema").out)
    build = next(row for row in written["commands"] if row["command"] == "build")
    override = next(param for param in build["params"] if "--set" in param["opts"])
    assert override["keys"] == "setting"


def test_a_name_that_is_not_a_contract_is_refused_with_the_list(run) -> None:
    ran = run("schema", "nope")
    assert ran.exit_code == 2
    assert "names no contract" in ran.err
    assert "setting" in ran.err


def test_schema_reads_no_project_and_writes_nothing(run, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert run("schema", "event").exit_code == 0
    assert list(tmp_path.iterdir()) == []
