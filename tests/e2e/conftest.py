"""What every suite in this directory needs before its first build, checked once per session.

The pipeline fixture and every packaged project record in Chromium and cut with ffmpeg. These suites
are only collected when a run names `e2e` or `scaffold`, so a machine without either tool fails here
with the command that fetches it rather than skipping a suite the run asked for.

Every build here also audits the still frames it skips, so a frame written again that a capture
would have drawn differently fails the build that wrote it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from decktalk.tomlmap import variable
from support.tools import machine_tools, require

NEEDED = ("chromium", "ffmpeg")
"""What a build of any project in this directory reaches for, which `decktalk doctor` reports."""

AUDIT_EVERY = 5
"""How often a build here captures a frame it would write again and compares the two.

Often enough that a still stretch of a fifth of a second is audited, and seldom enough that the
builds still take the skipping path they are judging.
"""


@pytest.fixture(scope="session", autouse=True)
def tools_present(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Fail the session before any build starts when this machine lacks a tool a build needs."""
    require(NEEDED, tmp_path_factory.mktemp("doctor"))


@pytest.fixture(scope="session", autouse=True)
def session_tools() -> Iterator[None]:
    """Bind this process's machine's cache and tools for the session, because the fixture runs ffmpeg outside a run."""
    with machine_tools():
        yield


@pytest.fixture(scope="session", autouse=True)
def audited() -> Iterator[None]:
    """Audit every still frame skip a build in this session makes, through the variable a subprocess inherits."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(variable("record.skip_audit_every"), str(AUDIT_EVERY))
        yield
