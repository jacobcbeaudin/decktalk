"""A value that may be used and never shown: an API key, and every other value read from `.env`.

No value read from `.env` or from the environment ever enters a `--json` payload, a log line or an
error message, and a secret is named by its variable name and never by its value. Marking a
dataclass field `repr=False` keeps the value out of `repr` and out of nothing else, because
`dataclasses.asdict` and any walker over `fields()` hand it straight back, so a secret is held in
`Secret` instead.

A `Secret` prints as its variable name in angle brackets wherever a string is expected, it is not
JSON, so a writer refuses it rather than writing it, and `reveal()` is the one way to read the
value. It refuses `pickle` and every other copy protocol for the same reason, so a worker pool
added later carries no secret with it. Every place a secret leaves DeckTalk is one `reveal()` that
a reader can find.

A value that was revealed can still reach a sentence, through an f-string or a header map logged by
mistake, so a secret whose name says it is a credential, or that has no name, also registers its
value when it is made. A variable read beside the key that names a published thing, such as the
voice id, is held the same way and is not registered, because a reader needs it in a URL. Every event and every error
object is built through `redacted`, which replaces each registered value with the secret's name,
so no line of the stream and no error can hold one whichever path produced it. The registry only
grows, and it holds values the process already holds in memory.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

SHORTEST_REGISTERED = 8
"""Truth: the shortest value the registry holds, so a short value cannot erase ordinary words from every line."""

SECRET_ENDINGS = ("_KEY", "_TOKEN", "_SECRET")
"""How the name of an environment variable that holds a credential ends, by the common convention."""

SECRET_WORDS = ("PASSWORD",)
"""What the name of an environment variable that holds a credential contains, wherever in the name."""

_known: tuple[tuple[str, str], ...] = ()
"""Every registered value beside the name it prints as, longest first, so a value inside another is replaced last."""

_lock = threading.Lock()


def register(value: str, name: str = "") -> None:
    """Add one value to the registry, which `Secret` does for every value it holds."""
    global _known  # noqa: PLW0603  (one registry per process, replaced whole so a reader never sees half of it)
    if len(value) < SHORTEST_REGISTERED:
        return
    with _lock:
        if any(value == held for held, _ in _known):
            return
        _known = tuple(sorted((*_known, (value, name)), key=lambda pair: len(pair[0]), reverse=True))


def secret_name(name: str) -> bool:
    """Whether an environment variable's name says it holds a credential."""
    upper = name.upper()
    return upper.endswith(SECRET_ENDINGS) or any(word in upper for word in SECRET_WORDS)


def register_environment(environ: Mapping[str, str]) -> None:
    """Register every value of `environ` whose name says it is a credential, which a machine does as it is built.

    A host hands a key to a machine in its environment rather than in `.env`, so the key is
    registered here, before any run could be handed a sentence that holds it.
    """
    for name, value in environ.items():
        if secret_name(name):
            register(value, name)


def redact(text: str) -> str:
    """The text with every registered value replaced by the name of the secret that holds it."""
    for value, name in _known:
        if value in text:
            text = text.replace(value, f"<secret {name}>" if name else "<secret>")
    return text


def redacted(given: Any) -> Any:  # noqa: ANN401  (whatever a model is being built from)
    """What a model is being built from, with every registered value taken out of every string in it.

    A model already built, such as a finding inside a finding line, is taken apart and rebuilt only
    when it holds a registered value, so the common case costs one serialization and no copy.
    """
    if not _known:
        return given
    if isinstance(given, str):
        return redact(given)
    if isinstance(given, Mapping):
        return {key: redacted(value) for key, value in given.items()}
    if isinstance(given, list | tuple):
        return type(given)(redacted(value) for value in given)
    if isinstance(given, BaseModel):
        dumped = given.model_dump_json()
        return given if redact(dumped) == dumped else redacted(given.model_dump(by_alias=True))
    return given


class Secret:
    """One secret value, named by the variable it was read from and printed by that name alone."""

    __slots__ = ("_value", "name")

    def __init__(self, value: str, name: str = "") -> None:
        self._value = value
        self.name = name
        if not name or secret_name(name):
            register(value, name)

    def reveal(self) -> str:
        """The value itself, for the one call that sends it to the service it belongs to."""
        return self._value

    def __bool__(self) -> bool:
        """True when the variable is set, which is what a caller checks before spending."""
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return f"<secret {self.name}>" if self.name else "<secret>"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        """Two secrets are equal when their values are. A secret never equals a plain string."""
        return isinstance(other, Secret) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __getstate__(self) -> None:
        """Refuse every copy protocol, so no `pickle` and no worker pool can carry the value out."""
        raise TypeError("a Secret is not serializable, and reveal() is the one way to read it")
