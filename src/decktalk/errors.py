"""The refusals DeckTalk makes on purpose: nine codes, seven classes and the exit code each one takes.

An error means DeckTalk could not run, so nothing was judged and something is broken. A finding is
the other thing entirely, which is a judgement about a film that did get made. Keeping the two apart
is why `except DeckTalkError` is worth writing: it catches an environment DeckTalk cannot work in
and never catches a bug in DeckTalk itself.

The count of classes is the count of distinct recoveries and not the count of codes. `USAGE` and
`INTERNAL` are codes with no class, because no library call produces either: a command line the
parser refused is the CLI's own refusal, and anything DeckTalk did not mean to raise reaches the
caller as it is and is reported as a bug. `DeckTalkError` is therefore the base class alone and is
never raised bare.

Each code carries the exit code its refusal takes, so the mapping is total by construction rather
than a table beside the list that a new code can miss.
"""

from __future__ import annotations

import threading
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, Field

from decktalk.findings import DOCS, MODEL, Location

REFUSED = 2
"""A command line DeckTalk refused, which a retry as written would refuse again."""
BROKEN = 3
"""Something DeckTalk needs is missing or wrong, so the run could not start or could not finish."""
INTERRUPTED = 130
"""The caller stopped the run, which is the shell's own code for a signalled process."""


class ErrorCode(Enum):
    """Why DeckTalk could not run, with the sentence a reader meets and the exit code it takes.

    The value is the code as JSON writes it and as the docs URL spells it. Each is a noun naming
    what is missing or wrong, never an imperative naming what the tool wishes the caller would do.
    """

    sentence: str
    exit_code: int

    def __new__(cls, code: str, exit_code: int, sentence: str) -> ErrorCode:
        member = object.__new__(cls)
        member._value_ = code
        member.exit_code = exit_code
        member.sentence = sentence
        return member

    def __repr__(self) -> str:
        return f"{type(self).__name__}.{self.name}"

    @property
    def url(self) -> str:
        """The docs page for this code, which every printed error block carries."""
        return f"{DOCS}/errors/{self.name}"

    INPUT = "INPUT", BROKEN, "A file the author writes is missing, unreadable or malformed."
    NOT_BUILT = "NOT_BUILT", BROKEN, "A file a stage needs was never built."
    PROVIDER = "PROVIDER", BROKEN, "The voice could not be reached, refused the request, or failed it."
    TOOL = "TOOL", BROKEN, "ffmpeg or Chromium is missing, or one of them failed."
    LOCKED = "LOCKED", BROKEN, "Another writer holds this project's build directory."
    APPROVAL = "APPROVAL", REFUSED, "A spend needed an approval that no flag and no terminal gave."
    CANCELLED = "CANCELLED", INTERRUPTED, "The caller stopped the run."
    USAGE = "USAGE", REFUSED, "The command line was refused, and a retry as written fails again."
    INTERNAL = "INTERNAL", BROKEN, "A bug in DeckTalk, with a traceback under -v."


class DeckTalkError(Exception):
    """The base of every refusal DeckTalk makes on purpose, which is never raised by itself.

    A raiser carries the smallest next action as `hint`, which is a whole command with its flags
    rather than a topic, and the place the reader should open as `location`.
    """

    code: ClassVar[ErrorCode]

    def __init__(self, message: str, *, hint: str | None = None, location: Location | None = None) -> None:
        super().__init__(message)
        self.hint = hint
        self.location = location


class InputError(DeckTalkError):
    """One of the four files the author writes is missing, unreadable or inconsistent."""

    code = ErrorCode.INPUT


class NotBuiltError(DeckTalkError):
    """A stage needs an artifact that no earlier run produced, and the hint names the command that makes it."""

    code = ErrorCode.NOT_BUILT


class ProviderError(DeckTalkError):
    """The voice could not be reached, refused the request, or failed it."""

    code = ErrorCode.PROVIDER

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        location: Location | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message, hint=hint, location=location)
        self.retryable = retryable
        """True when the same request may succeed later, which is what decides whether a caller waits."""


class ToolError(DeckTalkError):
    """ffmpeg, ffprobe or Chromium is unavailable, or one of them failed a call."""

    code = ErrorCode.TOOL


class ProjectLocked(DeckTalkError):
    """Another writer holds this project's build directory, so this run would overwrite its work."""

    code = ErrorCode.LOCKED


class ApprovalRequired(DeckTalkError):
    """A run that spends money was not approved, by a flag or by a person at a terminal.

    This is a library class and not a command-line concept, because a future service refuses the
    same spend for the same reason and must carry the same price with its refusal.
    """

    code = ErrorCode.APPROVAL


class Cancelled(DeckTalkError):
    """The caller stopped the run through its cancel token, between two sections."""

    code = ErrorCode.CANCELLED


class Cancel:
    """The token a caller holds to stop a run, which every mutating call takes and checks.

    It is thread safe, because the caller that stops a run is a terminal's interrupt handler or a
    renderer's own thread and never the thread doing the work. A stage checks it between sections,
    so a cancelled run leaves whole artifacts behind rather than half of one.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Ask the run to stop at its next section boundary."""
        self._event.set()

    def is_set(self) -> bool:
        """True once the caller has asked the run to stop."""
        return self._event.is_set()

    def check(self) -> None:
        """Raise `Cancelled` when the caller has asked the run to stop, which is what a stage calls."""
        if self._event.is_set():
            raise Cancelled("the caller stopped this run", hint="Run the command again to start a fresh run.")


class ErrorInfo(BaseModel):
    """The `error` of a result: why the command could not run, and what would let it.

    It is filled only when the command could not run at all, so a reader that finds it null knows
    the command ran and that every judgement is in `findings`.
    """

    model_config = MODEL

    code: ErrorCode = Field(description="The code a caller dispatches on, such as NOT_BUILT.")
    message: str = Field(description="One sentence saying what is wrong, with the measured detail in it.")
    hint: str | None = Field(None, description="The whole command that would clear this, or null.")
    location: Location | None = Field(None, description="The file and line to open, or null.")
    docs: str = Field(description="The docs page for this code.")

    @classmethod
    def of(cls, error: DeckTalkError) -> ErrorInfo:
        """The error as a result carries it, which is the one place an exception becomes data."""
        return cls(
            code=error.code,
            message=str(error),
            hint=error.hint,
            location=error.location,
            docs=error.code.url,
        )


__all__ = [
    "ApprovalRequired",
    "Cancel",
    "Cancelled",
    "DeckTalkError",
    "ErrorCode",
    "ErrorInfo",
    "InputError",
    "NotBuiltError",
    "ProjectLocked",
    "ProviderError",
    "ToolError",
]
