"""The per-user cache directory, which is where every tool DeckTalk fetches for a machine lives.

One directory per user holds the pinned ffmpeg build, beside the folder Playwright keeps Chromium
in, so a second project on the same machine downloads nothing. `DECKTALK_CACHE_DIR` moves it.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def cache_dir() -> Path:
    """The per-user cache directory, next to Playwright's ms-playwright folder. DECKTALK_CACHE_DIR overrides it."""
    override = os.environ.get("DECKTALK_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "decktalk"
