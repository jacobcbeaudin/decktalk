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
"""

from __future__ import annotations


class Secret:
    """One secret value, named by the variable it was read from and printed by that name alone."""

    __slots__ = ("_value", "name")

    def __init__(self, value: str, name: str = "") -> None:
        self._value = value
        self.name = name

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
