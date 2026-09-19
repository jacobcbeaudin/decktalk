"""Fetching the tools a machine needs to record and encode.

`decktalk install` runs once per machine. Chromium comes from Playwright and ffmpeg from the pinned,
verified download in `toolchain/`. Nothing here touches a project.
"""

from __future__ import annotations

import logging
import subprocess
import sys

from ..errors import ToolError
from ..media.ffmpeg import ffmpeg_paths
from ..toolchain.ffmpeg_fetch import FFMPEG_VERSION, installed_pinned

log = logging.getLogger(__name__)


def install() -> None:
    """Fetch the headless Chromium and the pinned ffmpeg build."""
    log.info("== Chromium (Playwright)")
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if sys.platform.startswith("linux"):
        cmd.append("--with-deps")
    if subprocess.call(cmd) != 0:
        raise ToolError("playwright install failed. See the output above.")
    log.info("== ffmpeg %s", FFMPEG_VERSION)
    found = installed_pinned()
    if found:
        log.info("   cached  %s", found[0])
    ff, fp = ffmpeg_paths()
    log.info("   ffmpeg  %s", ff)
    log.info("   ffprobe %s", fp)
