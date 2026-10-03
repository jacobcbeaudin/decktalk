"""The bridge from Python's standard logging to the run's event stream, which is the one output there is.

A module below the stages holds no run, in the same way a fetcher holds none, so it writes a
standard logging record with `logging.getLogger(__name__)`. One handler on the `decktalk` logger
turns every such record into a `Log` line of the run bound to the current context, and then hands
the record on to whatever logging a host configured, so a host reads the same sentence either way.

The run's file hears every record, debug included, so the package logger admits every level. A
host's handlers hear only what the host's own levels admit, the way they would if the package
logger held no level at all: the handler hands a record on itself, by the first level a host set on
the way from the record's logger to the root, and the package logger never propagates on its own.
A host that sets a level on the `decktalk` logger sets it for the run's file as well.

The run travels in a context variable, as the download listener in `toolchain/announce.py` does.
A worker thread that a stage starts runs under a copy of its parent's context, so a record written
by a recorder or a narration worker reaches the run that started it, and two runs in one process
each keep their own receiver.

Nothing here prints. With a handler in the hierarchy, Python's last-resort handler is never used for
a `decktalk` record, so a warning written while no run is bound goes to the host's own logging or
nowhere, and never to bare stderr. Every registered secret is taken out of the sentence, the data,
the traceback and the stack before any handler on the package logger or above it reads the record.
A receiver that raises is counted on the handler rather than printed, and a record written while
the stream is already delivering a line is dropped, so a renderer that logs cannot recurse into
the stream it is rendering.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

from decktalk.events import DELIVERING, Level
from decktalk.pipeline import Stage
from decktalk.secret import redact, redacted

LOGGER = logging.getLogger("decktalk")
"""The parent of every module logger in the package, which is where the one handler sits."""

EVERY = logging.NOTSET + 1
"""Derived: the lowest level above NOTSET, which admits every record and is never a level a host chose."""

PREFIX = f"{LOGGER.name}."
"""What every module logger's name starts with, which a line's `source` leaves off."""

Receiver = Callable[[logging.LogRecord], None]
"""What turns one record into one line of a run, which `Run.logged` is."""


@dataclass(frozen=True)
class Where:
    """The run, the stage and the section the current context is working in, as far as it knows them."""

    run: str | None = None
    stage: Stage | None = None
    section: int | None = None


WHERE: ContextVar[Where] = ContextVar("decktalk_where", default=Where())  # noqa: B039  (a frozen value is safe to share)
"""Where this context is working, which a run sets for its length and a stage or a section for its own."""

SINK: ContextVar[Receiver | None] = ContextVar("decktalk_log_sink", default=None)
"""Who hears a record written in this context, which is the bound run's receiver or nobody."""

HANDLING: ContextVar[bool] = ContextVar("decktalk_log_handling", default=False)
"""Whether this context is already turning a record into a line, so a record written on the way is dropped."""


@contextmanager
def logging_into(receiver: Receiver, *, run: str) -> Iterator[None]:
    """Hand every record written under this context to `receiver`, and to nobody once it closes."""
    sink, place = SINK.set(receiver), WHERE.set(Where(run=run))
    try:
        yield
    finally:
        WHERE.reset(place)
        SINK.reset(sink)


@contextmanager
def within(*, stage: Stage | None, section: int | None = None) -> Iterator[None]:
    """Stamp every line written under this context with one stage, and one section when there is one."""
    token = WHERE.set(replace(WHERE.get(), stage=stage, section=section))
    try:
        yield
    finally:
        WHERE.reset(token)


def level_of(number: int) -> Level:
    """The stream's level for a standard logging level, where anything above an error is an error."""
    if number >= logging.ERROR:
        return Level.ERROR
    if number >= logging.WARNING:
        return Level.WARNING
    if number >= logging.INFO:
        return Level.INFO
    return Level.DEBUG


def source_of(name: str) -> str:
    """The module a record came from, without the package's own name in front of it."""
    return name.removeprefix(PREFIX)


KEY_DIGITS = 12
"""How much of a cache key a decision line carries, which tells two keys apart without filling the line."""


def cache_decision(
    log: logging.Logger, cache: str, *, hit: bool, why: str, key: str | None = None, **more: object
) -> None:
    """Record one decision to keep or make something cached, and the one short token that says why.

    `why` is a fixed token such as `unchanged`, `no-record`, `key-changed` or `forced`, so a reader
    answers why a stage rebuilt with a filter on `data.why` rather than by parsing a sentence.
    """
    log.debug(
        "%s %s (%s).",
        cache,
        "reused" if hit else "made",
        why,
        extra={"data": {"cache": cache, "hit": hit, "why": why, "key": key[:KEY_DIGITS] if key else None, **more}},
    )


FORMATTER = logging.Formatter()
"""What renders a record's exception as the text a host's formatter appends to the sentence."""


def _redact(record: logging.LogRecord) -> None:
    """Take every registered secret out of the record itself, before it reaches a host's own handlers.

    This handler is the first on the package's logger, which every record passes before the root
    logger's handlers, so a host's formatter prints the sentence the run's line holds. The exception
    is rendered here into `exc_text`, which a standard formatter appends instead of rendering it
    again, and the stack is redacted where it stands. The exception itself stays on the record for
    the run's line and for a host that reads its type. A record whose message cannot be rendered is
    left for the handler that renders it to count.
    """
    if record.exc_info and not record.exc_text:
        record.exc_text = FORMATTER.formatException(record.exc_info)
    if record.exc_text:
        record.exc_text = redact(record.exc_text)
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    given = getattr(record, "data", None)
    if isinstance(given, Mapping):
        record.data = redacted(dict(given))
    try:
        said = record.getMessage()
    except Exception:  # noqa: BLE001  (a record that cannot be rendered is counted where it is emitted)
        return
    cleaned = redact(said)
    if cleaned != said:
        record.msg, record.args = cleaned, None


def admitted(record: logging.LogRecord) -> bool:
    """Whether the host's own levels admit a record, read as if the package logger held no level of its own.

    The first level set on the way from the record's logger to the root decides, as it does for
    any logger, and the package logger's own `EVERY` is passed over because DeckTalk set it there.
    """
    logger: logging.Logger | None = logging.getLogger(record.name)
    while logger is not None:
        if logger.level != logging.NOTSET and not (logger is LOGGER and logger.level == EVERY):
            return record.levelno >= logger.level
        logger = logger.parent
    return True


def onward(record: logging.LogRecord) -> None:
    """Hand a record to the handlers above the package logger that the host's levels admit it to.

    It walks the hierarchy as the standard library's own propagation does, honouring each logger's
    `propagate` and each handler's level, and with no handler found it prints nothing. A handler
    also attached to the package logger, as pytest attaches its capture to a logger that does not
    propagate, already heard the record there and is not handed it twice. A host that turns the
    package logger's own propagation back on has the standard library hand the record on instead.
    """
    if LOGGER.propagate or not admitted(record):
        return
    heard = LOGGER.handlers
    logger = LOGGER.parent
    while logger is not None:
        for handler in logger.handlers:
            if handler not in heard and record.levelno >= handler.level:
                handler.handle(record)
        logger = logger.parent if logger.propagate else None


class RunHandler(logging.Handler):
    """The one handler on the `decktalk` logger, which turns a record into a line of the bound run.

    It stamps the run, the stage and the section on the record before the record goes on to a
    host's handlers, so a host's own formatter can print `%(decktalk_run)s` beside the sentence.
    It renders the exception into `exc_text` with every secret taken out, which is the text a
    standard formatter after it prints, because an exception's own arguments can hold a secret.
    """

    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.failures = 0
        """How many records this handler could not turn into a line, which is counted and never printed."""

    def filter(self, record: logging.LogRecord) -> bool | logging.LogRecord:
        """Stamp where the record was written, then apply any filter a host added to this handler."""
        place = WHERE.get()
        record.decktalk_run = place.run
        record.decktalk_stage = place.stage.value if place.stage is not None else None
        record.decktalk_section = place.section
        _redact(record)
        return super().filter(record)

    def handle(self, record: logging.LogRecord) -> bool:
        """Emit without the handler's lock, because the stream is already thread safe.

        Holding a lock here across a delivery would put every recorder behind whichever one logged first.
        """
        passed = bool(self.filter(record))
        if passed:
            self.emit(record)
        onward(record)
        return passed

    def emit(self, record: logging.LogRecord) -> None:
        """Hand the record to the bound run, or to nobody when no run is bound or a line is being delivered."""
        receiver = SINK.get()
        if receiver is None or DELIVERING.get() or HANDLING.get():
            return
        token = HANDLING.set(True)
        try:
            receiver(record)
        except Exception:  # noqa: BLE001  (a record that cannot become a line is counted, never raised or printed)
            self.handleError(record)
        finally:
            HANDLING.reset(token)

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: ARG002  (the standard library's name)
        """Count a failure where the standard handler would print a traceback to stderr."""
        self.failures += 1


def install() -> RunHandler:
    """Put the one handler first on the `decktalk` logger, once, and give it back.

    The logger's level is set to `EVERY` only when nobody chose one, so the run's file hears every
    record while a host that set a level before importing DeckTalk keeps the level it set. The
    logger stops propagating, because the handler hands each record on by the host's own levels.
    """
    held = next((handler for handler in LOGGER.handlers if isinstance(handler, RunHandler)), None)
    if held is not None:
        return held
    handler = RunHandler()
    LOGGER.handlers.insert(0, handler)
    LOGGER.propagate = False
    if LOGGER.level == logging.NOTSET:
        LOGGER.setLevel(EVERY)
    return handler


HANDLER = install()
"""The handler this process installed, which a test reads to see what it could not deliver."""

__all__ = ["HANDLER", "LOGGER", "RunHandler", "Where", "cache_decision", "logging_into", "WHERE", "within"]
