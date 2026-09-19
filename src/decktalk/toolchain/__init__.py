"""What DeckTalk fetches or ships for one machine, and where it keeps it.

`cache.py` owns the per-user cache directory, `ffmpeg_fetch.py` downloads and verifies the pinned
ffmpeg build into it, and `assets.py` names the runtime and the KaTeX release that ship in the
wheel. Nothing here knows about a project.
"""
