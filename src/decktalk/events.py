"""One stream of progress: thirteen events, the four fields the library mints onto each, and the
subscribers that render them.

Every call opens a run and writes to this stream. The Rich live region, the JSON lines `--events`
prints on stderr, the per-run file under `build/events/` and any later dashboard are all subscribers
to it, so a renderer never computes a fraction and there is only one channel to keep in step.

`event` is the discriminator and there are thirteen names. Skip, keep and fail are not names:
`stage.done` and `section.done` carry an `outcome`, because four names for one event would force
four branches where one field read will do.

The library mints `event`, `time`, `seq` and `run`, so an emitter states only what it measured.
`seq` counts per run rather than per machine, because a machine-wide counter would leave gaps in
every file and a reader could not tell a gap from a lost line.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args

from pydantic import Field, field_validator, model_validator

from decktalk.errors import ErrorInfo
from decktalk.findings import Finding, Model, ProjectPath
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Cost, Elapsed, RunId, SectionNumber, SoundKind
from decktalk.secret import redacted

MOMENT = "Which event this line is, which is what a reader dispatches on."
"""The one sentence the discriminator publishes, so all thirteen names describe themselves alike."""


class Unit(Enum):
    """What one step of a progress line counts, so a renderer can name the thing rather than a number."""

    TAKE = "take"
    SECTION = "section"
    SCORE_ITEM = "score_item"
    PASS = "pass"
    PROBE = "probe"


class Level(Enum):
    """How much a log line matters, which is what `-v` and `-q` choose between."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Event(Model):
    """What every line of the stream carries, whichever event it is."""

    event: str = Field(description=MOMENT)
    time: datetime = Field(description="When this happened, as an instant.", json_schema_extra={"volatile": True})
    seq: int = Field(ge=0, description="This line's place in its run, counting from zero.")
    run: RunId

    @model_validator(mode="before")
    @classmethod
    def _redacted(cls, given: Any) -> Any:  # noqa: ANN401  (whatever the line is being built from)
        """Take every registered secret out of the line before it exists, whichever path wrote it."""
        return redacted(given)


class RunStart(Event):
    """A run opened, and this is where its lines are being written."""

    event: Literal["run.start"] = Field("run.start", description=MOMENT)
    events_file: ProjectPath | None = Field(
        None, description="The file this run's lines are appended to, or null when no project holds one."
    )


class RunDone(Event):
    """A run closed, and this is how it ended."""

    event: Literal["run.done"] = Field("run.done", description=MOMENT)
    outcome: Outcome = Field(description="Whether the run finished, was stopped, or failed.")
    elapsed_seconds: Elapsed
    error: ErrorInfo | None = Field(
        None,
        description="Why the run stopped or failed, in the shape a result's error takes, or null when it finished.",
    )
    dropped: int = Field(
        0, ge=0, description="How many lines the events file left out once it reached events.max_bytes."
    )


class StageStart(Event):
    """A stage began, and this is its place in the run's plan."""

    event: Literal["stage.start"] = Field("stage.start", description=MOMENT)
    stage: Stage = Field(description="The stage this line is about.")
    index: int = Field(ge=1, description="This stage's place in the run's plan, counting from one.")
    count: int = Field(ge=1, description="How many stages the run planned.")


class StageDone(Event):
    """A stage ended, and this is how."""

    event: Literal["stage.done"] = Field("stage.done", description=MOMENT)
    stage: Stage = Field(description="The stage this line is about.")
    outcome: Outcome = Field(
        description="Whether the stage ran, was skipped, kept what an earlier run made, was stopped, or failed."
    )
    elapsed_seconds: Elapsed


class SectionStart(Event):
    """One section of a stage began."""

    event: Literal["section.start"] = Field("section.start", description=MOMENT)
    stage: Stage = Field(description="The stage this line is about.")
    section: SectionNumber


class SectionDone(Event):
    """One section of a stage ended, and this is how."""

    event: Literal["section.done"] = Field("section.done", description=MOMENT)
    stage: Stage = Field(description="The stage this line is about.")
    section: SectionNumber
    outcome: Outcome = Field(
        description="Whether the section ran, was skipped, kept what an earlier run made, was stopped, or failed."
    )
    elapsed_seconds: Elapsed


class StageProgress(Event):
    """How far through its own work one stage is, counted in the thing it is working on.

    Narrate emits one per take, record one per section, score one per score item, assemble one per
    encoding pass and verify one per probe, so every stage that takes time reports the same shape.
    """

    event: Literal["stage.progress"] = Field("stage.progress", description=MOMENT)
    stage: Stage = Field(description="The stage this line is about.")
    section: SectionNumber | None = Field(None, description="The section being worked on, or null.")
    done: int = Field(ge=0, description="How many of the things are finished.")
    total: int = Field(ge=0, description="How many things there are in all.")
    unit: Unit = Field(description="What one of those things is.")
    label: str = Field(description="What a renderer prints beside the count.")


class FindingRaised(Event):
    """A judgement was made, carried as it was found rather than held back until the result."""

    event: Literal["finding.raised"] = Field("finding.raised", description=MOMENT)
    finding: Finding = Field(description="The judgement, in the same shape the result will carry.")


class CostPriced(Event):
    """A cost was worked out, either before the run buys anything or after it did."""

    event: Literal["cost.priced"] = Field("cost.priced", description=MOMENT)
    cost: Cost = Field(description="The cost, with its state saying whether it is an estimate.")


class TakeCharged(Event):
    """The voice provider was paid for one take, which is the line a ledger of real charges reads.

    A `cost.priced` event prices a whole run, before or after it. This one is written at the moment a take
    is bought, once per take, so a host that keeps its own ledger can record every charge as it
    happens and can tell by the take's digest that a retried run did not buy the same take twice. A
    provider that declares it bills nothing is paid nothing, so its takes write no line. A request
    whose reply broke after it was sent may have been billed and is never sent again, so it writes a
    line flagged `possibly_charged`, which a ledger counts toward a ceiling and never toward money spent.
    """

    event: Literal["take.charged"] = Field("take.charged", description=MOMENT)
    section: SectionNumber
    digest: str = Field(pattern=r"^[0-9a-f]+$", description="The take's digest, which names its files.")
    characters: int = Field(ge=0, description="How many characters were sent for this take.")
    dollars: float = Field(ge=0, description="What this take cost at the price in force, in US dollars.")
    possibly_charged: bool = Field(
        False,
        description=(
            "True when the reply broke after the request was sent, so the provider may or may not have billed "
            "it and no take was written. Its dollars count toward the run's ceiling and never toward what it spent."
        ),
    )


class SoundCharged(Event):
    """The sound provider was paid for one request, which is a score item or one part of its music.

    It is written the moment the provider answers, once per paid request, so a host that keeps its
    own ledger records every sound it paid for and can tell by the digest that a retried run did not
    buy the same request twice. A request whose reply broke after it was sent, or that a Ctrl-C
    abandoned, may have been billed, so it writes a line flagged `possibly_charged`, as `take.charged` does.
    """

    event: Literal["sound.charged"] = Field("sound.charged", description=MOMENT)
    name: str = Field(description="What the author calls the item in decktalk.toml.")
    kind: SoundKind = Field(description="What kind of sound the item is: music, ambience or an effect.")
    digest: str = Field(pattern=r"^[0-9a-f]+$", description="The digest of the request that was paid for.")
    seconds: float = Field(ge=0, description="How many seconds of audio were asked for.")
    dollars: float = Field(ge=0, description="What this request cost at the rate in force, in US dollars.")
    possibly_charged: bool = Field(
        False,
        description=(
            "True when the reply broke after the request was sent, or a Ctrl-C abandoned it, so the provider may or "
            "may not have billed it and no sound was written. Its dollars count toward the run's ceiling and never "
            "toward what it spent."
        ),
    )


class ToolFetch(Event):
    """A tool is being downloaded, which is the one time a run waits on the network."""

    event: Literal["tool.fetch"] = Field("tool.fetch", description=MOMENT)
    tool: str = Field(description="What is being fetched, such as ffmpeg or chromium.")
    bytes: int = Field(ge=0, description="How many bytes have arrived.")
    total_bytes: int | None = Field(None, ge=0, description="How large the download is, or null when unstated.")


Scalar = str | int | float | bool | None
"""One measured value a log line carries, which is flat so a reader filters on it without parsing."""

LINE_CHARS = 2000
"""Truth: the most characters a log line's message or any one of its values keeps, which is a page of text.

A page's error or a tool's complaint is text someone else chose, and a line with no limit would let
one of them fill the events file alone.
"""

CUT = " (cut)"
"""What a text cut to `LINE_CHARS` ends with, so a reader knows the rest was there."""


def _cut(text: str) -> str:
    """The text held to `LINE_CHARS`, which runs after redaction so no prefix of a secret can survive the cut."""
    return text if len(text) <= LINE_CHARS else text[:LINE_CHARS] + CUT


class RunLog(Event):
    """One sentence the library would have printed, had the library printed anything.

    A stage says its sentence through its run, and a module below the stages writes a standard
    logging record, which the bridge in `logs.py` turns into this line. Both carry where they were
    written, so a reader can tie a line from one of several parallel recorders to its section
    without parsing the sentence.
    """

    event: Literal["run.log"] = Field("run.log", description=MOMENT)
    level: Level = Field(description="How much this line matters.")
    message: str = Field(description="One sentence.")
    source: str | None = Field(
        None, description="The module that wrote this line, such as media.ffmpeg, or null for a stage's own sentence."
    )
    stage: Stage | None = Field(None, description="The stage this line was written in, or null outside every stage.")
    section: SectionNumber | None = Field(
        None, description="The section this line was written in, or null outside every section."
    )
    data: dict[str, Scalar] | None = Field(
        None,
        description="The measured values behind the sentence by name, each a string, a number, a boolean or null.",
    )

    @field_validator("message", mode="after")
    @classmethod
    def _short(cls, message: str) -> str:
        """Hold the sentence to `LINE_CHARS`, after the event's own redaction has run."""
        return _cut(message)

    @field_validator("data", mode="after")
    @classmethod
    def _short_values(cls, data: dict[str, Scalar] | None) -> dict[str, Scalar] | None:
        """Hold every text value to `LINE_CHARS`, after the event's own redaction has run."""
        if data is None:
            return None
        return {name: _cut(value) if isinstance(value, str) else value for name, value in data.items()}

    @field_validator("data", mode="before")
    @classmethod
    def _flat(cls, given: object) -> object:
        """Keep a scalar as it is and store anything else as its `repr`, so no object reaches a line.

        A header map or a path handed over by mistake is written as text, which the redaction every
        event passes through then sees, rather than refused, because a log line that raises would lose
        the moment it was written to record.
        """
        if not isinstance(given, dict):
            return given
        return {
            str(name): value if value is None or isinstance(value, str | int | float | bool) else repr(value)
            for name, value in given.items()
        }


AnyEvent = Annotated[
    RunStart
    | RunDone
    | StageStart
    | StageDone
    | SectionStart
    | SectionDone
    | StageProgress
    | FindingRaised
    | CostPriced
    | TakeCharged
    | SoundCharged
    | ToolFetch
    | RunLog,
    Field(discriminator="event"),
]
"""One line of the stream, which a reader parses by its `event` and never by trying each shape."""

EVENTS: dict[str, type[Event]] = {kind.model_fields["event"].default: kind for kind in get_args(get_args(AnyEvent)[0])}
"""Every event by its name, which is the closed list `decktalk schema event` prints, read off `AnyEvent`."""

Listener = Callable[[Event], None]

DELIVERING: ContextVar[bool] = ContextVar("decktalk_delivering", default=False)
"""Whether this thread is already handing an event to the renderers, which only this thread can know.

A subscriber that raises is reported by a second event emitted from inside the first delivery, and
that second event must not report its own subscriber failures again. The flag is per thread and per
context rather than one field on the stream, because two runs delivering on two threads at once
would otherwise each see the other's delivery and drop their own failure lines.
"""


@dataclass(eq=False)
class Subscription:
    """One renderer attached to the stream, which detaches by closing or by leaving its `with`."""

    stream: Events
    listener: Listener
    runs: frozenset[str] | None

    def wants(self, event: Event) -> bool:
        """True when this subscriber asked for the run the event belongs to."""
        return self.runs is None or event.run in self.runs

    def close(self) -> None:
        """Detach this renderer, after which it receives nothing."""
        self.stream.detach(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Events:
    """Every event of every run, and the renderers watching them.

    The stream lives on the machine rather than on a project, because installing a toolchain and
    reporting on a machine hold no project, and `install` downloads two hundred megabytes with nothing
    else to report it. A project's own view is one of these that hands every subscription to the
    machine's stream, filtered to the runs that project opened.
    """

    def __init__(self, *, source: Events | None = None) -> None:
        self._source = source or self
        self._subscriptions: list[Subscription] = []
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()
        self._order = threading.RLock()

    def subscribe(self, listener: Listener, *, runs: Iterable[str] | None = None) -> Subscription:
        """Attach a renderer, which receives every event of the named runs, or of every run."""
        subscription = Subscription(self._source, listener, None if runs is None else frozenset(runs))
        with self._source._lock:
            self._source._subscriptions.append(subscription)
        return subscription

    def detach(self, subscription: Subscription) -> None:
        """Remove one subscription, which `Subscription.close` calls and nothing else needs."""
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

    def emit[E: Event](self, run: str, kind: type[E], /, **fields: Any) -> E:
        """Mint the four fields onto one event, hand it to every renderer, and give it back.

        The fields are typed Any because they are whatever the named event class declares, and the
        class validates them, so a narrower type here would only repeat every event's schema.

        The run and the class are positional-only, so an event field that shares a parameter's name,
        such as the `kind` of a `sound.charged` line, is always read as the field.

        The event is returned so a caller that needs what it just reported, such as the run id and
        the sink path on `run.start`, reads it off the line rather than working it out a second time.

        The counter of a run is dropped once its `run.done` line is delivered, because a service that
        keeps one machine for its whole life opens runs without end and would otherwise hold one
        counter for every run it ever made.
        """
        source = self._source
        # A number is taken, the line is built and it is delivered under one lock, so the lines of a
        # run reach every file in the order of their `seq` whichever threads wrote them, and a line
        # that fails to build takes no number. The lock is reentrant because a delivery that reports
        # a subscriber's failure emits again on the same thread.
        with source._order:
            with source._lock:
                seq = source._counters.get(run, 0)
            event = kind(time=datetime.now(UTC), seq=seq, run=run, **fields)
            with source._lock:
                source._counters[run] = seq + 1
            self._deliver(event)
            if isinstance(event, RunDone):
                with source._lock:
                    source._counters.pop(run, None)
        return event

    def _deliver(self, event: Event) -> None:
        """Hand one event to every renderer that wants it, and never let one of them stop the run."""
        source = self._source
        with source._lock:
            subscriptions = list(source._subscriptions)
        outermost = not DELIVERING.get()
        token = DELIVERING.set(True)
        failures: list[str] = []
        try:
            for subscription in subscriptions:
                if not subscription.wants(event):
                    continue
                try:
                    subscription.listener(event)
                except Exception as failure:  # noqa: BLE001  (a renderer must never stop a run)
                    failures.append(f"A subscriber raised {type(failure).__name__} on a {event.event} line.")
        finally:
            DELIVERING.reset(token)
        if outermost:
            for message in failures:
                self.emit(event.run, RunLog, level=Level.ERROR, message=message)


DROPPED_PAST_THE_BOUND = (StageProgress, ToolFetch, RunLog)
"""The lines a bounded file may leave out, which are the ones the tools a run called can multiply.

Every other line is the run's own shape, a judgement or a dollar it spent, which is bounded by the
stages and sections of the run and is the ledger a host bills from, so it is always written. The
list names what may go rather than what stays, so an event added later is kept until someone
decides otherwise.
"""

LOUD = frozenset((Level.WARNING, Level.ERROR))
"""The log levels a bounded file keeps after it has left the quieter ones out."""

LOUD_HEADROOM = 2
"""How many times its bound a file may reach before warnings and errors are left out too.

The quiet lines go first, so a file at its bound still says what went wrong, and a run that warns
without end still stops somewhere.
"""


class JsonlSink:
    """A subscriber that appends one JSON line per event to a file, creating it at the first line.

    One file per run, never truncated, so a watch loop running beside a build by hand cannot
    overwrite what the other is writing. `max_bytes` bounds the file: once it is reached, `stage.progress`,
    `tool.fetch`, debug and info lines are left out and counted, warnings and errors follow them past
    `LOUD_HEADROOM` times the bound, and every line not in `DROPPED_PAST_THE_BOUND` is always written.
    """

    def __init__(self, path: Path, *, max_bytes: int | None = None) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.written = 0
        """How many bytes this sink has appended, which is what the bound is compared against."""
        self.dropped = 0
        """How many lines this sink left out, which the run's last line reports."""
        self._lock = threading.Lock()

    def _keeps(self, event: Event) -> bool:
        """Whether this line is written, given how much of the bound the file has already used."""
        if self.max_bytes is None or self.written < self.max_bytes or not isinstance(event, DROPPED_PAST_THE_BOUND):
            return True
        loud = isinstance(event, RunLog) and event.level in LOUD
        return loud and self.written < self.max_bytes * LOUD_HEADROOM

    def __call__(self, event: Event) -> None:
        line = (event.model_dump_json() + "\n").encode()
        with self._lock:
            if not self._keeps(event):
                self.dropped += 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as sink:
                sink.write(line)
            self.written += len(line)

    @staticmethod
    def prune(directory: Path, keep: int) -> tuple[Path, ...]:
        """Delete all but the newest `keep` event files, and say which ones went.

        A directory of every run this project ever made would grow without limit, and the runs a
        caller wants are the recent ones. Another run may prune the same directory at the same time,
        so a file that is gone before it is looked at or removed is simply one fewer to prune.
        """
        if not directory.is_dir():
            return ()
        dated: list[tuple[float, Path]] = []
        for path in directory.glob("*.jsonl"):
            try:
                dated.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                # silent: another run pruned the same file first.
                continue
        files = [path for _, path in sorted(dated, key=lambda pair: pair[0], reverse=True)]
        gone = tuple(files[keep:])
        for path in gone:
            path.unlink(missing_ok=True)
        return gone


__all__ = [
    "Event",
    "Events",
    "FindingRaised",
    "JsonlSink",
    "Level",
    "AnyEvent",
    "RunLog",
    "RunDone",
    "RunStart",
    "SectionDone",
    "SectionStart",
    "SoundCharged",
    "CostPriced",
    "TakeCharged",
    "StageDone",
    "StageProgress",
    "StageStart",
    "Subscription",
    "ToolFetch",
    "Unit",
]
