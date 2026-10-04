"""A project is a directory, one object opens it, and every call on it opens a run.

    my-lesson/
      decktalk.toml      what this presentation is, and every setting changed for it
      script.md          the narration, in "## N. Title" sections
      cues.json          which spoken phrase each moment lands on
      deck/index.html    the slides, which the runtime gives its query contract
      media/             the author's own clips, ambience beds and markers
      .env               the speech credential, which is never committed
      build/             everything generated

`decktalk.open(path)` returns a `Project`. Six verbs move it forward, `build` runs them in order,
five more calls report on it or cut a piece out of it, `serve` puts it on a local origin and `apply`
carries out a fix. Every one of them opens a run on the machine's event stream. Every one but
`serve` returns a frozen result whose findings carry a code, a place, a severity and often a fix,
and every one but `serve` and `apply` takes a cancel token. `price` alone opens no run, because
it says what a run would buy before anybody approves it.

This module is the facade and nothing below it may import it. It is also the only module that
reaches down into the stages, and it imports them when a call is made rather than at the top,
because a stage opens a browser and an encoder and importing the command line must load neither. A
verb calls its stage through `stages.table.CALLS`, the same row `build` calls. `Inputs` is what a
stage is handed, so a stage never sees a project, a machine or a run opener and can neither read the
environment nor print.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout

from decktalk.errors import Cancel, InputError, ProjectLocked
from decktalk.events import Event, Events, Level, Subscription
from decktalk.files import replace_all
from decktalk.findings import ERRORS_FAIL, Finding, Threshold
from decktalk.inputs import Inputs
from decktalk.inputs.paths import at
from decktalk.machine import Machine
from decktalk.machine.fixes import apply_fixes
from decktalk.machine.run import Run, new_run
from decktalk.pipeline import Stage
from decktalk.results import (
    ApplyResult,
    AssembleResult,
    BuildResult,
    CheckResult,
    ClipResult,
    Cost,
    CueResult,
    NarrateResult,
    RecordResult,
    Result,
    Scope,
    ScoreResult,
    ServeResult,
    StatusResult,
    StoryboardResult,
    VerifyResult,
    WordsResult,
)
from decktalk.settings import PROJECT_FILE, PROJECT_VARIABLE, Layers, Settings
from decktalk.settings.layers import route, scoped

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
"""The interface `serve` listens on unless told otherwise, which no other machine can reach."""

CLOSE_POLL_SECONDS = 0.05
"""Calibration: how often a serving origin looks for a close, so closing it returns at once rather than in 0.5 s."""

NOT_AUTHORED = frozenset({"build", ".git", ".venv", "node_modules", "__pycache__"})
"""The directories no author edits, which are what a run, a tool or a package manager writes."""

SECTION_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")
"""One item of a section selection, which is a number or two numbers with a dash between them."""


def open(
    path: str | Path | None = None,
    *,
    machine: Machine | None = None,
    overrides: Iterable[str] = (),
    threshold: Threshold = ERRORS_FAIL,
) -> Project:
    """The project in `path`, in `DECKTALK_PROJECT`, or in the directory the process started in.

    The machine is made once when a caller passes none, so a script that opens two projects should
    make one itself and hand it to both, which is what keeps the toolchain and the stream shared.

    A machine a host built may carry overrides of its own, such as the page policy it enforces on
    every project. A project opened on it starts from those, and `overrides` come after them. They
    may set project-scoped keys only, as `Project` says. With no machine given, the machine is made
    from `overrides`, so every key reaches the layer it belongs to.

    `threshold` is which findings fail a call on this project, and every result's `ok` is read from
    it, as is the point a build stops at. The default fails on an error of any code.
    """
    pairs = tuple(overrides)
    here = machine or Machine.from_environment(overrides=pairs)
    named = path if path is not None else here.environ.get(PROJECT_VARIABLE)
    root = here.cwd / Path(named).expanduser() if named else here.cwd
    if root.is_file():
        root = root.parent
    return Project(here, root, overrides=pairs if machine is not None else (), threshold=threshold)


def section_numbers(selection: str) -> tuple[int, ...]:
    """The sections a selection such as `3,5-7` names, in order and without repeats.

    The library takes a run of numbers and never a string, so this sits at the edge a command line
    and a query string reach it through, where the text is parsed once into the one spelling.
    """
    found: list[int] = []
    for item in selection.replace(" ", "").split(","):
        match = SECTION_RANGE.fullmatch(item)
        if not match:
            raise InputError(
                f"{item!r} does not name a section or a run of them.",
                hint="Write a number such as 3, a run such as 5-7, or a list such as 3,5-7.",
            )
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        if last < first:
            raise InputError(
                f"{item!r} runs backwards, so it names no section.",
                hint=f"Write the lower number first, as in {last}-{first}.",
            )
        found.extend(range(first, last + 1))
    return tuple(dict.fromkeys(found))


class ProjectEvents(Events):
    """This project's view of the machine's stream, which follows the runs the project opens.

    The run set is read when a line is delivered rather than when a renderer attaches, because a
    caller subscribes before it makes the call it wants to watch, and a view fixed at subscription
    time would be silent through exactly that call.
    """

    def __init__(self, machine: Machine, runs: set[str]) -> None:
        super().__init__(source=machine.events)
        self._own = runs

    def subscribe(self, listener: Callable[[Event], None], *, runs: Iterable[str] | None = None) -> Subscription:
        """Attach a renderer, which receives the lines of every run this project opens."""
        wanted = self._own if runs is None else set(runs)
        return self._source.subscribe(lambda event: listener(event) if event.run in wanted else None)


class Origin:
    """A local origin serving one project, which `serve` hands back and a watch loop holds open."""

    def __init__(self, server: ThreadingHTTPServer, result: ServeResult) -> None:
        self._server = server
        self._stopped = threading.Event()
        self.result = result

    def wait(self) -> None:
        """Block until the origin is closed, which is what a command with no other work to do does."""
        self._stopped.wait()

    def close(self) -> None:
        """Stop serving, after which the URL answers nothing."""
        self._server.shutdown()
        self._server.server_close()
        self._stopped.set()

    def __enter__(self) -> Origin:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Project:
    """One project directory, opened once, with one call per command.

    Every stage call opens a run, takes `cancel`, and returns the frozen result named after it.
    `apply`, `serve`, `reload` and `sections_touching` take no `cancel`, because each of them finishes
    at once or, as `serve` does, hands back something the caller closes itself. Nothing here prints,
    nothing reads the environment, and every path a result carries is relative to `root`, so two
    projects in one process share nothing but the machine they were opened on.

    What the project was read into is private: a caller reads `settings` and `layers`, and every other
    fact about the project comes back on a result.
    """

    root: Path
    threshold: Threshold
    _inputs: Inputs
    machine: Machine
    events: Events

    def __init__(
        self, machine: Machine, root: Path, *, overrides: tuple[str, ...] = (), threshold: Threshold = ERRORS_FAIL
    ) -> None:
        """Open the project at `root` on `machine`, with the caller's `overrides` over the machine's own.

        `threshold` is which findings fail a call on this project. Every run the project opens carries
        it, so every result's `ok` and the point a build stops at are read from this one rule.

        A machine-scoped key in `overrides` is refused, because it names the browser DeckTalk launches
        and the trust it gives a page, and those belong to whoever built the machine. A host that
        forwards a tenant's pairs would otherwise hand the tenant both.
        """
        if taken := sorted(scoped(route(overrides), Scope.MACHINE)):
            raise InputError(
                f"'{taken[0]}' is machine-scoped, so an override of one project cannot set it.",
                hint="Set it in the overrides of the machine the project is opened on.",
            )
        self.machine = machine
        self.root = root.resolve()
        self.overrides = overrides
        self.threshold = threshold
        self._inputs = Inputs.load(
            self.root,
            environ=machine.environ,
            machine=dict(machine.tables),
            overrides=(*machine.overrides, *overrides),
            store=machine.store,
        )
        self._runs: set[str] = set()
        self.events = ProjectEvents(machine, self._runs)

    def __repr__(self) -> str:
        return f"Project({self.root.as_posix()!r})"

    # ---- what the project is -------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        """Every setting in force for this project on this machine, with each layer already applied."""
        return self._inputs.settings

    @property
    def layers(self) -> Layers:
        """What every layer said about every key, which is what a setting's provenance is read from.

        It is resolved at `open()` and again at `reload()`, so a watch loop that sees an edited
        project file sees the layer that set each key move with it.
        """
        return self._inputs.layers

    def reload(self) -> Project:
        """This project read again from disk, which is what a watch loop calls when a file changed."""
        return Project(self.machine, self.root, overrides=self.overrides, threshold=self.threshold)

    def sections_touching(self, path: Path) -> tuple[int, ...]:
        """Every section a change to this file would change, in section order."""
        return self._inputs.sections_touching(path)

    def select(self, only: Sequence[int] | None) -> tuple[int, ...] | None:
        """The sections a run of numbers selects in this project, refused when one is not a section.

        No selection is every section. A selection names sections the project carries and no
        others, so a number no section carries, or a selection that names nothing, is refused as
        `INPUT` with the sections there are, before any run is opened. Every call that takes `only`
        asks this first, and a command line that prices a run asks it before pricing, so a run that
        could select nothing never starts.
        """
        if only is None:
            return None
        named = tuple(dict.fromkeys(only))
        carried = [section.number for section in self._inputs.document.sections]
        missing = [number for number in named if number not in carried]
        if named and not missing:
            return named
        what = (
            f"no section carries the number {', '.join(str(number) for number in missing)}"
            if missing
            else "the selection names no section"
        )
        raise InputError(
            f"{what}, so the run would select nothing.",
            hint=f"The sections are {carried}.",
            location=at(self.root / PROJECT_FILE, self.root),
        )

    def authored_files(self) -> tuple[Path, ...]:
        """Every file under the project an author edits, which is what a watch loop polls for saves.

        The walk prunes a directory before it descends, so it never lists what `node_modules` or
        `.git` holds. The project's own build and take folders are pruned wherever its settings put
        them, because a build that wrote into a watched folder would start the next build without end.
        """
        workspace = self._inputs.workspace
        written = {workspace.build.resolve(), workspace.takes.resolve()}
        found: list[Path] = []
        for folder, dirs, files in os.walk(self.root):
            here = Path(folder)
            dirs[:] = [name for name in dirs if name not in NOT_AUTHORED and (here / name).resolve() not in written]
            found.extend(here / name for name in files)
        return tuple(found)

    # ---- the six verbs, in run order --------------------------------------------------------

    def narrate(
        self,
        *,
        only: Sequence[int] | None = None,
        spend: bool = False,
        max_cost: float | None = None,
        force: bool = False,
        replace_voiced: bool = False,
        cancel: Cancel | None = None,
    ) -> NarrateResult:
        """Speak each section of the script and time every word in it.

        Every take already on disk is played, voiced or placeholder, and the voice is built only when a
        take must be made, so a run that makes nothing reads no key. `spend` gates money and nothing
        else: set to true it buys the takes that are missing, and the default buys nothing, so each take
        a provider that bills would sell is a placeholder with estimated words and a `TAKE_MISSING`
        finding. A provider that declares it bills nothing, such as `dtsp`, makes every missing take
        either way, and one that cannot be reached plays a placeholder with a `TAKE_MISSING` finding
        that says to start it. `max_cost` is a ceiling in US dollars, checked before the first paid
        request. `force` makes each placeholder again and never buys: every voiced take on disk is kept.
        `replace_voiced` is the one way a voiced take is made again. A run that may call the voice makes
        each targeted take again, and one that may not plays a placeholder in its place and keeps the
        voiced take on disk.

        Raises `ApprovalRequired` when the run would spend over `max_cost`, and `ProviderError` when
        a provider that bills fails, or when a free provider answers and fails. Raises `Cancelled`
        when it is cancelled or interrupted. A refusal raised while takes are being made carries a
        `NarrateResult` of the takes made so far, and what they cost, as its `result`.
        """
        return self._stage(Stage.NARRATE, NarrateResult, cancel=cancel, spend=spend, max_cost=max_cost,
                          only=only, force=force, replace_voiced=replace_voiced)  # fmt: skip

    def cue(
        self,
        *,
        only: Sequence[int] | None = None,
        cancel: Cancel | None = None,
    ) -> CueResult:
        """Turn each cue phrase into a second on its own section's clock."""
        return self._stage(Stage.CUE, CueResult, cancel=cancel, only=only)

    def record(
        self,
        *,
        only: Sequence[int] | None = None,
        force: bool = False,
        cancel: Cancel | None = None,
    ) -> RecordResult:
        """Record each page section in a headless browser, against the seconds the cues named."""
        return self._stage(Stage.RECORD, RecordResult, cancel=cancel, only=only, force=force)

    def score(
        self,
        *,
        only: Sequence[int] | None = None,
        spend: bool = False,
        max_cost: float | None = None,
        replace_score: bool = False,
        cancel: Cancel | None = None,
    ) -> ScoreResult:
        """Compose the music, the ambience bed and the effects this project describes.

        `spend` set to true buys what needs buying, and the default reports the plan and buys
        nothing. `max_cost` is a ceiling in US dollars, checked before the first paid request. An
        item the ledger holds is never bought again while its request is unchanged and its audio is
        on disk, unless `replace_score` says to buy every bought sound again. A run that may not
        spend ignores it and keeps every bought sound on disk.

        Raises `ApprovalRequired` when the run would spend over `max_cost`, or when `max_cost` is given
        and nothing states the rate, and `ProviderError` when the sound provider fails on a run that may
        spend.
        """
        return self._stage(Stage.SCORE, ScoreResult, cancel=cancel, spend=spend,
                          max_cost=max_cost, only=only, replace_score=replace_score)  # fmt: skip

    def assemble(
        self,
        *,
        only: Sequence[int] | None = None,
        score: bool = True,
        loudness: bool = True,
        strict: bool = False,
        cancel: Cancel | None = None,
    ) -> AssembleResult:
        """Cut, mix and encode the sections into one film."""
        return self._stage(Stage.ASSEMBLE, AssembleResult, cancel=cancel, only=only, score=score,
                          loudness=loudness, strict=strict)  # fmt: skip

    def verify(self, *, only: Sequence[int] | None = None, cancel: Cancel | None = None) -> VerifyResult:
        """Measure the finished film: every start, every cut, every seam and every landing."""
        return self._stage(Stage.VERIFY, VerifyResult, cancel=cancel, only=only)

    # ---- the whole run ----------------------------------------------------------------------

    def build(
        self,
        *,
        stages: Sequence[Stage] | None = None,
        skip: Sequence[Stage] = (),
        only: Sequence[int] | None = None,
        spend: bool = False,
        max_cost: float | None = None,
        force: bool = False,
        replace_voiced: bool = False,
        replace_score: bool = False,
        loudness: bool = True,
        strict: bool = False,
        cancel: Cancel | None = None,
    ) -> BuildResult:
        """Run every stage in order, or the span of them `stages` names.

        `stages` is the span to run, in run order, and `skip` leaves stages out of it. A stage whose
        findings reach the project's threshold stops the run, and the result still comes back with
        its findings, its spend and the stage it stopped after in `stopped_at`. A threshold that fails
        on nothing runs every stage whatever it finds. The film carries the score unless
        `skip` names that stage, which is the one switch for that decision. `spend` means what it means
        to `narrate` and `score`, and `max_cost` caps the takes and the sounds together, so a
        build whose two prices pass it is refused before it buys anything. `replace_voiced` means what it means to
        `narrate` and `replace_score` means what it means to `score`, so each buys again only
        what its own stage bought. `force` means what it means to `narrate` and `record`, and it also
        cuts and measures a film that nothing changed again. It never buys, so every voiced take and
        every item of the score is kept. `loudness` and `strict` mean what they mean to
        `assemble`.

        Raises `ApprovalRequired`, `ProviderError` and `Cancelled` as `narrate` does, and `ToolError`
        when `strict` is true and the mix misses its loudness. A refusal raised after the run bought
        something carries a `BuildResult` with what it spent as its `result`. Under the untrusted page policy a run
        that may spend refuses to open a page, so a host voices with `narrate` and builds without
        `spend`.
        """
        from decktalk.stages.build import build  # noqa: PLC0415

        return self._call(build, BuildResult, cancel=cancel, spend=spend, max_cost=max_cost, stages=stages, skip=skip,
                          only=only, force=force, replace_voiced=replace_voiced, replace_score=replace_score,
                          loudness=loudness, strict=strict)  # fmt: skip

    def price(
        self,
        *,
        stages: Collection[Stage] | None = None,
        only: Sequence[int] | None = None,
        replace_voiced: bool = False,
        replace_score: bool = False,
    ) -> Cost:
        """What a run of these stages that may spend would buy, or, when none of them buys, the price of nothing at
        the voice's rate, so a reader always finds a rate.

        `stages` defaults to the whole pipeline, and an empty `stages` prices nothing. The options mean
        what they mean to `build`. It is
        the sum `build` holds `max_cost` against and reports, worked out from each stage's plan alone:
        no run is opened, no lock is taken, no voice is built and nothing is sent, so a caller prices a
        run before it asks anybody to approve it. Raises `InputError` when a stage that buys cannot be
        planned, which is the reason the run could not be priced.
        """
        from decktalk.stages import build  # noqa: PLC0415

        only = self.select(only)
        return build.price(self._inputs, tuple(stages if stages is not None else Stage), only=only,
                           replace_voiced=replace_voiced, replace_score=replace_score)  # fmt: skip

    # ---- the five that report or cut, and apply ---------------------------------------------

    def status(self, *, cancel: Cancel | None = None) -> StatusResult:
        """Report what is written, what is built, what is stale, and what to do next."""
        from decktalk.stages.status import status  # noqa: PLC0415

        return self._call(status, StatusResult, cancel=cancel, writes=False)

    def check(
        self,
        *paths: Path,
        only: Sequence[int] | None = None,
        pages: bool = True,
        frames: bool = True,
        cancel: Cancel | None = None,
    ) -> CheckResult:
        """Judge the script, the cue file and the pages before a build, and price the narration it would buy.

        `pages` set to false judges the written files with no browser at all and says which
        judgements it could not reach, so a new deck gets its first cue rows without a download and
        a hook that has no browser can still run. `frames` set to false keeps the browser and drops
        the freeze comparison.

        A check that opens pages freezes frames into the build directory and draws the storyboard
        over them, so it holds the build lock for as long as a stage that produces would. A check
        with no pages reads and judges alone, so it takes nothing and runs beside a build.
        """
        from decktalk.stages.check import check  # noqa: PLC0415

        return self._call(check, CheckResult, cancel=cancel, writes=pages, paths=tuple(paths), only=only,
                          pages=pages, frames=frames)  # fmt: skip

    def words(self, *, only: Sequence[int] | None = None, cancel: Cancel | None = None) -> WordsResult:
        """Every spoken word with its start and its end, which is how a cue phrase is written."""
        from decktalk.stages.words import words  # noqa: PLC0415

        return self._call(words, WordsResult, cancel=cancel, writes=False, only=only)

    def storyboard(
        self,
        *,
        only: Sequence[int] | None = None,
        slides: Sequence[str] | None = None,
        after: Sequence[str] | None = None,
        before: Sequence[str] | None = None,
        times: Sequence[float] | None = None,
        cancel: Cancel | None = None,
    ) -> StoryboardResult:
        """Freeze every slide at every cue onto one page, which is the checkpoint before anything is bought.

        The four selectors beside `only` narrow which panels are drawn. `slides` names the slides,
        `after` and `before` name the state just after and just before one cue, which are the pair
        an author compares to see what a reveal changed, and `times` names seconds of the section's
        own clock. Each one repeats, and one that matches nothing draws nothing.
        """
        from decktalk.stages.storyboard import storyboard  # noqa: PLC0415

        return self._call(storyboard, StoryboardResult, cancel=cancel, only=only, slide=slides, after=after,
                          before=before, at=times)  # fmt: skip

    def clip(
        self,
        section: int,
        *,
        start: float,
        end: float,
        out: Path,
        gain_db: float = 0.0,
        hold_seconds: float = 0.0,
        cancel: Cancel | None = None,
    ) -> ClipResult:
        """Cut a span of one built section into its own file, refusing a section the project does not carry."""
        from decktalk.stages.clip import clip  # noqa: PLC0415

        self.select((section,))
        return self._call(clip, ClipResult, cancel=cancel, section=section, start=start, end=end, out=out,
                          gain_db=gain_db, hold_seconds=hold_seconds)  # fmt: skip

    def apply(self, findings: Finding | Iterable[Finding], *, unsafe: bool = False) -> ApplyResult:
        """Carry out the fixes a set of findings offer, and say what each one did.

        A safe fix cannot lose the author's work and is applied. An unsafe fix can, so it is applied
        only when the caller asked for that. A fix only a person can make is reported and never
        applied.
        """
        with self._open(writes=True) as run:
            return apply_fixes(run, findings, root=self.root, scope=Scope.PROJECT, unsafe=unsafe)

    # ---- the local origin ---------------------------------------------------------------------

    def serve(self, *, host: str = LOOPBACK, port: int = 0) -> Origin:
        """Serve this project's deck on a local origin, and hand back the origin rather than a result.

        The origin answers only for the deck directory and the files the document declares, so a
        page the recorder drives and a preview an author leaves running reach the same short list
        and neither can read the credential beside it. It also answers one alias, which is where a
        previewed page reads its resolved cues, since a preview has no recorder to put them in its
        URL and the build directory is not served.
        """
        # The server is the media layer's, which carries the routing every recorded page also uses.
        from decktalk.media.origin import Allowed, open_server, served_url  # noqa: PLC0415

        with self._open(writes=False) as run:
            allowed = Allowed.of(self.root, self._inputs.served_paths())
            server = open_server(allowed, host, port, self._inputs.documents())
            result = run.result(
                ServeResult,
                url=served_url(server),
                port=int(server.server_address[1]),
            )
            threading.Thread(target=server.serve_forever, args=(CLOSE_POLL_SECONDS,), daemon=True).start()
            return Origin(server, result)

    # ---- how every call is made -----------------------------------------------------------------

    def _stage[R: Result](self, stage: Stage, model: type[R], **options: Any) -> R:
        """Call one stage through the row `build` calls it through, so a replaced row is obeyed by both."""
        from decktalk.stages.table import CALLS  # noqa: PLC0415

        return self._call(CALLS[stage].call, model, **options)

    def _call[R: Result](
        self,
        call: Callable[..., Result],
        model: type[R],
        *,
        cancel: Cancel | None = None,
        spend: bool = False,
        max_cost: float | None = None,
        writes: bool = True,
        **options: object,
    ) -> R:
        """Open a run, hold the build directory when the call writes, and hand the call its inputs.

        `model` is the result this command answers with, which is named after the command itself, so
        a call that answers with another is a bug caught here rather than in the caller. A call that
        takes `only` has its selection judged by `select` before the run opens.
        """
        if "only" in options:
            options["only"] = self.select(cast("Sequence[int] | None", options["only"]))
        with self._open(cancel=cancel, spend=spend, max_cost=max_cost, writes=writes) as run:
            answered = call(self._inputs, run, **options)
        if not isinstance(answered, model):
            command = model.__name__.removesuffix("Result").lower()
            raise TypeError(f"{command} answered with {type(answered).__name__} rather than {model.__name__}")
        return answered

    @contextmanager
    def _open(
        self,
        *,
        cancel: Cancel | None = None,
        spend: bool = False,
        max_cost: float | None = None,
        writes: bool = True,
    ) -> Iterator[Run]:
        """One run of this project, with its lines beside the build and its lock held while it writes.

        Every run writes its event lines under the build directory and may prune old ones, so the
        tree is confined before the run opens, whether or not the stage itself writes.
        """
        self._inputs.workspace.confine()
        files = self._inputs.settings.events
        opening = new_run()
        self._runs.add(opening)
        with self.machine._run(
            id=opening,
            cancel=cancel,
            spend=spend,
            max_cost=max_cost,
            root=self.root,
            events_dir=self._inputs.workspace.events_dir,
            keep_runs=files.keep_runs,
            max_bytes=files.max_bytes,
            threshold=self.threshold,
        ) as run:
            # What the load noticed, such as a misspelled key, is said on every run of the project,
            # because the project was loaded once and each run's events file is read on its own.
            for note in self._inputs.notes:
                run.note(note, level=Level.WARNING)
            with self._lock(run) if writes else nullcontext():
                yield run

    @contextmanager
    def _lock(self, run: Run) -> Iterator[None]:
        """Hold this project's build directory for the length of one writing run.

        The trigger is not a service, it is a build run by hand under a live watch loop. The lock is
        the operating system's own, taken through `filelock` on the open file, so the system releases
        it the moment the holder dies however it dies, and two writers that race for it cannot both
        win. Who holds it is written to a note beside it, because Windows refuses a read of a locked
        file. A refusal reports that note, a crashed holder leaves it behind, and a run that finds it
        under a lock nobody holds says so rather than refusing, because a caller cannot clear a file
        it was never told about.
        """
        path = self._inputs.workspace.lock_path
        note = self._inputs.workspace.owner_path
        try:
            held = FileLock(path, blocking=False).acquire()
        except Timeout:
            owner = _read_owner(note)
            whose = f" (process {owner[0]}, run {owner[1]})" if owner else ""
            raise ProjectLocked(
                f"another writer holds this build directory{whose}.",
                hint="Wait for that run to finish, or stop it and run this again.",
                location=at(path, self.root),
            ) from None
        except OSError as unusable:
            # filelock opens the file without following a link, so a link planted inside the build,
            # which confinement lets through because it stays inside, is refused here, as is a
            # directory under the lock's name.
            raise InputError(
                f"{path.name} cannot be used as the build lock ({unusable.strerror or unusable}).",
                hint=f"Delete {path.name} from the build directory and run again.",
                location=at(path, self.root),
            ) from unusable
        with held:
            if _read_owner(note) is not None:
                gone = f"A run that is no longer there left {note.name} behind, so this run took it."
                run.note(gone, level=Level.WARNING)
            # The note is replaced whole, so a reader never sees half a line.
            replace_all({note: f"{os.getpid()} {run.id}\n"})
            # A host that sees two jobs collide learns the winner's side from this line.
            log.debug("This run holds the build directory.", extra={"data": {"pid": os.getpid(), "lock": path.name}})
            try:
                yield
            finally:
                note.unlink(missing_ok=True)


def _read_owner(note: Path) -> tuple[int, str] | None:
    """The process and the run the note names, or None when there is no note or it names nobody.

    A link is never followed, so a note a project shipped cannot quote a file from elsewhere.
    """
    try:
        text = "" if note.is_symlink() else note.read_text(encoding="utf-8")
    except OSError:
        # silent: an owner note that cannot be read names no owner.
        return None
    pid, _, run = text.strip().partition(" ")
    try:
        return int(pid), run
    except ValueError:
        # silent: an owner note that does not parse names no owner.
        return None


__all__ = ["Origin", "Project", "open", "section_numbers"]
