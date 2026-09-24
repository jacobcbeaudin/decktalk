"""`decktalk schema`, which is the one command that prints a contract rather than a result."""

from __future__ import annotations

import json

import pytest

from decktalk.cli import catalog, contracts

KEY_KEYS = ("id", "description", "default", "bounds", "scope")
"""What every published knob carries, which is the Midjourney parameter list an agent turns."""


def test_the_schema_command_is_registered() -> None:
    assert callable(contracts.schema)


def test_bare_schema_prints_the_whole_instruction_set(run) -> None:
    ran = run("schema")
    assert ran.exit_code == 0
    written = json.loads(ran.out)
    assert set(written) == {"commands", "globals", "exits", "errors", "findings", "stages"}


def test_the_contract_carries_no_envelope(run) -> None:
    written = json.loads(run("schema").out)
    assert "schema" not in written
    assert "ok" not in written
    assert "findings" not in set(written) - {"findings"} or isinstance(written["findings"], list)


def test_a_schema_document_carries_no_reserved_keys(run) -> None:
    written = json.loads(run("schema", "finding").out)
    assert written["title"] == "Finding"
    assert "ok" not in written


@pytest.mark.parametrize("name", catalog.names())
def test_every_published_name_answers(run, name: str) -> None:
    ran = run("schema", name)
    assert ran.exit_code == 0
    assert json.loads(ran.out)


def test_the_settings_document_publishes_every_knob_with_its_range(run) -> None:
    written = json.loads(run("schema", "settings").out)
    assert written["keys"]
    assert all(set(KEY_KEYS) <= set(key) for key in written["keys"])
    assert written["numbers"]


def test_the_machine_filter_is_a_subset_of_the_whole(run) -> None:
    everything = {key["id"] for key in json.loads(run("schema", "settings").out)["keys"]}
    machine = {key["id"] for key in json.loads(run("schema", "settings", "--machine").out)["keys"]}
    assert machine < everything


def test_the_page_document_publishes_every_attribute(run) -> None:
    written = json.loads(run("schema", "page").out)
    assert {"data-in", "data-slide"} <= {row["name"] for row in written["attributes"]}
    assert written["measurable_span_seconds"] > 0


def test_the_project_document_publishes_the_cue_row(run) -> None:
    written = json.loads(run("schema", "project").out)
    assert written["file"] == "cues.json"
    assert {"cue", "on"} <= {row["key"] for row in written["sections"]["cues"]}


def test_the_set_parameter_points_at_the_key_space(run) -> None:
    written = json.loads(run("schema").out)
    build = next(row for row in written["commands"] if row["command"] == "build")
    override = next(param for param in build["params"] if "--set" in param["opts"])
    assert override["keys"] == "settings"


def test_a_name_that_is_not_a_contract_is_refused_with_the_list(run) -> None:
    ran = run("schema", "nope")
    assert ran.exit_code == 2
    assert "names no contract" in ran.err
    assert "settings" in ran.err


def test_schema_reads_no_project_and_writes_nothing(run, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert run("schema", "event").exit_code == 0
    assert list(tmp_path.iterdir()) == []
