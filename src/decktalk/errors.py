"""Exceptions raised by DeckTalk. The CLI turns them into exit codes; library callers catch them."""

from __future__ import annotations


class DeckTalkError(Exception):
    """Base class for every error DeckTalk raises on purpose."""


class ConfigError(DeckTalkError):
    """decktalk.toml, cues.json or .env is missing, malformed, or inconsistent."""


class MissingInputError(DeckTalkError):
    """A stage needs an artifact that an earlier stage has not produced yet."""


class ProviderError(DeckTalkError):
    """An external API (ElevenLabs, Gemini, fal.ai) refused or failed a request."""


class ToolError(DeckTalkError):
    """ffmpeg, ffprobe or Chromium is unavailable or failed."""
