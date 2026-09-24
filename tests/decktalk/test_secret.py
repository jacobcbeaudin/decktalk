"""A value that may be used and never shown, attacked by every way a value usually escapes."""

from __future__ import annotations

import copy
import json
import logging
import pickle
import pprint
import traceback
from dataclasses import asdict, dataclass

import pytest
from pydantic_core import PydanticSerializationError

from decktalk.artifacts import Stored
from decktalk.secret import Secret

VALUE = "sk_sentinel_key_that_must_never_print"


@dataclass(frozen=True)
class Holder:
    """A dataclass that holds one, which is how every real holder holds one."""

    name: str
    key: Secret


class Leak(Stored):
    """An artifact that tried to carry one, which is the one door every file under `build/` goes through."""

    name: str
    key: object


def test_the_value_is_readable_only_through_reveal():
    """One call reads a secret, so every place a secret leaves DeckTalk is one call a reader finds."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    assert secret.reveal() == VALUE
    assert secret.name == "ELEVENLABS_API_KEY"


def test_a_secret_prints_as_the_variable_it_came_from():
    """A secret is named by its variable name and never by its value."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    assert repr(secret) == "<secret ELEVENLABS_API_KEY>"
    assert str(secret) == "<secret ELEVENLABS_API_KEY>"
    assert repr(Secret(VALUE)) == "<secret>"


@pytest.mark.parametrize(
    "show",
    [
        repr,
        str,
        lambda s: f"{s}",
        lambda s: f"{s!r}",
        lambda s: f"{s!s}",
        lambda s: "%s" % (s,),  # noqa: UP031  (the spelling a log call uses is the point)
        lambda s: "%r" % (s,),  # noqa: UP031
        "{}".format,
        pprint.pformat,
        lambda s: json.dumps(s, default=str),
        lambda s: repr(Holder("eleven", s)),
        lambda s: json.dumps({"name": "eleven", "key": s}, default=str),
    ],
)
def test_no_ordinary_way_of_showing_a_value_shows_this_one(show):
    """These are the ways a value reaches a terminal, a log or a payload, and none of them works."""
    assert VALUE not in show(Secret(VALUE, "ELEVENLABS_API_KEY"))


def test_a_secret_refuses_every_copy_protocol():
    """`pickle` carries a value between processes, so a worker pool added later carries none."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    attempts = (
        secret.__getstate__,
        lambda: secret.__reduce_ex__(2),
        lambda: pickle.dumps(secret),
        lambda: copy.copy(secret),
        lambda: copy.deepcopy(secret),
    )
    for attempt in attempts:
        with pytest.raises(TypeError, match="not serializable"):
            attempt()
    with pytest.raises(TypeError):
        asdict(Holder("eleven", secret))  # asdict deep-copies, so it refuses as well


def test_a_secret_is_not_json_and_is_refused_rather_than_written(tmp_path):
    """The artifact writer is how every build file reaches the disk, so one holding a secret raises instead."""
    with pytest.raises(TypeError):
        json.dumps({"key": Secret(VALUE)})
    out = tmp_path / "leak.json"
    with pytest.raises(PydanticSerializationError):
        Leak(name="eleven", key=Secret(VALUE)).write(out)
    assert not out.exists()
    assert not list(tmp_path.iterdir())


def test_a_traceback_of_a_failure_holding_one_shows_no_value():
    """An error reaches a log and a terminal, and `-v` prints the whole traceback with its locals."""
    secret = Secret(VALUE, "ELEVENLABS_API_KEY")
    try:
        raise RuntimeError(f"could not reach the provider with {secret}")
    except RuntimeError as exc:
        text = "".join(traceback.format_exception(exc))
    assert VALUE not in text and "<secret ELEVENLABS_API_KEY>" in text


def test_a_logging_call_shows_no_value(caplog):
    """Every log line goes to stderr, where a CI job keeps it."""
    with caplog.at_level(logging.WARNING, logger="decktalk"):
        logging.getLogger("decktalk").warning("key %s is set", Secret(VALUE, "ELEVENLABS_API_KEY"))
    assert VALUE not in caplog.text and "<secret ELEVENLABS_API_KEY>" in caplog.text


def test_an_unset_variable_is_falsy_and_a_set_one_is_not():
    """A caller checks whether a variable is set before it spends, and never by reading the value."""
    assert not Secret("", "ELEVENLABS_API_KEY")
    assert Secret(VALUE, "ELEVENLABS_API_KEY")
    assert len(Secret(VALUE)) == len(VALUE)


def test_two_secrets_are_equal_by_value_and_a_plain_string_is_never_one():
    """Comparing against a plain string would be the leak the type exists to stop."""
    assert Secret(VALUE, "A") == Secret(VALUE, "B")
    assert Secret(VALUE) != Secret("other")
    assert Secret(VALUE) != VALUE
    assert len({Secret(VALUE), Secret(VALUE)}) == 1
