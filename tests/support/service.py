"""A local HTTP service on threads of its own, which a test holds in place of a paid provider.

A threaded service is what lets a reply stall on one thread while the retry reaches the next, and
a stalled reply waits on `released`, which the fixture sets however the test ends.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

STALL_SECONDS = 10
"""The longest a stalled reply holds its thread when nothing releases it, well past any read timeout here."""

SHUTDOWN_POLL_SECONDS = 0.01
"""How often the service looks for its stop, which `serve_forever` leaves at half a second a test."""


class Service(HTTPServer):
    """A threaded loopback service whose stalled replies are let go by one event."""

    def __init__(self) -> None:
        super().__init__(host="127.0.0.1", threaded=True)
        self.released = threading.Event()

    def thread_target(self) -> None:
        """Serve until stopped, looking for the stop often enough that a teardown never waits on it."""
        assert self.server is not None
        self.server.serve_forever(poll_interval=SHUTDOWN_POLL_SECONDS)

    def stalls(self, _request: Request) -> Response:
        """A reply that sends its headers and one byte of a hundred, then waits until the test is over.

        On the caller's side that is a read that times out after the request was sent.
        """

        def body() -> Iterator[bytes]:
            yield b"{"
            self.released.wait(timeout=STALL_SECONDS)

        return Response(body(), 200, {"Content-Length": "100"})
