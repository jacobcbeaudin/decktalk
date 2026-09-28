"""What DeckTalk fetches or ships for one machine, and where it keeps it.

`cache.py` owns the per-user cache directory, `ffmpeg_fetch.py` downloads and verifies the pinned
ffmpeg build into it, `chromium_fetch.py` fetches the headless Chromium that Playwright manages,
and `assets.py` names the runtime and the KaTeX release that ship in the wheel. Nothing here knows
about a project.
"""

TAIL_LINES = 6
"""Truth: a tool says what it could not do in its last few lines, and everything above is its progress."""


def tail(said: bytes) -> str:
    """The last lines of what a tool wrote, as one line, which is where the reason for a failure is."""
    lines = said.decode(errors="replace").strip().splitlines()
    return " | ".join(line.strip() for line in lines[-TAIL_LINES:]) or "it said nothing"
