"""A Ctrl-C delivered to the main thread the way a terminal delivers one, for the tests that stop a pool.

A real SIGINT aimed at the main thread is what wakes a blocked wait the way a key press does, so these
tests interrupt with the signal itself rather than by raising inside the code under test. Only POSIX
can aim a signal at one thread, so `aimed_signals` skips such a test everywhere else.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

aimed_signals = pytest.mark.skipif(
    not hasattr(signal, "pthread_kill"), reason="Only POSIX can send a signal to one thread."
)
"""Skips a test that presses Ctrl-C, on a platform that cannot aim a signal at the main thread."""


@contextmanager
def interrupts_raise() -> Iterator[None]:
    """SIGINT raises `KeyboardInterrupt` on the main thread for the length of the block, as it does in a terminal."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def press_ctrl_c() -> None:
    """Send SIGINT to the main thread, from whichever thread calls this."""
    main = threading.main_thread().ident
    assert main is not None, "the main thread has started, since it is the one running the test"
    signal.pthread_kill(main, signal.SIGINT)
