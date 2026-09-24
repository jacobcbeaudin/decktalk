"""One stream of progress: eleven moments, the four fields the library mints onto each, and the
subscribers that render them.

Every call opens a run and writes to this stream. The Rich live region, the JSON lines `--events`
prints on stderr, the per-run file under `build/events/` and any later dashboard are all subscribers
to it, so a renderer never computes a fraction and there is only one channel to keep in step.

`event` is the discriminator and there are eleven names. Skip and fail are not names: `stage.done`
and `section.done` carry an `outcome`, because three names for one moment would force three branches
where one field read will do.

The library mints `event`, `time`, `seq` and `run`, so an emitter states only what it measured.
`seq` counts per run rather than per machine, because a machine-wide counter would leave gaps in
every file and a reader could not tell a gap from a lost line.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, Self, TypeVar

from pydantic import BaseModel, Field

from decktalk.findings import MODEL, Finding, ProjectPath
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Elapsed, Run, SectionNumber, Spend

MOMENT = "Which moment this line reports, which is what a reader dispatches on."
"""The one sentence the discriminator publishes, so all eleven names describe themselves alike."""


class Unit(Enum):
    """What one step of a progress line counts, so a renderer can name the thing rather than a number."""

    TAKE = "take"
    SECTION = "section"
    ASSET = "asset"
    PASS = "pass"
    PROBE = "probe"


class Level(Enum):
    """How much a log line matters, which is what `-v` and `-q` choose between."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Event(BaseModel):
    """What every line of the stream carries, whichever moment it reports."""

    model_config = MODEL

    event: str = Field(description=MOMENT)
    time: datetime = Field(description="When this happened, as an instant.", json_schema_extra={"volatile": True})
    seq: int = Field(ge=0, description="This line's place in its run, counting from zero.")
    run: Run


class RunStart(Event):
    """A run opened, and this is where its lines are being written."""

    event: Literal["run.start"] = Field("run.start", description=MOMENT)
    events_path: ProjectPath | None = Field(
        None, description="The file this run's lines are appended to, or null when no project holds one."
    )


class RunDone(Event):
    """A run closed, and this is how it ended."""

    event: Literal["run.done"] = Field("run.done", description=MOMENT)
    outcome: Outcome = Field(description="Whether the run finished, was stopped, or failed.")
    seconds: Elapsed


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
    outcome: Outcome = Field(description="Whether the stage ran, was skipped, or failed.")
    seconds: Elapsed


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
    outcome: Outcome = Field(description="Whether the section ran, was skipped, or failed.")
    seconds: Elapsed


class Progress(Event):
    """How far through its own work one stage is, counted in the thing it is working on.

    Narrate emits one per take, record one per section, soundscape one per asset, assemble one per
    encoding pass and verify one per probe, so every stage that takes time reports the same shape.
    """

    event: Literal["progress"] = Field("progress", description=MOMENT)
    stage: Stage = Field(description="The stage this line is about.")
    section: SectionNumber | None = Field(None, description="The section being worked on, or null.")
    done: int = Field(ge=0, description="How many of the things are finished.")
    total: int = Field(ge=0, description="How many things there are in all.")
    unit: Unit = Field(description="What one of those things is.")
    label: str = Field(description="What a renderer prints beside the count.")


class FindingEvent(Event):
    """A judgement was made, carried as it was found rather than held back until the result."""

    event: Literal["finding"] = Field("finding", description=MOMENT)
    finding: Finding = Field(description="The judgement, in the same shape the result will carry.")


class SpendEvent(Event):
    """A price was worked out, either before the run buys anything or after it did."""

    event: Literal["spend"] = Field("spend", description=MOMENT)
    spend: Spend = Field(description="The price, with its state saying whether it is an estimate.")


class Fetch(Event):
    """A tool is being downloaded, which is the one moment a run stops for the network."""

    event: Literal["fetch"] = Field("fetch", description=MOMENT)
    tool: str = Field(description="What is being fetched, such as ffmpeg or chromium.")
    bytes: int = Field(ge=0, description="How many bytes have arrived.")
    total_bytes: int | None = Field(None, ge=0, description="How large the download is, or null when unstated.")


class Log(Event):
    """One sentence the library would have printed, had the library printed anything."""

    event: Literal["log"] = Field("log", description=MOMENT)
    level: Level = Field(description="How much this line matters.")
    message: str = Field(description="One sentence.")


Line = Annotated[
    RunStart
    | RunDone
    | StageStart
    | StageDone
    | SectionStart
    | SectionDone
    | Progress
    | FindingEvent
    | SpendEvent
    | Fetch
    | Log,
    Field(discriminator="event"),
]
"""One line of the stream, which a reader parses by its `event` and never by trying each shape."""

EVENTS: dict[str, type[Event]] = {
    "run.start": RunStart,
    "run.done": RunDone,
    "stage.start": StageStart,
    "stage.done": StageDone,
    "section.start": SectionStart,
    "section.done": SectionDone,
    "progress": Progress,
    "finding": FindingEvent,
    "spend": SpendEvent,
    "fetch": Fetch,
    "log": Log,
}
"""Every event by its name, which is the closed list `decktalk schema event` prints."""

E = TypeVar("E", bound=Event)
Listener = Callable[[Event], None]


class Subscription:
    """One renderer attached to the stream, which detaches by closing or by leaving its `with`."""

    def __init__(self, stream: Events, listener: Listener, runs: frozenset[str] | None) -> None:
        self._stream = stream
        self._listener = listener
        self._runs = runs

    def wants(self, event: Event) -> bool:
        """True when this subscriber asked for the run the event belongs to."""
        return self._runs is None or event.run in self._runs

    def deliver(self, event: Event) -> None:
        """Hand one event to the renderer."""
        self._listener(event)

    def close(self) -> None:
        """Detach this renderer, after which it receives nothing."""
        self._stream.detach(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Events:
    """Every event of every run, and the renderers watching them.

    The stream lives on the machine rather than on a project, because installing a toolchain and
    reporting on a machine hold no project and would otherwise leave `--events` silent on the two
    commands that download two hundred megabytes. A project's own view is one of these filtered to
    the runs that project opened.
    """

    def __init__(self, *, source: Events | None = None, runs: Iterable[str] | None = None) -> None:
        self._source = source or self
        self._runs = frozenset(runs) if runs is not None else None
        self._subscriptions: list[Subscription] = []
        self._counters: dict[str, itertools.count[int]] = {}
        self._lock = threading.Lock()
        self._delivering = False

    def view(self, runs: Iterable[str]) -> Events:
        """This stream filtered to the named runs, which is what a project hands its own callers."""
        return Events(source=self._source, runs=runs)

    def subscribe(self, listener: Listener, *, runs: Iterable[str] | None = None) -> Subscription:
        """Attach a renderer, which receives every event of the runs this view covers."""
        wanted = self._runs if runs is None else frozenset(runs)
        subscription = Subscription(self._source, listener, wanted)
        self._source.attach(subscription)
        return subscription

    def attach(self, subscription: Subscription) -> None:
        """Add one subscription to the stream, which `subscribe` calls and nothing else needs."""
        with self._lock:
            self._subscriptions.append(subscription)

    def detach(self, subscription: Subscription) -> None:
        """Remove one subscription, which `Subscription.close` calls and nothing else needs."""
        with self._lock:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

    def emit(self, run: str, kind: type[E], **fields: Any) -> E:
        """Mint the four fields onto one event, hand it to every renderer, and give it back.

        The event is returned so a caller that needs what it just reported, such as the run id and
        the sink path on `run.start`, reads it off the line rather than working it out a second time.
        """
        with self._lock:
            counter = self._counters.setdefault(run, itertools.count())
            seq = next(counter)
        event = kind(time=datetime.now(UTC), seq=seq, run=run, **fields)
        self._deliver(event)
        return event

    def _deliver(self, event: Event) -> None:
        """Hand one event to every renderer that wants it, and never let one of them stop the run."""
        with self._lock:
            subscriptions = list(self._subscriptions)
            outermost = not self._delivering
            self._delivering = True
        failures: list[str] = []
        try:
            for subscription in subscriptions:
                if not subscription.wants(event):
                    continue
                try:
                    subscription.deliver(event)
                except Exception as failure:  # noqa: BLE001  (a renderer must never stop a run)
                    failures.append(f"A subscriber raised {type(failure).__name__} on a {event.event} line.")
        finally:
            if outermost:
                with self._lock:
                    self._delivering = False
        if outermost:
            for message in failures:
                self.emit(event.run, Log, level=Level.ERROR, message=message)


class JsonlSink:
    """A subscriber that appends one JSON line per event to a file, creating it at the first line.

    One file per run, never truncated, so a watch loop running beside a build by hand cannot
    overwrite what the other is writing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def __call__(self, event: Event) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as sink:
                sink.write(event.model_dump_json() + "\n")

    @staticmethod
    def prune(directory: Path, keep: int) -> tuple[Path, ...]:
        """Delete all but the newest `keep` event files, and say which ones went.

        A directory of every run this project ever made would grow without limit, and the runs a
        caller wants are the recent ones.
        """
        if not directory.is_dir():
            return ()
        files = sorted(directory.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
        gone = tuple(files[keep:])
        for path in gone:
            path.unlink()
        return gone


__all__ = [
    "Event",
    "Events",
    "Fetch",
    "FindingEvent",
    "JsonlSink",
    "Level",
    "Line",
    "Log",
    "Progress",
    "RunDone",
    "RunStart",
    "SectionDone",
    "SectionStart",
    "SpendEvent",
    "StageDone",
    "StageStart",
    "Subscription",
    "Unit",
]
