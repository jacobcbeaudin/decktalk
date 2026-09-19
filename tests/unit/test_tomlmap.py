"""One mapping read into typed values, with located errors, key hints and each field's range."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from decktalk.errors import ConfigError
from decktalk.tomlmap import (
    ABOVE_ZERO,
    Check,
    Table,
    env_names,
    from_mapping,
    unknown_key_message,
    unknown_key_warnings,
)


def tune[T](default: T, check: Check | None = None) -> T:
    """The settings module's own field helper, minus the sentence the reference prints."""
    return field(default=default, metadata={"check": check})


@dataclass(frozen=True)
class Inner:
    count: int = tune(3, ABOVE_ZERO)
    ratio: float = tune(0.5)
    on: bool = tune(True)
    name: str = tune("medium")
    delays: tuple[float, ...] = tune((0.7, 1.5))
    maybe: str | None = tune(None)


@dataclass(frozen=True)
class Outer:
    inner: Inner = field(default_factory=Inner)


# ---- a value written in a file --------------------------------------------------------------


def test_a_mapping_value_has_to_be_the_type_its_author_wrote():
    """A file carries typed values, so a conversion here would read a value nobody typed."""
    for raw, wanted in [("25", "a str is not an int"), (25.7, "a float is not an int"), (True, "a boolean")]:
        with pytest.raises(ConfigError) as info:
            from_mapping(Outer, base={"inner": {"count": raw}}, prefixes=["decktalk"], environ={})
        assert "[inner] count" in str(info.value) and wanted in str(info.value)


def test_a_whole_number_written_for_a_number_field_widens_and_nothing_else_does():
    """`ratio = 1` is the number one, while `count = 1.0` is a float an author did not mean as an int."""
    built = from_mapping(Outer, base={"inner": {"ratio": 1}}, prefixes=["decktalk"], environ={})
    assert built.inner.ratio == 1.0 and isinstance(built.inner.ratio, float)
    with pytest.raises(ConfigError, match="a float is not an int"):
        from_mapping(Outer, base={"inner": {"count": 1.0}}, prefixes=["decktalk"], environ={})


def test_a_boolean_is_never_a_number_and_a_number_is_never_a_boolean():
    """TOML's `true` is not 1, and reading it as one would turn a typo into a working setting."""
    with pytest.raises(ConfigError, match="a boolean is not a number"):
        from_mapping(Outer, base={"inner": {"count": True}}, prefixes=["decktalk"], environ={})
    with pytest.raises(ConfigError, match="a int is not a boolean"):
        from_mapping(Outer, base={"inner": {"on": 1}}, prefixes=["decktalk"], environ={})


def test_an_array_field_refuses_a_string_and_reads_a_list():
    """A string is iterable, so without the refusal `delays = "0.7"` would become three characters."""
    built = from_mapping(Outer, base={"inner": {"delays": [0.2, 1.0]}}, prefixes=["decktalk"], environ={})
    assert built.inner.delays == (0.2, 1.0)
    with pytest.raises(ConfigError, match="a str is not an array"):
        from_mapping(Outer, base={"inner": {"delays": "0.7,1.5"}}, prefixes=["decktalk"], environ={})


def test_an_optional_field_reads_its_inner_type():
    built = from_mapping(Outer, base={"inner": {"maybe": "here"}}, prefixes=["decktalk"], environ={})
    assert built.inner.maybe == "here"
    with pytest.raises(ConfigError, match=r"\[inner\] maybe"):
        from_mapping(Outer, base={"inner": {"maybe": 7}}, prefixes=["decktalk"], environ={})


# ---- a value exported in the environment ------------------------------------------------------


def test_an_environment_variable_is_a_string_and_is_converted():
    """The one place a conversion belongs, because the environment carries nothing but strings."""
    env = {
        "DECKTALK_INNER_COUNT": "25",
        "DECKTALK_INNER_RATIO": "0.25",
        "DECKTALK_INNER_ON": "false",
        "DECKTALK_INNER_DELAYS": "0.7, 1.5",
    }
    built = from_mapping(Outer, base={}, prefixes=["decktalk"], environ=env)
    assert (built.inner.count, built.inner.ratio, built.inner.on) == (25, 0.25, False)
    assert built.inner.delays == (0.7, 1.5)


def test_an_environment_variable_wins_over_the_mapping():
    built = from_mapping(
        Outer, base={"inner": {"count": 9}}, prefixes=["decktalk"], environ={"DECKTALK_INNER_COUNT": "2"}
    )
    assert built.inner.count == 2


def test_an_environment_variable_that_is_not_the_field_type_is_a_located_error():
    with pytest.raises(ConfigError) as info:
        from_mapping(Outer, base={}, prefixes=["decktalk"], environ={"DECKTALK_INNER_COUNT": "high"})
    assert str(info.value).startswith("[inner] count: expected int, got 'high'")


def test_every_variable_the_loader_reads_can_be_listed():
    """The unknown-variable warning names the closest real one, so the list has to be complete."""
    assert env_names(Outer, "decktalk") == {
        "DECKTALK_INNER_COUNT",
        "DECKTALK_INNER_RATIO",
        "DECKTALK_INNER_ON",
        "DECKTALK_INNER_NAME",
        "DECKTALK_INNER_DELAYS",
        "DECKTALK_INNER_MAYBE",
    }


# ---- the range a field declares ---------------------------------------------------------------


def test_a_field_outside_its_range_fails_from_either_layer():
    """The rule is on the field, so it applies wherever the value came from."""
    for base, environ in [({"inner": {"count": 0}}, {}), ({}, {"DECKTALK_INNER_COUNT": "0"})]:
        with pytest.raises(ConfigError) as info:
            from_mapping(Outer, base=base, prefixes=["decktalk"], environ=environ)
        assert str(info.value) == "[inner] count: must be above zero, got 0"


def test_a_field_with_no_rule_takes_any_value_of_its_type():
    built = from_mapping(Outer, base={"inner": {"ratio": -4.0}}, prefixes=["decktalk"], environ={})
    assert built.inner.ratio == -4.0


# ---- the located reader and its hints ---------------------------------------------------------


def test_a_table_names_the_file_and_the_key_in_every_message():
    t = Table({"page": "a.html", "scene": 2, "on": True, "ratio": 1.5}, "decktalk.toml: [[section]] number=1")
    assert t.get_str("page", required=True) == "a.html"
    assert t.get_int("scene") == 2 and t.get_num("ratio") == 1.5 and t.get_bool("on") is True
    assert t.get_str("missing", "fallback") == "fallback" and t.get_int("missing") is None
    with pytest.raises(ConfigError, match=r"\[\[section\]\] number=1: 'gone' is required and is not there"):
        t.get_str("gone", required=True)
    with pytest.raises(ConfigError, match="'scene' must be str, got int"):
        t.get_str("scene")
    with pytest.raises(ConfigError, match="'on' must be a number, got a boolean"):
        t.get_num("on")


def test_an_unknown_key_is_a_warning_that_names_the_closest_one_it_knows():
    """A reader who mistyped a key is given the key DeckTalk does read, and nothing else."""
    message = unknown_key_message("presett", {"preset", "crf"}, "decktalk.toml: [video]")
    assert message == "decktalk.toml: [video]: ignoring unknown key 'presett' (did you mean 'preset'?)."
    assert "did you mean" not in unknown_key_message("zebra", {"preset"}, "decktalk.toml: [video]")
    assert unknown_key_warnings({"preset": 1, "zebra": 2}, {"preset"}, "x") == [
        unknown_key_message("zebra", {"preset"}, "x")
    ]
    assert unknown_key_warnings({"preset": 1}, {"preset"}, "x") == []
