"""The environment a browser or an encoder DeckTalk starts is given, which is never the one the process holds.

Playwright starts Chromium with a copy of the process environment unless it is handed another, and
so does a plain subprocess, so a speech key, a cloud credential or any other secret the host holds
would sit in the environment of the process that runs a page's script, and of the ffmpeg that opens
a file someone else supplied. DeckTalk's own secret discipline never sees that path. So every
Chromium launch and every ffmpeg and ffprobe call is handed an environment built here, from the
names in `CHILD_KEYS` alone.

The values come from the machine rather than from the process, because only the machine reads the
environment. A run binds the machine's own mapping through `children_see`, and a child started
outside any run is handed the temporary directory and nothing else, which is enough for Chromium to
start and for ffmpeg to work.

A run that may buy has the key in reach, so it also binds that fact through `allowing_spend`, and the one
place a browser starts reads it with `may_spend` before it opens a page it does not trust.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

CHILD_KEYS = (
    "HOME",
    "PATH",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "LC_NUMERIC",
    "LC_TIME",
    "TZ",
    "TMPDIR",
    "TMP",
    "TEMP",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "FONTCONFIG_FILE",
    "FONTCONFIG_PATH",
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
)
"""The names a child process may see, each of which says where a file or a display is or how to print a date.

None of them is where a credential is kept. The locale and the time zone are here so a trusted page
formats a number or a date the way the author's own browser does, the proxy names so it reaches the
network the way the machine does, and the Windows names because a Windows process started without
them cannot load its own system libraries. An untrusted page's Chromium is handed a proxy of its own
on the command line, which wins over any proxy named here, and ffmpeg opens files alone, so a proxy
named here reaches nothing through it.
"""

TEMP_KEYS = ("TMPDIR", "TMP", "TEMP")
"""The names a platform reads its temporary directory from, which a launch always carries."""

MACHINE: ContextVar[Mapping[str, str] | None] = ContextVar("decktalk_machine_environ", default=None)
"""The environment of the machine this run belongs to, which `children_see` binds for the run."""


@contextmanager
def children_see(environ: Mapping[str, str]) -> Iterator[None]:
    """Build every child environment from `environ` while this is open, which the machine opens for a run."""
    token = MACHINE.set(environ)
    try:
        yield
    finally:
        MACHINE.reset(token)


SPEND: ContextVar[bool] = ContextVar("decktalk_spend", default=False)
"""Whether the run this context belongs to may buy, which `allowing_spend` binds for the run."""


@contextmanager
def allowing_spend(spend: bool) -> Iterator[None]:
    """Say whether the run may buy while this is open, which the machine opens for a run."""
    token = SPEND.set(spend)
    try:
        yield
    finally:
        SPEND.reset(token)


def may_spend() -> bool:
    """Whether the run this context belongs to may buy, which is false outside every run."""
    return SPEND.get()


def child_environment() -> dict[str, str]:
    """The environment a launched browser or encoder is given: the machine's values for `CHILD_KEYS`, and no others.

    Names are matched without regard to case, because Windows spells `SystemRoot` as it likes and
    reads it either way. The temporary directory is always present, because Chromium writes its
    profile there and a machine that named none still has one.
    """
    wanted = {key.upper() for key in CHILD_KEYS}
    bound = MACHINE.get() or {}
    kept = {name: value for name, value in bound.items() if name.upper() in wanted}
    if not any(name.upper() in TEMP_KEYS for name in kept):
        temp = tempfile.gettempdir()
        kept.update(dict.fromkeys(TEMP_KEYS, temp))
    return kept


__all__ = ["CHILD_KEYS", "child_environment", "children_see", "allowing_spend", "may_spend"]
