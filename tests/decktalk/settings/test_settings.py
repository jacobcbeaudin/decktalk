"""The settings: how the five layers stack, what each refuses, and what every key publishes.

The test that earns its keep is the boundary sweep. It builds the same value five ways at each edge
of every key and asserts that the loader and a JSON Schema validator reach the same verdict, which
is the only mechanism that keeps the published range and the enforced range one range. Everything
else here holds a rule the design states in a sentence.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from decktalk.errors import InputError
from decktalk.findings import Code
from decktalk.results import Nature, Scope, Source
from decktalk.settings import BY_ID, DOCUMENT_TABLES, KEYS, SHARED_TABLES, Settings
from decktalk.settings.layers import (
    load,
    value_of,
)
from decktalk.settings.numbers import NUMBERS_BY_ID
from decktalk.tomlmap import Bounds, Key
from support.paths import REPO

SCHEMA = REPO / "schemas" / "v1" / "decktalk.json"


def schema() -> dict[str, Any]:
    """The committed whole-file schema, which is what the loader is held equal to."""
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def subschema(document: dict[str, Any], key_id: str) -> dict[str, Any]:
    """One key's own subschema, found by walking the tables of its dotted id."""
    body = document["properties"]
    for part in key_id.split("."):
        body = body[part] if "properties" not in body else body["properties"][part]
        body = body.get("properties", body)
    return body


def accepts(document: dict[str, Any], key_id: str, value: object) -> bool:
    """Whether the published schema admits this value for this key."""
    try:
        jsonschema.validate(value, subschema(document, key_id))
    except jsonschema.ValidationError:
        return False
    return True


def loads(key_id: str, value: object) -> bool:
    """Whether the loader admits this value for this key, written in the file the key belongs in."""
    table: dict[str, Any] = {}
    holder = table
    parts = key_id.split(".")
    for part in parts[:-1]:
        holder = holder.setdefault(part, {})
    holder[parts[-1]] = value
    machine = BY_ID[key_id].scope is Scope.MACHINE
    try:
        load(machine=table if machine else {}, project={} if machine else table, environ={})
    except InputError:
        return False
    return True


def values(key_id: str) -> st.SearchStrategy[object]:
    """Every value worth judging for a key, as its own type or as a TOML array of that type."""
    key = BY_ID[key_id]
    assert key.bounds is not None
    if key.bounds.items is not None:
        return st.lists(around(key.bounds.items), max_size=3)
    return around(key.bounds, words=key.annotation is str)


def around(bounds: Bounds, *, words: bool = False) -> st.SearchStrategy[object]:
    """A range's edges exactly, and numbers of either type on each side of every edge.

    A float key is also handed integers and an integer key floats, because TOML writes both, and
    JSON Schema counts a float with no fraction such as `320.0` as an integer, as the loader does.
    """
    if bounds.pattern is not None:
        # A value that matches, the same value with a filter or a rule after it, and any text at all.
        matching = st.from_regex(bounds.pattern, fullmatch=True)
        return matching | matching.map(lambda value: f"{value}:s=1x1,movie=/etc/passwd") | st.text()
    if bounds.enum is not None:
        members = st.sampled_from(bounds.enum)
        if words:
            return members | st.text()
        return members | st.integers(min_value=min(bounds.enum) - 2, max_value=max(bounds.enum) + 2)
    edges = [edge for edge in (bounds.ge, bounds.gt, bounds.le) if edge is not None]
    return st.sampled_from(edges).flatmap(
        lambda edge: (
            st.just(edge)
            | st.floats(min_value=edge - 1, max_value=edge + 1)
            | st.integers(min_value=int(edge) - 2, max_value=int(edge) + 2)
        )
    )


SWEEP_EXAMPLES = 40
"""How many values each key is judged at, which reaches both sides of every edge of every key."""

SWEPT = [key.id for key in KEYS if key.bounds is not None]
"""Every key with a range, whether a span, a set or the range of each item of an array."""


class TestThePublishedRangeIsTheEnforcedRange:
    """The one mechanism that keeps an agent's trust in a bound worth having."""

    @pytest.mark.parametrize("key_id", SWEPT)
    @settings(max_examples=SWEEP_EXAMPLES)
    @given(data=st.data())
    def test_the_loader_and_the_schema_judge_every_edge_alike(self, key_id: str, data: st.DataObject) -> None:
        value = data.draw(values(key_id))
        assert loads(key_id, value) is accepts(schema(), key_id, value), f"{key_id} at {value!r}"

    @pytest.mark.parametrize("key_id", SWEPT)
    def test_a_value_of_the_wrong_type_is_refused_by_both(self, key_id: str) -> None:
        wrong = "a string" if BY_ID[key_id].annotation is not str else 17
        assert loads(key_id, wrong) is False
        assert accepts(schema(), key_id, wrong) is False

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_default_validates_against_its_own_subschema(self, key: Key) -> None:
        value = list(key.default) if isinstance(key.default, tuple) else key.default
        assert accepts(schema(), key.id, value), f"{key.id} default {value!r} is outside its published range"

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_default_is_inside_its_own_safe_range(self, key: Key) -> None:
        assert key.bounds is None or key.bounds.holds(key.default)


class TestTheRecordEveryKeyCarries:
    """A key that cannot be read whole is a key an agent cannot turn with confidence."""

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_key_says_what_it_changes_in_a_whole_sentence(self, key: Key) -> None:
        assert key.description.endswith(".")
        assert ";" not in key.description
        assert "—" not in key.description
        assert len(key.description.split()) > 3

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_hazard_is_a_whole_sentence_with_no_dash(self, key: Key) -> None:
        assert key.hazard is None or (key.hazard.endswith(".") and "—" not in key.hazard)

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_every_see_also_and_every_requires_resolves(self, key: Key) -> None:
        for name in key.see_also:
            assert name in BY_ID or name in NUMBERS_BY_ID, f"{key.id} sees {name}, which is neither"
        if key.requires is not None:
            left, _op, right = key.requires.split()
            for side in (left, right):
                assert side in BY_ID or side in NUMBERS_BY_ID or side.replace(".", "").isdigit()

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_a_key_that_is_not_chosen_says_what_produces_it(self, key: Key) -> None:
        assert key.source is Source.CHOSEN or key.evidence is not None

    @pytest.mark.parametrize("key", KEYS, ids=lambda key: key.id)
    def test_no_key_is_a_truth_or_a_derived_number(self, key: Key) -> None:
        assert key.nature in (Nature.TASTE, Nature.APPARATUS)

    def test_every_key_name_ends_in_its_own_unit_where_it_has_one(self) -> None:
        units = {"ms": "milliseconds", "seconds": "seconds", "dbfs": "dBFS", "percent": "percent", "luma": "luma"}
        for key in KEYS:
            last = key.name.rsplit("_", 1)[-1]
            if last in units:
                assert key.unit is not None, f"{key.id} names a unit it does not publish"

    def test_no_verdict_limit_is_machine_scoped(self) -> None:
        for key in KEYS:
            if key.scope is Scope.MACHINE:
                assert not key.decides, f"{key.id} decides a verdict per machine"

    def test_the_key_a_diagnostic_names_is_the_key_config_set_takes(self) -> None:
        for key in KEYS:
            assert BY_ID[key.id] is key
            assert key.environment.startswith("DECKTALK_")


class TestTheTables:
    """The table list is the vocabulary, so a name that belongs to two things is caught here."""

    def test_the_document_tables_and_the_tuning_tables_do_not_collide(self) -> None:
        tuning = {key.table.split(".")[0] for key in KEYS}
        assert not tuning & set(DOCUMENT_TABLES)

    def test_every_shared_table_is_a_table_some_key_sits_in_or_under(self) -> None:
        tables = {key.table for key in KEYS}
        for shared in SHARED_TABLES:
            assert shared in tables or any(table.startswith(f"{shared}.") for table in tables)

    def test_no_key_name_still_says_sfx(self) -> None:
        assert not [key.id for key in KEYS if "sfx" in key.id]

    def test_the_value_of_a_dotted_key_is_the_value_the_tree_holds(self) -> None:
        settings = Settings()
        assert value_of(settings, "score.music.model") == settings.score.music.model

    def test_the_findings_a_key_decides_are_real_codes(self) -> None:
        for key in KEYS:
            for code in key.decides:
                assert isinstance(code, Code)
