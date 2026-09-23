"""Fetching the tools a machine needs to record and encode, before anything asks for them.

Nothing has to run this. A command that needs Chromium fetches it, and a command that needs ffmpeg
fetches that, so a machine with DeckTalk on it can build. `decktalk install` is the way to do both
up front instead: in a Docker layer, in a CI job that caches the download, on a machine that will
be offline later, and on Linux, where it is also the one command that installs Chromium's system
libraries and therefore the one command that may ask for a root password. Nothing here touches a
project.
"""

from __future__ import annotations

import logging
import sys

from ..media.ffmpeg import ffmpeg_paths
from ..toolchain.chromium_fetch import fetch_chromium
from ..toolchain.ffmpeg_fetch import FFMPEG_VERSION, installed_pinned

log = logging.getLogger(__name__)


def install() -> None:
    """Fetch the headless Chromium and the pinned ffmpeg build, with Chromium's system libraries on Linux."""
    log.info("== Chromium (Playwright)")
    fetch_chromium(with_deps=sys.platform.startswith("linux"))
    log.info("== ffmpeg %s", FFMPEG_VERSION)
    found = installed_pinned()
    if found:
        log.info("   cached  %s", found[0])
    ff, fp = ffmpeg_paths()
    log.info("   ffmpeg  %s", ff)
    log.info("   ffprobe %s", fp)
