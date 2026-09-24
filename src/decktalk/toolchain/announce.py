"""How a download says it is happening, so a run that stops for the network says so as it happens.

A fetch runs inside the first render that needs a tool, under a call stack that carries no project
and no event stream, and this layer sits below `events.py`, so a fetcher can neither be handed a
stream nor import one. The listener therefore travels in a context variable, which the machine sets
for the length of a run. A caller that sets nothing downloads exactly as before and nobody hears
about it, and two runs in one process each keep their own listener, which is what a service needs.

A thread starts in a fresh context rather than in its parent's, so a caller that fetches on a thread
of its own runs it through `contextvars.copy_context().run`, as a thread pool with a context does.

The three arguments are the three fields of the `fetch` event, so the machine's listener is one line
that mints the event and nothing in between translates.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol


class Announce(Protocol):
    """One line about a download: what is being fetched, how much has arrived, and how much there is."""

    def __call__(self, tool: str, done_bytes: int, total_bytes: int | None) -> None: ...


def silent(tool: str, done_bytes: int, total_bytes: int | None) -> None:  # noqa: ARG001  (the listener that hears nothing)
    """The listener in force when nobody is watching, which is what keeps a fetcher free of a stream."""


LISTENER: ContextVar[Announce] = ContextVar("decktalk_fetch_listener", default=silent)
"""Who hears about a download in this context, which `announcing` sets and every fetcher reads."""


def announce(tool: str, done_bytes: int, total_bytes: int | None = None) -> None:
    """Tell whoever is listening how a download is going, which is the one thing a fetcher reports."""
    LISTENER.get()(tool, done_bytes, total_bytes)


@contextmanager
def announcing(listener: Announce) -> Iterator[None]:
    """Report every fetch under this context to `listener`, and to nobody once it closes."""
    token = LISTENER.set(listener)
    try:
        yield
    finally:
        LISTENER.reset(token)
