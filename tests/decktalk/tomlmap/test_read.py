"""Reading a mapping into typed values, and a table that names the file, the table and the line."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from hypothesis import strategies as st

from decktalk.errors import InputError
from decktalk.findings import Code
from decktalk.results import Scope
from decktalk.tomlmap import Bounds, Nature, Source, tune
from decktalk.tomlmap.read import Table, from_mapping, read_value

FINITE = st.floats(allow_nan=False, allow_infinity=False)
"""Every finite number, which is what a range is written in and what its edges are."""


@dataclass(frozen=True)
class Inner:
    """One nested table, so the walk over a tree is exercised rather than assumed."""

    count: int = tune(3, "How many.", bounds=Bounds(ge=1, le=10), unit="things")
    ratio: float = tune(0.5, "What share.", bounds=Bounds(ge=0, le=1))
    on: bool = tune(True, "Whether to.")


@dataclass(frozen=True)
class Outer:
    """The tree under test, whose one key carries every published field a key can carry."""

    name: str = tune(
        "x",
        "What it is called.",
        bounds=Bounds(enum=("x", "y")),
        typed=Bounds(),
        unit="word",
        scope=Scope.MACHINE,
        nature=Nature.APPARATUS,
        source=Source.STATED,
        evidence="a command",
        hazard="A wrong word breaks it.",
        requires="inner.count >= 1",
        see_also=("inner.count",),
        decides=(Code.CUE_OFF,),
    )
    delays: tuple[float, ...] = tune(
        (0.7, 1.5),
        "When to look.",
        bounds=Bounds(min_items=1, items=Bounds(gt=0, le=10)),
    )
    inner: Inner = Inner()


class TestLoading:
    """The mapping decides the value, the environment converts it, and the range refuses it."""

    def test_a_mapping_and_an_environment_stack_in_that_order(self) -> None:
        built = from_mapping(Outer, base={"inner": {"count": 5}}, environ={"DECKTALK_INNER_RATIO": "0.25"})
        assert built.inner.count == 5
        assert built.inner.ratio == 0.25
        assert built.inner.on is True

    def test_a_value_outside_the_safe_range_is_refused_by_its_own_sentence(self) -> None:
        with pytest.raises(InputError, match="must be between 1 and 10"):
            from_mapping(Outer, base={"inner": {"count": 11}}, environ={})

    def test_a_refusal_at_the_edge_carries_the_hazard_as_its_hint(self) -> None:
        with pytest.raises(InputError) as caught:
            from_mapping(Outer, base={"name": "z"}, environ={})
        assert caught.value.hint == "A wrong word breaks it."

    def test_a_mapping_value_of_the_wrong_type_is_refused_rather_than_converted(self) -> None:
        with pytest.raises(InputError, match="not an int"):
            from_mapping(Outer, base={"inner": {"count": "5"}}, environ={})

    def test_an_array_is_judged_element_by_element(self) -> None:
        with pytest.raises(InputError, match="must be above 0"):
            from_mapping(Outer, base={"delays": [0.7, -1.0]}, environ={})

    def test_one_value_answers_the_same_way_from_a_file_and_from_the_environment(self) -> None:
        bounds = next(f for f in Inner.__dataclass_fields__.values() if f.name == "count").metadata["bounds"]
        assert read_value(int, "5", where="inner.count", bounds=bounds, hazard=None, from_env=True, said="a test") == 5
        with pytest.raises(InputError, match="must be between 1 and 10"):
            read_value(int, "11", where="inner.count", bounds=bounds, hazard=None, from_env=True, said="a test")


class TestTable:
    """One mapping read key by key, with the file, the table and the line in every refusal."""

    def test_a_refusal_carries_the_file_and_the_line_the_key_sits_on(self, tmp_path: Path) -> None:
        text = "[video]\nwidth = 1920\npreset = 4\n"
        table = Table({"preset": 4}, "decktalk.toml: [video]", tmp_path / "decktalk.toml", text=text, table="video")
        with pytest.raises(InputError) as caught:
            table.get_str("preset", required=True)
        assert caught.value.location is not None
        assert caught.value.location.line == 3

    def test_a_required_key_that_is_missing_names_itself(self) -> None:
        with pytest.raises(InputError, match="is required"):
            Table({}, "[video]").get_str("preset", required=True)

    def test_a_path_outside_the_project_is_refused(self) -> None:
        with pytest.raises(InputError, match="outside the project"):
            Table({"file": "/etc/passwd"}, "[mix]").get_path("file")

    def test_a_boolean_is_never_read_as_a_number(self) -> None:
        with pytest.raises(InputError, match="must be a number"):
            Table({"crf": True}, "[video]").get_int("crf")

    def test_an_array_of_tables_refuses_a_row_that_is_not_one(self) -> None:
        with pytest.raises(InputError, match="must be a table"):
            Table({"section": [1]}, "[project]").get_tables("section")
