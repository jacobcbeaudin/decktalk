"""The per-user cache directory, which is where every tool DeckTalk fetches for a machine lives.

One directory per user holds the pinned ffmpeg build, beside the folder Playwright keeps Chromium
in, so a second project on the same machine downloads nothing. `[tools] cache_dir` moves it, and it
arrives through `caching_in` because this layer sits below the settings it would otherwise read.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

ELSEWHERE: ContextVar[str] = ContextVar("decktalk_cache_dir", default="")
"""Where this run keeps what it fetches, which is empty for the standard per-user directory."""


@contextmanager
def caching_in(directory: str) -> Iterator[None]:
    """Keep what is fetched under `directory` while this is open, or under the standard one when it is empty."""
    token = ELSEWHERE.set(directory)
    try:
        yield
    finally:
        ELSEWHERE.reset(token)


def cache_dir() -> Path:
    """The directory this run keeps fetched tools in, next to Playwright's own ms-playwright folder."""
    if elsewhere := ELSEWHERE.get():
        return Path(elsewhere)
    if sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "decktalk"
