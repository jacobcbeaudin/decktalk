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
from enum import Enum, IntEnum
from typing import Any, ClassVar

from pydantic import Field, model_validator

from decktalk.findings import DOCS, Location, Model
from decktalk.secret import redact, redacted


class Exit(IntEnum):
    """Every code a run can exit with, with the sentence that says what it means and the phrase help prints.

    It is the one exit table: the error codes take theirs from it, a judged run takes the first two,
    and `decktalk schema` and the help epilog are both written from it. 130 is the shell's own code
    for a signalled process.
    """

    sentence: str
    phrase: str

    def __new__(cls, code: int, sentence: str, phrase: str) -> Exit:
        member = int.__new__(cls, code)
        member._value_ = code
        member.sentence = sentence
        member.phrase = phrase
        return member

    FOUND_NOTHING = (
        0,
        "The command ran and found nothing at or above the threshold --fail-on set.",
        "found nothing at the --fail-on threshold",
    )
    FOUND_SOMETHING = (
        1,
        "The command ran and found something at or above the threshold --fail-on set.",
        "found something at it",
    )
    REFUSED = 2, "The command line was refused, which is USAGE or APPROVAL.", "refused the command line"
    BROKEN = 3, "The command could not run, which is every other error code.", "could not run"
    INTERRUPTED = 130, "The caller stopped the run.", "interrupted"


class ErrorCode(Enum):
    """Why DeckTalk could not run, with the sentence a reader meets and the exit code it takes.

    The value is the code as JSON writes it and as the docs URL spells it. Each is a noun naming
    what is missing or wrong, never an imperative naming what the tool wishes the caller would do.
    """

    sentence: str
    exit_code: Exit

    def __new__(cls, code: str, exit_code: Exit, sentence: str) -> ErrorCode:
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

    INPUT = "INPUT", Exit.BROKEN, "A file the author writes is missing, unreadable or malformed."
    NOT_BUILT = "NOT_BUILT", Exit.BROKEN, "A file a stage needs was never built."
    PROVIDER = "PROVIDER", Exit.BROKEN, "The voice could not be reached, refused the request, or failed it."
    TOOL = "TOOL", Exit.BROKEN, "ffmpeg or Chromium is missing, or one of them failed."
    LOCKED = "LOCKED", Exit.BROKEN, "Another writer holds this project's build directory."
    APPROVAL = "APPROVAL", Exit.REFUSED, "A spend needed an approval that no flag and no terminal gave."
    CANCELLED = "CANCELLED", Exit.INTERRUPTED, "The caller stopped the run."
    USAGE = "USAGE", Exit.REFUSED, "The command line was refused, and a retry as written fails again."
    INTERNAL = "INTERNAL", Exit.BROKEN, "A bug in DeckTalk, with a traceback under -v."


class DeckTalkError(Exception):
    """The base of every refusal DeckTalk makes on purpose, which is never raised by itself.

    A raiser carries the smallest next action as `hint`, which is a whole command with its flags
    rather than a topic, and the place the reader should open as `location`.
    """

    code: ClassVar[ErrorCode]

    def __init__(self, message: str, *, hint: str | None = None, location: Location | None = None) -> None:
        # A message quotes what a tool or a service said, so every registered secret is taken out of it
        # before the exception exists, and `str()` of it, its hint and its error object hold none.
        super().__init__(redact(message))
        self.hint = redact(hint) if hint is not None else None
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
        reached: bool = True,
    ) -> None:
        super().__init__(message, hint=hint, location=location)
        self.retryable = retryable
        """True when the same request may succeed later, which is what decides whether a caller waits."""
        self.reached = reached
        """False when nothing answered at all, so the request was never received and nothing was billed."""


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
    """The run stopped before a section started, because its caller cancelled it or its pool halted."""

    code = ErrorCode.CANCELLED


class Cancel:
    """The token a caller holds to stop a run, which every mutating call takes and checks.

    It is thread safe, because the caller that stops a run holds it on a thread of its own, such as
    a service's request handler, and never on the thread doing the work. A stage checks it between
    sections, and a pool of workers starts no section once it is set, so a cancelled run leaves whole
    artifacts behind rather than half of one. A terminal's Ctrl-C does not set it: the interrupt
    reaches the run's own thread as `KeyboardInterrupt`, which `decktalk.stages.pool` answers the
    same way.
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
            raise Cancelled("The caller stopped this run.", hint="Run the command again to start a fresh run.")


class ErrorInfo(Model):
    """The `error` of a result: why the command could not run, and what would let it.

    It is filled only when the command could not run at all, so a reader that finds it null knows
    the command ran and that every judgement is in `findings`.
    """

    code: ErrorCode = Field(description="The code a caller dispatches on, such as NOT_BUILT.")
    message: str = Field(description="One sentence saying what is wrong, with the measured detail in it.")
    hint: str | None = Field(None, description="The whole command that would clear this, or null.")
    location: Location | None = Field(None, description="The file and line to open, or null.")
    docs: str = Field(description="The docs page for this code.")

    @model_validator(mode="before")
    @classmethod
    def _redacted(cls, given: Any) -> Any:  # noqa: ANN401  (whatever the error is being built from)
        """Take every registered secret out of the error before it exists, whichever path built it."""
        return redacted(given)

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

    @classmethod
    def of_failure(cls, failure: BaseException) -> ErrorInfo:
        """Whatever ended a run as a result carries it, which is how a run's last line says why it ended.

        A refusal keeps its own code. An interrupt is the caller stopping the run, as a cancel token
        is. Anything else is a bug in DeckTalk, and only its type and message are kept, because a
        traceback carries locals and paths that nothing has checked for a secret.
        """
        if isinstance(failure, DeckTalkError):
            return cls.of(failure)
        if isinstance(failure, KeyboardInterrupt):
            return cls(code=ErrorCode.CANCELLED, message="The run was interrupted.", docs=ErrorCode.CANCELLED.url)
        return cls(
            code=ErrorCode.INTERNAL,
            message=f"{type(failure).__name__}: {failure}",
            hint="Run the command again with -v for the traceback, and open an issue with it.",
            docs=ErrorCode.INTERNAL.url,
        )


STOPS = (Cancelled, KeyboardInterrupt)
"""What a caller raises to stop a run, which ends it as stopped rather than as failed."""


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
