"""The exceptions DeckTalk raises on purpose. Library callers catch them, and the CLI prints them.

Each subclass names what went wrong, so a caller can tell a bad project file from a provider
that refused a request without reading a message. Anything DeckTalk did not mean to raise is
a bug and reaches the caller as it is.
"""

from __future__ import annotations


class DeckTalkError(Exception):
    """Base class for every error DeckTalk raises on purpose."""


class ConfigError(DeckTalkError):
    """decktalk.toml, cues.json or .env is missing, malformed, or inconsistent."""


class MissingInputError(DeckTalkError):
    """A stage needs an artifact that an earlier stage has not produced yet."""


class ProviderError(DeckTalkError):
    """An external API such as ElevenLabs refused or failed a request."""


class ToolError(DeckTalkError):
    """ffmpeg, ffprobe or Chromium is unavailable or failed."""
