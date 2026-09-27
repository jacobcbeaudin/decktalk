"""What a platform test runs the fetched toolchain under, which is this process's own machine.

The cache a tool is fetched into is named by a machine, and a test that reads the real toolchain
outside a run binds the one this process would build, as a run does.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from decktalk.machine import Machine


@pytest.fixture(autouse=True)
def machine_tools() -> Iterator[None]:
    """Bind this process's machine's cache and tools for the length of each platform test."""
    with Machine.from_environment().toolchain.bound():
        yield
