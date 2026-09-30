"""What DeckTalk fetches or ships for one machine, and where it keeps it.

`cache.py` owns the per-user cache directory, `ffmpeg_fetch.py` downloads and verifies the pinned
ffmpeg build into it, `chromium_fetch.py` fetches the headless Chromium that Playwright manages,
and `assets.py` names the runtime and the KaTeX release that ship in the wheel. Nothing here knows
about a project.

It also holds the two words every traced tool call is recorded in: the last lines a tool wrote, and
the command line a person would type to run it again.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Sequence

TAIL_LINES = 6
"""Truth: a tool says what it could not do in its last few lines, and everything above is its progress."""


def tail(said: bytes) -> str:
    """The last lines of what a tool wrote, as one line, which is where the reason for a failure is."""
    lines = said.decode(errors="replace").strip().splitlines()
    return " | ".join(line.strip() for line in lines[-TAIL_LINES:]) or "it said nothing"


def command_line(argv: Sequence[object]) -> str:
    """A command as a person would type it again, which is what a trace of a tool call records."""
    return shlex.join(str(part) for part in argv)


def traced(log: logging.Logger, what: str, argv: Sequence[object], *, code: int, seconds: float, said: bytes) -> None:
    """Record one tool call that ended: at debug when it succeeded, and at warning when it did not.

    The command, its exit code, how long it took and the last lines it wrote are the four things an
    operator needs to run it again by hand. No environment is ever recorded, because the environment
    is where a credential sits.
    """
    log.log(
        logging.DEBUG if code == 0 else logging.WARNING,
        "%s exited %d after %.2f seconds.",
        what,
        code,
        seconds,
        extra={
            "data": {"argv": command_line(argv), "exit": code, "seconds": round(seconds, 3), "output_tail": tail(said)}
        },
    )
