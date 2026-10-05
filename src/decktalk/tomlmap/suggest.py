"""The warning for a key nobody reads, with the key it most likely meant."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping
from typing import Any


def unknown_key_message(
    key: str, known: Iterable[str], where: str, *, at: str = "", anywhere: Iterable[str] = ()
) -> str:
    """The warning for one key that DeckTalk does not read, with the closest known key when one is near.

    `anywhere` is every dotted key a reader could have meant and `at` is the dotted name of this key's
    table. A key the typed one names is offered first, wherever it lives, because a key is often
    written under a table it does not live in. Otherwise a name in the same
    table spelled nearly like it is offered, and then the nearest key anywhere. A key in the same
    table is offered by its own name and any other in full.
    """
    dotted = f"{at}.{key}" if at else key
    named = named_key(dotted, anywhere)
    close = difflib.get_close_matches(key, sorted(known), n=1)
    near = named if named is not None else close[0] if close else nearest(dotted, anywhere)
    if near is not None and at and near.startswith(f"{at}.") and "." not in near.removeprefix(f"{at}."):
        near = near.removeprefix(f"{at}.")
    hint = f" (did you mean '{near}'?)" if near else ""
    return f"{where}: ignoring unknown key '{key}'{hint}."


def unknown_key_warnings(
    table: Mapping[str, Any], known: Iterable[str], where: str, *, at: str = "", anywhere: Iterable[str] = ()
) -> list[str]:
    """One warning per key in `table` that is not in `known`. An unknown key is ignored, not an error.

    A table nobody knows is warned about key by key, so each of its keys meets the key it most likely
    meant rather than the table being named once.
    """
    names = set(known)
    leaves = [
        leaf
        for key in sorted(set(table) - names)
        for leaf in (
            [f"{key}.{inner}" for inner in _leaves(table[key])]
            if isinstance(table[key], Mapping) and table[key]
            else [key]
        )
    ]
    return [unknown_key_message(leaf, names, where, at=at, anywhere=anywhere) for leaf in leaves]


def _leaves(table: Mapping[str, Any], prefix: str = "") -> list[str]:
    """Every scalar of a nested table by its dotted name inside that table."""
    out: list[str] = []
    for name, value in table.items():
        if isinstance(value, Mapping) and value:
            out += _leaves(value, f"{prefix}{name}.")
        else:
            out.append(f"{prefix}{name}")
    return out


NAMED_PART_MIN = 3
"""How long the last part of a mistyped key must be before a key whose own last part holds it is offered.

A shorter part, such as `db` or `a`, sits inside too many names to say which one was meant.
"""


def nearest(key: str, known: Iterable[str]) -> str | None:
    """The key one nobody knows most likely meant, or None when none is near.

    The last part of a key names the thing it sets, and a person who remembers that thing and not
    its table writes it under the wrong one, as `record.fps` for `video.fps`, or as
    `elevenlabs.music_model` for `score.music.model`. So a key
    named by the typed one is offered first, then a key whose last part is spelled nearly like it,
    the closest whole key among them when there are several, and the closest spelling of the whole
    key only when none exists.
    """
    names = sorted(known)
    part = key.rsplit(".", 1)[-1]
    named = named_key(key, names)
    if named is not None:
        return named
    alike = set(difflib.get_close_matches(part, {name.rsplit(".", 1)[-1] for name in names}))
    holding = [name for name in names if name.rsplit(".", 1)[-1] in alike]
    close = difflib.get_close_matches(key, holding, n=1, cutoff=0) if holding else []
    close = close or difflib.get_close_matches(key, names, n=1)
    return close[0] if close else None


def named_key(key: str, known: Iterable[str]) -> str | None:
    """The closest key whose last part holds the typed last part, or whose whole name holds each of its words.

    Either is a key the typed one names rather than one spelled like it, which is what lets a key
    written under the wrong table find the table it lives in.
    """
    names = sorted(known)
    part = key.rsplit(".", 1)[-1]
    holding = [name for name in names if len(part) >= NAMED_PART_MIN and part in name.rsplit(".", 1)[-1]]
    holding = holding or [name for name in names if _covers(name, part)]
    close = difflib.get_close_matches(key, holding, n=1, cutoff=0) if holding else []
    return close[0] if close else None


def _covers(name: str, part: str) -> bool:
    """Whether every word of `part` opens a word of the dotted key `name`, so `effect_seconds` finds `effects`."""
    words = [word for word in part.split("_") if len(word) >= NAMED_PART_MIN]
    held = re.split(r"[._]", name)
    return len(words) > 1 and all(any(word.startswith(typed) for word in held) for typed in words)


def did_you_mean(key: str, known: Iterable[str]) -> str:
    """The key one nobody knows most likely meant, as a clause a refusal appends, or nothing when none is near."""
    near = nearest(key, known)
    return f" Did you mean '{near}'?" if near else ""
