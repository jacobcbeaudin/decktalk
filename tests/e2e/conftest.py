"""What every suite in this directory needs before its first build, checked once per session.

The pipeline fixture and every packaged project record in Chromium and cut with ffmpeg. These suites
are only collected when a run names `e2e` or `scaffold`, so a machine without either tool fails here
with the command that fetches it rather than skipping a suite the run asked for.
"""

from __future__ import annotations

import pytest

from support.tools import require

NEEDED = ("chromium", "ffmpeg")
"""What a build of any project in this directory reaches for, which `decktalk doctor` reports."""


@pytest.fixture(scope="session", autouse=True)
def tools_present(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Fail the session before any build starts when this machine lacks a tool a build needs."""
    require(NEEDED, tmp_path_factory.mktemp("doctor"))
