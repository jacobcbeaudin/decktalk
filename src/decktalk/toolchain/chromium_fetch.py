"""The headless Chromium Playwright manages: whether this machine has it, and fetching it.

Playwright downloads one pinned Chromium revision per version of the playwright package and keeps
it in its own cache, beside the ffmpeg build in `ffmpeg_fetch.py`. The fetch runs the first time a
command needs a browser, so a build works on a machine where nothing was installed by hand, and
`decktalk install` runs the same fetch up front.

The two callers differ in one flag. `--with-deps` asks Playwright to install Chromium's system
libraries, which it does by shelling out to apt-get through sudo, so it can ask for a root
password. Only `decktalk install` passes it, because that is a command a person typed and is
waiting on. A build never does: builds run unattended in scripts and in CI, where a password
prompt is a hang rather than a question. When Chromium then will not launch because those
libraries are genuinely missing, the error says to run `decktalk install`.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Playwright

from ..errors import ToolError
from . import tail
from .announce import announce

log = logging.getLogger(__name__)

TOOL = "chromium"
"""What a `fetch` line calls this download, which is the name `doctor` and `install` print too."""

# What both callers run. Playwright resolves the revision from its own version, so nothing is
# pinned here: the pin is the playwright dependency in pyproject.toml.
INSTALL_ARGS = ("-m", "playwright", "install", "chromium")
# The flag that reaches sudo, named once so it is clear which call passes it and which does not.
WITH_DEPS = "--with-deps"


def installed_chromium(pw: Playwright) -> str | None:
    """The Chromium executable Playwright has on disk for this machine, or None when it has none.

    `pw` is a started `sync_playwright`. The path it names is the revision this playwright package
    expects, so upgrading playwright makes this None again and the next command fetches the build
    the new version wants.
    """
    try:
        path = pw.chromium.executable_path
    except PlaywrightError:  # pragma: no cover - a driver that cannot answer is a machine without it
        return None
    return str(path) if Path(path).is_file() else None


FETCH_TIMEOUT_SECONDS = 1800
"""Calibration: half an hour, far longer than 200 MB takes on a usable connection, so only a stuck install hits it."""


def fetch_chromium(*, with_deps: bool = False) -> None:
    """Run `playwright install chromium` for this machine, raising a ToolError when it fails.

    `with_deps` adds Chromium's system libraries and may ask for a root password, so only
    `decktalk install` passes it. The password prompt goes to the terminal itself, so it is seen
    although the installer's own output is kept. That output is kept rather than printed, because
    nothing in the library prints, and its last lines are the reason a failed fetch gives.

    The download is announced before it starts and never counted as it arrives, because Playwright
    reports its progress to its own output and tells this process nothing. A fetch that runs past
    `FETCH_TIMEOUT_SECONDS` is stopped and refused, so a stalled mirror cannot hold a build forever.
    """
    announce(TOOL, 0, None)
    cmd = [sys.executable, *INSTALL_ARGS, *([WITH_DEPS] if with_deps else [])]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=FETCH_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            f"playwright install ran for longer than {FETCH_TIMEOUT_SECONDS} seconds, so it was stopped.",
            hint="Check the network, then run `decktalk install`.",
        ) from exc
    if done.returncode != 0:
        raise ToolError(
            f"playwright install failed: {tail(done.stderr or done.stdout or b'')}",
            hint="Check the network, then run `decktalk install`.",
        )
