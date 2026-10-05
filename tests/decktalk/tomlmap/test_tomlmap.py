"""Declaring a key: the range it publishes and the registry a dataclass tree yields."""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given
from hypothesis import strategies as st

from decktalk.findings import Code
from decktalk.results import Scope
from decktalk.tomlmap import PUBLISHED, Bounds, Nature, Source, registry, tune

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


class TestBounds:
    """The range is data, so the loader and the schema read one statement rather than two."""

    @given(st.data(), FINITE, FINITE)
    def test_a_bound_holds_exactly_at_its_own_edge(self, data: st.DataObject, one: float, other: float) -> None:
        """The value is drawn from the two edges as often as from anywhere else, which is where a bound slips."""
        low, high = sorted((one, other))
        value = data.draw(st.sampled_from((low, high)) | FINITE)
        assert Bounds(ge=low, le=high).holds(value) is (low <= value <= high)
        assert Bounds(gt=low, le=high).holds(value) is (low < value <= high)
        assert Bounds(enum=(low, high)).holds(value) is (value in (low, high))

    @given(st.lists(FINITE, max_size=4), st.integers(min_value=0, max_value=3))
    def test_an_array_holds_when_it_is_long_enough_and_every_item_holds(self, values: list[float], least: int) -> None:
        held = len(values) >= least and all(value > 0 for value in values)
        assert Bounds(min_items=least, items=Bounds(gt=0)).holds(tuple(values)) is held

    def test_a_range_says_itself_in_words(self) -> None:
        assert Bounds(ge=0, le=100).sentence == "must be between 0 and 100"
        assert Bounds(gt=0).sentence == "must be above 0"
        assert Bounds(enum=(25, 30)).sentence == "must be one of 25, 30"
        assert Bounds().sentence == "takes any value of its type"

    def test_a_range_emits_the_json_schema_keywords_it_states(self) -> None:
        assert Bounds(ge=1, le=9).json_schema() == {"minimum": 1, "maximum": 9}
        assert Bounds(gt=0, le=1).json_schema() == {"exclusiveMinimum": 0, "maximum": 1}
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
