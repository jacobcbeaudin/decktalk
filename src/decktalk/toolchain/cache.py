"""The per-user cache and data directories, where every tool DeckTalk fetches and its take store live.

One directory per user holds the pinned ffmpeg build and the folder Playwright keeps Chromium in,
so a second project on the same machine downloads nothing and a job caches one directory. The
machine works out which directory that is from its own environment, or takes the one
`[tools] cache_dir` names, and binds it for every run through `caching_in`, because this layer sits
below the machine and the settings it would otherwise have to read.

Nothing here reads the process environment. A fetch outside any binding is refused rather than sent
to a directory worked out from whatever process it happens to run in, because a host that built its
machine by hand has already said where its tools live.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from decktalk.errors import ToolError

ELSEWHERE: ContextVar[str] = ContextVar("decktalk_cache_dir", default="")
"""Where this run keeps what it fetches, which is empty until a machine binds its directory."""

CACHE_NAME = "decktalk"
"""The folder DeckTalk keeps inside a per-user root, which holds every tool it fetches or its data."""

CACHE_FOLDER = "cache"
"""The tool cache's folder inside DeckTalk's data folder, where one root holds both."""


@contextmanager
def caching_in(directory: str) -> Iterator[None]:
    """Keep what is fetched under `directory` while this is open.

    An empty directory leaves the binding already in force, which is how `[tools] cache_dir` left
    unset falls through to the directory the machine worked out for itself.
    """
    if not directory:
        yield
        return
    token = ELSEWHERE.set(directory)
    try:
        yield
    finally:
        ELSEWHERE.reset(token)


def cache_dir() -> Path:
    """The directory this run keeps fetched tools in, or a `TOOL` refusal when no machine bound one."""
    if bound := ELSEWHERE.get():
        return Path(bound)
    raise ToolError(
        "no machine named a directory to keep fetched tools in.",
        hint="Open a run on a machine, which binds its cache directory, before fetching a tool.",
    )


def standard_cache_dir(environ: Mapping[str, str], home: Path, platform: str = sys.platform) -> Path:
    """The per-user cache directory this platform's conventions name, worked out from values passed in.

    The environment and the home directory are arguments, so the machine that owns them decides
    where its tools live and a second machine in the same process can decide differently. Windows
    has one local per-user root for both, so the cache is a `cache` folder inside DeckTalk's data
    folder there, which keeps the take store beside it and never inside it.
    """
    if platform == "darwin":
        return home / "Library" / "Caches" / CACHE_NAME
    if platform == "win32":
        return standard_data_dir(environ, home, platform) / CACHE_FOLDER
    return Path(environ.get("XDG_CACHE_HOME") or home / ".cache") / CACHE_NAME


def standard_data_dir(environ: Mapping[str, str], home: Path, platform: str = sys.platform) -> Path:
    """The per-user data directory this platform's conventions name, which is kept and backed up.

    The take store lives here rather than in the cache, because a cleaner empties a cache and macOS
    leaves `~/Library/Caches` out of a backup, while the store holds voiced takes. Windows
    uses the local root rather than the roaming one, so a roaming profile does not carry audio at
    every sign-in.
    """
    if platform == "darwin":
        root = home / "Library" / "Application Support"
    elif platform == "win32":
        root = Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    else:
        root = Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return root / CACHE_NAME
