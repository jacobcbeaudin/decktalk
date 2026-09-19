"""The exceptions DeckTalk raises on purpose, and the closed list of codes the CLI reports them by.

Each subclass names what went wrong, so a caller can tell a bad project file from a provider
that refused a request without reading a message. Anything DeckTalk did not mean to raise is
a bug and reaches the caller as it is.

An error carries the smallest next action as `hint`, the file it is about as `path`, and the line
in that file as `line`, each filled by the raiser that knows it and left null where nothing does.
The CLI prints all four, which is what keeps a message to one sentence.

Every class carries its `ErrorCode`, and the envelope's `error.code` is read from it and from
nowhere else. A subclass a stage defines to carry a result with its refusal inherits the code of
the class it is a kind of, so no caller meets a code the list does not hold.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import ClassVar


class ErrorCode(Enum):
    """What the envelope's `error.code` says, and the value is the code as JSON writes it.

    Four name an error class. `USAGE` is a command line the parser refused, and `INTERNAL` is
    anything DeckTalk did not mean to raise, which includes a bare `DeckTalkError`.
    """

    CONFIG = "CONFIG"
    MISSING_INPUT = "MISSING_INPUT"
    PROVIDER = "PROVIDER"
    TOOL = "TOOL"
    USAGE = "USAGE"
    INTERNAL = "INTERNAL"


class DeckTalkError(Exception):
    """Base class for every error DeckTalk raises on purpose."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        path: Path | None = None,
        line: int | None = None,
    ) -> None:
        super().__init__(message)
        self.hint = hint
        self.path = path
        self.line = line


class ConfigError(DeckTalkError):
    """decktalk.toml, cues.json or .env is missing, malformed, or inconsistent."""

    code = ErrorCode.CONFIG


class MissingInputError(DeckTalkError):
    """A stage needs an artifact that an earlier stage has not produced yet."""

    code = ErrorCode.MISSING_INPUT


class ProviderError(DeckTalkError):
    """An external API such as ElevenLabs refused or failed a request."""

    code = ErrorCode.PROVIDER


class ToolError(DeckTalkError):
    """ffmpeg, ffprobe or Chromium is unavailable or failed."""

    code = ErrorCode.TOOL
