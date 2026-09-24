"""The platform fact: `install` and `doctor` are the two commands every machine runs, and they differ here.

`decktalk install` fetches about two hundred megabytes and `decktalk doctor` reports what it found,
and the `platform` group in `scripts/check.py` runs both for real on all three platforms after this
file. What this file holds is the part of those two commands that is a different answer on every
platform and that a faked download can never reach: where the per-user cache is, what an executable
is called, and that `doctor` reports a row for every tool with a path this platform could run.

Nothing here downloads anything, so it runs in milliseconds and the two real commands that follow it
in the group are the smoke. `doctor` fetches nothing by design, which is what lets it run first, and
it is also why a row here may be a tool this machine has not fetched yet. What is asserted is the
pair: a tool `doctor` found names a version, and a tool it did not find names neither a version nor
a path and was not fetched to make the report read better.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from decktalk.machine import Machine

pytestmark = pytest.mark.platform
"""Every test here is about this machine, so only the group that names this platform runs one."""


EXPECTED_TOOLS = ("chromium", "ffmpeg", "ffprobe", "katex")
"""Every tool a machine needs, which `doctor` reports a row for whether it has been fetched or not."""

WINDOWS = os.name == "nt"


def test_doctor_reports_a_row_for_every_tool_and_fetches_nothing() -> None:
    """A report that leaves a tool out is a machine a reader thinks is ready and is not."""
    report = Machine.from_environment().doctor()
    assert tuple(row.tool for row in report.tools) == EXPECTED_TOOLS
    for row in report.tools:
        assert row.fetched is False, f"doctor fetched {row.tool}, and doctor is the command that fetches nothing"
        if row.version is None:
            assert row.path is None, f"doctor named a path for {row.tool} and no version to go with it"
            continue
        assert row.path is None or Path(row.path).is_absolute(), row.tool


def test_doctor_names_this_platform_and_this_python() -> None:
    """The two facts a bug report needs first, which is why they are on the machine report at all."""
    report = Machine.from_environment().doctor()
    assert sys.platform in report.platform
    assert report.python.startswith(".".join(str(part) for part in sys.version_info[:2]))


def test_the_cache_is_one_directory_per_user_on_this_platform() -> None:
    """A second project on this machine downloads nothing, which only holds while the cache is shared."""
    report = Machine.from_environment().doctor()
    cache = Path(report.cache)
    assert cache.is_absolute()
    assert cache.name == "decktalk"
    assert cache.parent != Path.cwd(), "a cache under the working directory is a cache per project"


def test_every_tool_path_this_platform_reports_is_named_the_way_it_runs_one() -> None:
    """An executable named without its suffix on Windows is a path nothing can start."""
    report = Machine.from_environment().doctor()
    for row in report.tools:
        if row.path is None or row.tool not in ("ffmpeg", "ffprobe"):
            continue
        assert Path(row.path).name == (f"{row.tool}.exe" if WINDOWS else row.tool), row.path
