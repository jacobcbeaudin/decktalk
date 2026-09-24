"""One mapping read into typed values, with declarative bounds, located errors and key hints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from decktalk.errors import InputError
from decktalk.findings import Code
from decktalk.results import Scope
from decktalk.tomlmap import (
    PUBLISHED,
    Bounds,
    Nature,
    Source,
    Table,
    did_you_mean,
    env_names,
    from_mapping,
    read_value,
    registry,
    tune,
    unknown_key_message,
    unknown_key_warnings,
)


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
        source=Source.MEASURED,
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


class TestBounds:
    """The range is data, so the loader and the schema read one statement rather than two."""

    @pytest.mark.parametrize(
        ("bounds", "value", "held"),
        [
            (Bounds(ge=0, le=100), 0, True),
            (Bounds(ge=0, le=100), -0.001, False),
            (Bounds(ge=0, le=100), 100, True),
            (Bounds(ge=0, le=100), 100.001, False),
            (Bounds(gt=0), 0, False),
            (Bounds(lt=1), 1, False),
            (Bounds(enum=("a", "b")), "c", False),
            (Bounds(min_items=1, items=Bounds(gt=0)), (), False),
            (Bounds(min_items=1, items=Bounds(gt=0)), (0.5, -1), False),
            (Bounds(min_items=1, items=Bounds(gt=0)), (0.5,), True),
        ],
    )
    def test_a_bound_holds_exactly_at_its_own_edge(self, bounds: Bounds, value: object, held: bool) -> None:
        assert bounds.holds(value) is held

    def test_a_range_says_itself_in_words(self) -> None:
        assert Bounds(ge=0, le=100).sentence == "must be between 0 and 100"
        assert Bounds(gt=0).sentence == "must be above 0"
        assert Bounds(enum=(25, 30)).sentence == "must be one of 25, 30"
        assert Bounds().sentence == "takes any value of its type"

    def test_a_range_emits_the_json_schema_keywords_it_states(self) -> None:
        assert Bounds(ge=1, le=9).json_schema() == {"minimum": 1, "maximum": 9}
        assert Bounds(gt=0, lt=1).json_schema() == {"exclusiveMinimum": 0, "exclusiveMaximum": 1}
        assert Bounds(enum=("a",)).json_schema() == {"enum": ["a"]}
        assert Bounds(min_items=1, items=Bounds(gt=0)).json_schema() == {
            "minItems": 1,
            "items": {"exclusiveMinimum": 0},
        }


class TestRegistry:
    """A key is declared once, and the walk is what turns that declaration into a published row."""

    def test_the_walk_names_every_key_of_a_tree_with_its_dotted_id(self) -> None:
        assert [key.id for key in registry(Outer)] == ["name", "delays", "inner.count", "inner.ratio", "inner.on"]

    def test_a_key_carries_every_field_a_surface_publishes(self) -> None:
        key = registry(Outer)[0]
        published = {
            "id": key.id,
            "description": key.description,
            "default": key.default,
            "typed": key.typed,
            "bounds": key.bounds,
            "enum": key.enum,
            "unit": key.unit,
            "decides": key.decides,
            "scope": key.scope,
            "nature": key.nature,
            "source": key.source,
            "evidence": key.evidence,
            "hazard": key.hazard,
            "requires": key.requires,
            "see_also": key.see_also,
            "environment": key.environment,
        }
        assert tuple(published) == PUBLISHED
        assert key.environment == "DECKTALK_NAME"
        assert (key.table, key.name) == ("", "name")
        assert registry(Outer)[2].table == "inner"

    def test_a_key_with_no_range_still_says_what_it_accepts(self) -> None:
        assert registry(Outer)[4].range == "takes any value of its type"


class TestLoading:
    """The mapping decides the value, the environment converts it, and the range refuses it."""

    def test_a_mapping_and_an_environment_stack_in_that_order(self) -> None:
        built = from_mapping(Outer, base={"inner": {"count": 5}}, prefixes=["t"], environ={"T_INNER_RATIO": "0.25"})
        assert built.inner.count == 5
        assert built.inner.ratio == 0.25
        assert built.inner.on is True

    def test_a_value_outside_the_safe_range_is_refused_by_its_own_sentence(self) -> None:
        with pytest.raises(InputError, match="must be between 1 and 10"):
            from_mapping(Outer, base={"inner": {"count": 11}}, prefixes=["t"], environ={})

    def test_a_refusal_at_the_edge_carries_the_hazard_as_its_hint(self) -> None:
        with pytest.raises(InputError) as caught:
            from_mapping(Outer, base={"name": "z"}, prefixes=["t"], environ={})
        assert caught.value.hint == "A wrong word breaks it."

    def test_a_mapping_value_of_the_wrong_type_is_refused_rather_than_converted(self) -> None:
        with pytest.raises(InputError, match="not an int"):
            from_mapping(Outer, base={"inner": {"count": "5"}}, prefixes=["t"], environ={})

    def test_an_array_is_judged_element_by_element(self) -> None:
        with pytest.raises(InputError, match="must be above 0"):
            from_mapping(Outer, base={"delays": [0.7, -1.0]}, prefixes=["t"], environ={})

    def test_one_value_answers_the_same_way_from_a_file_and_from_the_environment(self) -> None:
        declared = next(f for f in Inner.__dataclass_fields__.values() if f.name == "count")
        assert read_value(int, "5", where="inner.count", field=declared, from_env=True) == 5
        with pytest.raises(InputError, match="must be between 1 and 10"):
            read_value(int, "11", where="inner.count", field=declared, from_env=True)

    def test_every_environment_name_of_a_tree_is_named(self) -> None:
        assert env_names(Outer, "t") == {"T_NAME", "T_DELAYS", "T_INNER_COUNT", "T_INNER_RATIO", "T_INNER_ON"}


class TestMessages:
    """An unknown key is a warning with the nearest name, because a typo is the common failure."""

    def test_an_unknown_key_names_the_closest_one_that_is_known(self) -> None:
        assert "did you mean 'width'?" in unknown_key_message("widht", ["width", "height"], "[video]")

    def test_an_unknown_key_with_nothing_near_it_names_nothing(self) -> None:
        assert unknown_key_message("zzz", ["width"], "[video]") == "[video]: ignoring unknown key 'zzz'."

    def test_one_warning_per_unknown_key_in_a_table(self) -> None:
        assert len(unknown_key_warnings({"width": 1, "a": 2, "b": 3}, ["width"], "[video]")) == 2

    def test_the_did_you_mean_clause_is_empty_when_nothing_is_near(self) -> None:
        assert did_you_mean("verify.cue_offset_maks_ms", ["verify.cue_offset_max_ms"]) == (
            " Did you mean 'verify.cue_offset_max_ms'?"
        )
        assert did_you_mean("zzzzzzzz", ["verify.cue_offset_max_ms"]) == ""


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
