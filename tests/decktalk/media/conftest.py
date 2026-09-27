"""What a media test runs its real ffmpeg under, which is this process's own machine.

A call to ffmpeg outside a run has no machine to say where the pinned build is kept, so a test that
runs the real tool binds the toolchain of the machine this process would build, as a run does.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from decktalk.machine import Machine


@pytest.fixture(autouse=True)
def machine_tools(request: pytest.FixtureRequest) -> Iterator[None]:
    """Bind this process's machine's tools for every test that runs the real ffmpeg, and for no other."""
    if request.node.get_closest_marker("media") is None:
        yield
        return
    with Machine.from_environment().toolchain.bound():
        yield
