"""One call in progress, and the threshold its caller judges what it found by.

A `Run` is one call in progress: its id, the stream it writes to, the token that stops it and the
gate it passes before it spends anything. Every call on a machine and on a project opens one, which
is what makes the event stream total: `install` and `doctor` hold no project and would otherwise
leave a renderer silent through the two commands that download two hundred megabytes.

The spend gate lives here rather than on the command line, because a service refuses the same spend
for the same reason. No call buys anything unless its caller said it may spend, and a ceiling is
checked before the first request rather than counted down as the money goes.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from decktalk.errors import STOPS, ApprovalRequired, Cancel
from decktalk.events import (
    CostPriced,
    Event,
    FindingRaised,
    Level,
    RunLog,
    SectionDone,
    SectionStart,
    StageDone,
    StageProgress,
    StageStart,
    ToolFetch,
    Unit,
)
from decktalk.findings import ERRORS_FAIL, Finding, Threshold
from decktalk.inputs.paths import relative
from decktalk.logs import WHERE, level_of, source_of, within
from decktalk.pipeline import Outcome, Stage
from decktalk.results import (
    DOLLAR_DIGITS,
    BillingBasis,
    Cost,
    Layer,
    Result,
    money,
)

if TYPE_CHECKING:  # pragma: no cover
    from decktalk.machine import Machine

log = logging.getLogger(__name__)

RUN_DIGITS = 12
"""How much of a random id names a run, which is enough that two runs never share a file."""


def new_run() -> str:
    """A fresh run id, which names this run's events file and its rows in `status`."""
    return uuid.uuid4().hex[:RUN_DIGITS]


@dataclass
class Ending:
    """How a block the run timed ended, which the block may set before it returns."""

    outcome: Outcome = Outcome.RAN


_PROVIDER_OF = {Stage.NARRATE: "the voice", Stage.SCORE: "the score's provider"}
"""Who is paid for what each stage that buys buys, as a refusal names it."""


def _undeclared(cost: Cost) -> str:
    """Who declares no bill in this price, with its verb: the provider of each buying row nobody can price.

    A price with no such row names the voice when it counts characters, and the score's provider otherwise.
    """
    rows = [
        _PROVIDER_OF[row.stage]
        for row in cost.stages
        if row.state is cost.state and row.buys and row.billing is BillingBasis.UNDECLARED
    ]
    if not rows:
        return f"{'the voice' if cost.characters > 0 else _PROVIDER_OF[Stage.SCORE]} declares"
    return f"{' and '.join(rows)} {'declares' if len(rows) == 1 else 'declare'}"


class Run:
    """One call in progress: its id, its stream, its cancel token, its spend gate and its threshold.

    A stage is handed one of these and reports through it. It is the only thing a stage has that
    knows about the machine, so a stage can neither read the environment nor print. A stage builds
    every provider from `run.machine`, so a thread the run hands work to answers with the same
    providers whether or not it copied the context of the thread that opened the run.
    """

    def __init__(
        self,
        machine: Machine,
        *,
        id: str,
        cancel: Cancel,
        spend: bool = False,
        max_cost: float | None = None,
        root: Path | None = None,
        threshold: Threshold = ERRORS_FAIL,
    ) -> None:
        self.id = id
        self.machine = machine
        self.cancel = cancel
        self.spend = spend
        self.max_cost = max_cost
        # The most everything this run approved under `max_cost` can cost, which the cap is held against.
        self.approved = 0.0
        self.root = root
        # The caller's own rule for which findings fail it, which `ok` is judged by and a build stops on.
        self.threshold = threshold
        self.written: list[Path] = []
        self.findings: list[Finding] = []
        self.opened = time.monotonic()

    # ---- the stream ---------------------------------------------------------------------

    def emit[E: Event](self, kind: type[E], /, **fields: object) -> E:
        """Put one line on the stream, with the four fields the library mints already on it."""
        return self.machine.events.emit(self.id, kind, **fields)

    def note(self, message: str, *, level: Level = Level.INFO) -> None:
        """One sentence the library would have printed, had the library printed anything."""
        place = WHERE.get()
        self.emit(RunLog, level=level, message=message, stage=place.stage, section=place.section)

    def logged(self, record: logging.LogRecord) -> None:
        """Turn one standard logging record into one line of this run, which is the bridge's receiver.

        The sentence is the record's own rendering, in which a `Secret` argument already reads as its
        name. A record that carries an exception adds the exception's type and the first line of its
        message to `data`, and never the traceback, because a traceback carries locals and paths.
        """
        given = getattr(record, "data", None)
        data = dict(given) if isinstance(given, Mapping) else {}
        if record.exc_info and record.exc_info[1] is not None:
            failure = record.exc_info[1]
            first = next(iter(str(failure).splitlines()), "")
            data["error"] = f"{type(failure).__name__}: {first}" if first else type(failure).__name__
        place = WHERE.get()
        self.emit(
            RunLog,
            level=level_of(record.levelno),
            message=record.getMessage(),
            source=source_of(record.name),
            stage=place.stage,
            section=place.section,
            data=data or None,
        )

    def found(self, finding: Finding) -> Finding:
        """Record one judgement and report it as it was made, rather than holding it to the end."""
        self.findings.append(finding)
        self.emit(FindingRaised, finding=finding)
        return finding

    def progress(
        self, stage: Stage, *, done: int, total: int, unit: Unit, label: str, section: int | None = None
    ) -> None:
        """How far through its own work a stage is, counted in the thing it is working on."""
        self.emit(StageProgress, stage=stage, section=section, done=done, total=total, unit=unit, label=label)

    def wrote(self, path: Path) -> Path:
        """Record one file this run wrote, which is what fills `written` without a stage listing it twice."""
        self.written.append(path)
        return path

    def check(self) -> None:
        """Raise `Cancelled` when the caller has asked the run to stop, which a stage calls between sections."""
        self.cancel.check()

    def stage(self, stage: Stage, *, index: int = 1, count: int = 1) -> AbstractContextManager[Ending]:
        """Open and close one stage on the stream, whatever the stage does inside."""
        return self._timed(StageStart, StageDone, stage=stage, opening={"index": index, "count": count})

    def section(self, stage: Stage, section: int) -> AbstractContextManager[Ending]:
        """Open and close one section of one stage on the stream, and check the cancel token first."""
        self.check()
        return self._timed(SectionStart, SectionDone, stage=stage, section=section)

    @contextmanager
    def _timed(
        self, start: type[Event], done: type[Event], *, opening: Mapping[str, int] | None = None, **both: object
    ) -> Iterator[Ending]:
        """Emit `start`, run the block, and emit `done` with how it ended and how long it took.

        A block the caller cancelled or interrupted ends as stopped, because nothing in it failed. A
        block that returns normally ends as its `Ending` says, which is ran unless the block set it.
        """
        started = time.monotonic()
        self.emit(start, **both, **(opening or {}))
        stage, section = both.get("stage"), both.get("section")
        ending = Ending()
        try:
            with within(stage=cast("Stage | None", stage), section=cast("int | None", section)):
                yield ending
        except BaseException as failure:
            ended = Outcome.STOPPED if isinstance(failure, STOPS) else Outcome.FAILED
            self.emit(done, **both, outcome=ended, elapsed_seconds=time.monotonic() - started)
            raise
        self.emit(done, **both, outcome=ending.outcome, elapsed_seconds=time.monotonic() - started)

    def fetching(self, tool: str, done_bytes: int, total_bytes: int | None = None) -> None:
        """A tool is arriving, which is the one moment a run stops for the network.

        The three arguments are the three fields of the line, so a fetcher reports and nothing in
        between translates. This is the listener the run binds for its own length.
        """
        self.emit(ToolFetch, tool=tool, bytes=done_bytes, total_bytes=total_bytes)

    # ---- the spend gate -----------------------------------------------------------------

    def approve(self, cost: Cost) -> Cost:
        """Let a priced request through, or refuse it before anything is bought.

        Every stage that buys passes its price through here before its first request, so no stage
        can spend without its caller's approval and no ceiling can be passed halfway. Spend gates
        money and nothing else, so a price whose provider declares it bills nothing passes whatever
        the run may spend, and is never asked about. `--max-cost` caps the whole run. A build holds
        its whole price against it first, through `approve_whole`, and each approval here then adds
        the most it can cost to what this run already approved, so a stage that asks for more than
        the build was priced at is refused before anything in it is bought. The ceiling is summed
        and never the estimate, because a provider charges one request at a time. What the run
        bought before such a refusal stays bought and kept.
        """
        self.emit(CostPriced, cost=cost)
        if cost.free:
            return cost
        if not self.spend:
            raise ApprovalRequired(
                f"{cost.sentence} Nothing approved it.",
                hint="Pass --spend to approve it, or --no-spend to play placeholders where a take is missing.",
            )
        if self.max_cost is None:
            return cost
        self.approved = self._capped(cost, self.max_cost, already=self.approved)
        return cost

    def approve_whole(self, cost: Cost) -> None:
        """Hold the whole run's price against `max_cost` once, before any stage buys anything.

        A run that buys from more than one stage is refused here when their sum is over the cap, so
        it buys nothing rather than buying its takes and being refused at its sounds. Nothing is
        recorded as approved, because each stage still passes `approve` with its own price, and the
        running total there refuses a stage that asks for more than this price allowed for.
        """
        if self.spend and self.max_cost is not None and cost.buys and not cost.free:
            self._capped(cost, self.max_cost, already=0.0)

    def _capped(self, cost: Cost, cap: float, *, already: float) -> float:
        """The most the run can cost with `cost` added to what it `already` approved, refused over `cap`."""
        if cost.billing is BillingBasis.UNDECLARED:
            raise ApprovalRequired(
                f"--max-cost was given and {_undeclared(cost)} no bill, so the cap would guard a made-up price.",
                hint=(
                    "A provider DeckTalk ships states how it bills, per character, per second or free, and one "
                    "a host registered itself states nothing DeckTalk can price. Run without --max-cost to buy "
                    "from one that declares none."
                ),
            )
        if cost.price_layer is Layer.DEFAULT:
            raise ApprovalRequired(
                "--max-cost was given and nothing states the rate this run buys at, so the cap would guard a made-up "
                "price.",
                hint=(
                    f"Set {cost.price_key or 'the rate its provider declares'} to what your plan charges, "
                    "then run it again."
                ),
            )
        most = round(already + cost.ceiling_dollars, DOLLAR_DIGITS)
        if most > cap:
            raise ApprovalRequired(
                f"{cost.sentence} {self._over(most, cap, already)}",
                hint=f"Raise the ceiling to --max-cost {most:.2f}, or narrow the run with --section.",
            )
        return most

    @staticmethod
    def _over(most: float, cap: float, already: float) -> str:
        """Why the cap refused: the whole run's most against the cap, with what was bought before it kept."""
        if not already:
            return f"The most it can cost, {money(most)}, is over the {money(cap)} ceiling --max-cost set."
        return (
            f"With the {money(already)} this run already approved, the most it can cost is {money(most)}, "
            f"which is over the {money(cap)} ceiling --max-cost set. What it bought before this is kept, and nothing "
            "more is bought."
        )

    # ---- the result ---------------------------------------------------------------------

    def result[R: Result](
        self,
        model: type[R],
        *,
        findings: Iterable[Finding] | None = None,
        written: Iterable[Path] | None = None,
        **fields: object,
    ) -> R:
        """Fill one result: this run's id, the judgements it made, the files it wrote and how long it took.

        `ok` is false when any judgement reaches this run's threshold, which is the caller's own and is
        the same rule its exit code is read from, so no stage decides for itself what counts as having
        found something. A stage that reported its judgements and its files through the run names
        neither here, and no stage times itself.
        """
        judged = tuple(findings) if findings is not None else tuple(self.findings)
        declared = model.model_fields
        if "run" in declared:
            fields.setdefault("run", self.id)
        if "written" in declared:
            paths = self.written if written is None else list(written)
            fields.setdefault("written", tuple(dict.fromkeys(self._relative(path) for path in paths)))
        if "elapsed_seconds" in declared:
            fields.setdefault("elapsed_seconds", time.monotonic() - self.opened)
        fields.setdefault("ok", not self.threshold.fails(judged))
        return model(findings=judged, **cast("dict[str, Any]", fields))

    def _relative(self, path: Path) -> Path:
        return relative(path, self.root) if self.root else path
