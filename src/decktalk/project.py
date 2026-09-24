"""A project is a directory, one object opens it, and every call on it opens a run.

    my-lesson/
      decktalk.toml      what this presentation is, and every knob turned for it
      script.md          the narration, in "## N. Title" sections
      cues.json          which spoken phrase each moment lands on
      deck/index.html    the slides, which the runtime gives its query contract
      media/             the author's own clips, beds and markers
      .env               the speech credential, which is never committed
      build/             everything generated

`decktalk.open(path)` returns a `Project`. Six verbs move it forward, six more calls report on it or
cut a piece out of it, `serve` puts it on a local origin and `apply` carries out a fix. Every one of
them opens a run on the machine's event stream, takes a cancel token, and returns a frozen result
whose findings carry a code, a place, a certainty and often a fix.

This module is the facade and nothing below it may import it. It is also the only module that
reaches down into the stages, and it does so by name when a call is made rather than by an import at
the top, because a stage opens a browser and an encoder and importing the command line must load
neither. `Inputs` is what a stage is handed, so a stage never sees a project, a machine or a run
opener and can neither read the environment nor print.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from http.server import ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from decktalk.errors import Cancel, InputError, ProjectLocked
from decktalk.events import Event, Events, Level, Subscription
from decktalk.findings import Finding
from decktalk.inputs import Document, Inputs, Workspace
from decktalk.inputs.paths import at, relative
from decktalk.machine import Machine, Run, apply_fix, fixes_of, new_run
from decktalk.pipeline import Stage
from decktalk.results import (
    ApplyResult,
    AssembleResult,
    BuildResult,
    CheckResult,
    ClipResult,
    CueResult,
    NarrateResult,
    RecordResult,
    Result,
    Scope,
    ServeResult,
    SoundscapeResult,
    StatusResult,
    StoryboardResult,
    VerifyResult,
    Voicing,
    WordsResult,
)
from decktalk.settings import Layers, Settings

STAGES = "decktalk.stages"
"""The package every stage lives in, named rather than imported so the facade loads none of them.

This is the one edge from the facade down into the stages. A stage opens a browser and an encoder,
and the command line imports this module, so the module a call needs is loaded by that call.
"""

PROJECT_VARIABLE = "DECKTALK_PROJECT"
"""The variable that names the project when a caller names none, read only through the machine."""

LOCK_FILE = ".lock"
"""What the file a writer holds is called, under the build directory it is writing into."""

SECTION_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")
"""One item of a section selection, which is a number or two numbers with a dash between them."""


def open(
    path: str | Path | None = None,
    *,
    machine: Machine | None = None,
    overrides: Iterable[str] = (),
) -> Project:
    """The project in `path`, in `DECKTALK_PROJECT`, or in the directory the process started in.

    The machine is made once when a caller passes none, so a script that opens two projects should
    make one itself and hand it to both, which is what keeps the toolchain and the stream shared.
    """
    pairs = tuple(overrides)
    here = machine or Machine.from_environment(overrides=_split(pairs))
    named = path if path is not None else here.environ.get(PROJECT_VARIABLE)
    root = Path(named).expanduser() if named else here.cwd
    root = root if root.is_absolute() else here.cwd / root
    if root.is_file():
        root = root.parent
    return Project(here, root, overrides=pairs or here.overrides)


def _split(overrides: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Each `table.key=value` override as its two halves, which is how a machine takes them."""
    return tuple((pair.partition("=")[0], pair.partition("=")[2]) for pair in overrides)


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
        found.extend(range(first, last + 1))
    return tuple(dict.fromkeys(found))


def stage_call(name: str) -> Callable[..., Any]:
    """The one function a stage publishes, which is the module-level function named after the stage.

    Every stage satisfies the same convention: `decktalk.stages.<name>` holds `<name>(inputs, run,
    **options)`, which returns the result model named after it. That is the whole seam between the
    facade and the stages, so a test fakes a stage by replacing one attribute.
    """
    return getattr(import_module(f"{STAGES}.{name}"), name)


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

    def __init__(self, server: ThreadingHTTPServer, result: ServeResult, run: Run) -> None:
        self._server = server
        self._stopped = threading.Event()
        self.result = result
        self.run = run

    @property
    def url(self) -> str:
        """Where the deck is served, which is what an author opens and a recorder drives."""
        return self.result.url

    @property
    def port(self) -> int:
        """The port the origin listens on, which a caller reads when it asked for any free one."""
        return self.result.port

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

    Every call opens a run, takes `cancel`, and returns the frozen result named after it. Nothing
    here prints, nothing reads the environment, and every path a result carries is relative to
    `root`, so two projects in one process share nothing but the machine they were opened on.
    """

    root: Path
    inputs: Inputs
    machine: Machine
    events: Events

    def __init__(self, machine: Machine, root: Path, *, overrides: tuple[str, ...] = ()) -> None:
        self.machine = machine
        self.root = root.resolve()
        self.overrides = overrides
        self.inputs = Inputs.load(self.root, environ=machine.environ, machine=dict(machine.tables), overrides=overrides)
        self._runs: set[str] = set()
        self.events = ProjectEvents(machine, self._runs)

    def __repr__(self) -> str:
        return f"Project({self.root.as_posix()!r})"

    # ---- what the project is -------------------------------------------------------------

    @property
    def document(self) -> Document:
        """The parsed `decktalk.toml`, which says what this presentation is."""
        return self.inputs.document

    @property
    def workspace(self) -> Workspace:
        """Every path under the build directory, named once."""
        return self.inputs.workspace

    @property
    def settings(self) -> Settings:
        """Every knob in force for this project on this machine, with each layer already applied."""
        return self.inputs.settings

    @property
    def layers(self) -> Layers:
        """What every layer said about every key, which is what a knob's provenance is read from.

        It is resolved at `open()` and again at `reload()`, so a watch loop that sees an edited
        project file sees the layer that set each key move with it.
        """
        return self.inputs.layers

    def reload(self) -> Project:
        """This project read again from disk, which is what a watch loop calls when a file changed."""
        return Project(self.machine, self.root, overrides=self.overrides)

    def sections_touching(self, path: Path) -> tuple[int, ...]:
        """Every section a change to this file would change, in section order."""
        return self.inputs.sections_touching(path)

    # ---- the six verbs, in run order --------------------------------------------------------

    def narrate(
        self,
        *,
        only: Sequence[int] | None = None,
        voice: Voicing = Voicing.PLACEHOLDER,
        max_cost: float | None = None,
        force: bool = False,
        replace_voiced: bool = False,
        cancel: Cancel | None = None,
    ) -> NarrateResult:
        """Speak each section of the script and time every word in it."""
        return self._call(Stage.NARRATE, NarrateResult, cancel=cancel, voice=voice, max_cost=max_cost,
                          only=only, force=force, replace_voiced=replace_voiced)  # fmt: skip

    def cue(
        self,
        *,
        only: Sequence[int] | None = None,
        allow_unknown: bool = False,
        cancel: Cancel | None = None,
    ) -> CueResult:
        """Turn each cue phrase into a second on its own section's clock."""
        return self._call(Stage.CUE, CueResult, cancel=cancel, only=only, allow_unknown=allow_unknown)

    def record(
        self,
        *,
        only: Sequence[int] | None = None,
        force: bool = False,
        cancel: Cancel | None = None,
    ) -> RecordResult:
        """Record each page section in a headless browser, against the seconds the cues named."""
        return self._call(Stage.RECORD, RecordResult, cancel=cancel, only=only, force=force)

    def soundscape(
        self,
        *,
        only: Sequence[int] | None = None,
        voice: Voicing = Voicing.PLACEHOLDER,
        max_cost: float | None = None,
        force: bool = False,
        cancel: Cancel | None = None,
    ) -> SoundscapeResult:
        """Generate the music, the ambience bed and the effects this project describes."""
        return self._call(Stage.SOUNDSCAPE, SoundscapeResult, cancel=cancel, voice=voice,
                          max_cost=max_cost, only=only, force=force)  # fmt: skip

    def assemble(
        self,
        *,
        only: Sequence[int] | None = None,
        soundscape: bool = True,
        loudness: bool = True,
        strict: bool = False,
        cancel: Cancel | None = None,
    ) -> AssembleResult:
        """Cut, mix and encode the sections into one film."""
        return self._call(Stage.ASSEMBLE, AssembleResult, cancel=cancel, only=only, soundscape=soundscape,
                          loudness=loudness, strict=strict)  # fmt: skip

    def verify(self, *, only: Sequence[int] | None = None, cancel: Cancel | None = None) -> VerifyResult:
        """Measure the finished film: every start, every cut, every seam and every landing."""
        return self._call(Stage.VERIFY, VerifyResult, cancel=cancel, only=only)

    # ---- the whole run ----------------------------------------------------------------------

    def build(
        self,
        *,
        stages: Sequence[Stage] | None = None,
        skip: Sequence[Stage] = (),
        only: Sequence[int] | None = None,
        voice: Voicing = Voicing.PLACEHOLDER,
        max_cost: float | None = None,
        force: bool = False,
        replace_voiced: bool = False,
        soundscape: bool = True,
        loudness: bool = True,
        strict: bool = False,
        allow_unknown: bool = False,
        cancel: Cancel | None = None,
    ) -> BuildResult:
        """Run every stage in order, or the span of them `stages` names."""
        return self._call("build", BuildResult, cancel=cancel, voice=voice, max_cost=max_cost, stages=stages, skip=skip,
                          only=only, force=force, replace_voiced=replace_voiced, soundscape=soundscape,
                          loudness=loudness, strict=strict, allow_unknown=allow_unknown)  # fmt: skip

    # ---- the six that report or cut ---------------------------------------------------------

    def status(self, *, cancel: Cancel | None = None) -> StatusResult:
        """Report what is written, what is built, what is stale, and what to do next."""
        return self._call("status", StatusResult, cancel=cancel, writes=False)

    def check(
        self,
        *paths: Path,
        only: Sequence[int] | None = None,
        pages: bool = True,
        frames: bool = True,
        cancel: Cancel | None = None,
    ) -> CheckResult:
        """Judge the script, the cue file and the pages before a build, and price what a build costs.

        `pages` set to false judges the written files with no browser at all and says which
        judgements it could not reach, so a new deck gets its first cue rows without a download and
        a hook that has no browser can still run. `frames` set to false keeps the browser and drops
        the freeze comparison.

        A check that opens pages freezes frames into the build directory and draws the storyboard
        over them, so it holds the build lock for as long as a stage that produces would. A check
        with no pages reads and judges alone, so it takes nothing and runs beside a build.
        """
        return self._call("check", CheckResult, cancel=cancel, writes=pages, paths=tuple(paths), only=only,
                          pages=pages, frames=frames)  # fmt: skip

    def words(self, *, only: Sequence[int] | None = None, cancel: Cancel | None = None) -> WordsResult:
        """Every spoken word with its start and its end, which is how a cue phrase is written."""
        return self._call("words", WordsResult, cancel=cancel, writes=False, only=only)

    def storyboard(self, *, only: Sequence[int] | None = None, cancel: Cancel | None = None) -> StoryboardResult:
        """Freeze every slide at every cue onto one page, which is the checkpoint before credits are spent."""
        return self._call("storyboard", StoryboardResult, cancel=cancel, only=only)

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
        """Cut a span of one built section into its own file."""
        return self._call("clip", ClipResult, cancel=cancel, section=section, start=start, end=end, out=out,
                          gain_db=gain_db, hold_seconds=hold_seconds)  # fmt: skip

    def apply(self, fix: Finding | Iterable[Finding], *, unsafe: bool = False) -> ApplyResult:
        """Carry out the fixes a set of findings offer, and say what each one did.

        A safe fix cannot lose the author's work and is applied. An unsafe fix can, so it is applied
        only when the caller asked for that. A fix only a person can make is reported and never
        applied.
        """
        with self._open(writes=True) as run:
            outcomes = tuple(
                apply_fix(run, code, found, root=self.root, scope=Scope.PROJECT, unsafe=unsafe)
                for code, found in fixes_of(fix)
            )
            return run.result(ApplyResult, fixes=outcomes)

    # ---- the local origin ---------------------------------------------------------------------

    def serve(self, *, host: str = "127.0.0.1", port: int = 0) -> Origin:
        """Serve this project's deck on a local origin, and hand back the origin rather than a result.

        The origin answers only for the deck directory and the files the document declares, so a
        page the recorder drives and a preview an author leaves running reach the same short list
        and neither can read the credential beside it. It also answers one alias, which is where a
        previewed page reads its resolved cues, since a preview has no recorder to put them in its
        URL and the build directory is not served.
        """
        # The server is the media layer's, which carries the routing every recorded page also uses.
        from decktalk.media.origin import Allowed, bound_host, open_server  # noqa: PLC0415

        with self._open(writes=False) as run:
            server = open_server(Allowed.of(self.root, self.inputs.served_paths()), host, port)
            address = f"{bound_host(server)}:{server.server_address[1]}"
            result = run.result(
                ServeResult,
                url=f"http://{address}",
                port=int(server.server_address[1]),
                root=relative(self.root, self.root),
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            return Origin(server, result, run)

    # ---- how every call is made -----------------------------------------------------------------

    def _call[R: Result](
        self,
        stage: Stage | str,
        model: type[R],
        *,
        cancel: Cancel | None = None,
        voice: Voicing = Voicing.PLACEHOLDER,
        max_cost: float | None = None,
        writes: bool = True,
        **options: object,
    ) -> R:
        """Open a run, hold the build directory when the call writes, and hand the stage its inputs.

        `model` is the result this command answers with, which is named after the command itself, so
        the one table that says which callable implements which command is the twelve calls above.
        """
        name = stage.value if isinstance(stage, Stage) else stage
        with self._open(cancel=cancel, voice=voice, max_cost=max_cost, writes=writes) as run:
            answered = stage_call(name)(self.inputs, run, **options)
        if not isinstance(answered, model):
            raise TypeError(f"{name} answered with {type(answered).__name__} rather than {model.__name__}")
        return cast("R", answered)

    @contextmanager
    def _open(
        self,
        *,
        cancel: Cancel | None = None,
        voice: Voicing = Voicing.PLACEHOLDER,
        max_cost: float | None = None,
        writes: bool = True,
    ) -> Iterator[Run]:
        """One run of this project, with its lines beside the build and its lock held while it writes."""
        keep = self.inputs.settings.output.events_keep_runs
        opening = new_run()
        self._runs.add(opening)
        with self.machine.run(
            id=opening,
            cancel=cancel,
            voice=voice,
            max_cost=max_cost,
            root=self.root,
            events_dir=self.workspace.events_dir,
            keep_runs=keep,
        ) as run:
            with self._lock(run) if writes else nullcontext():
                yield run

    @contextmanager
    def _lock(self, run: Run) -> Iterator[None]:
        """Hold this project's build directory for the length of one writing run.

        The trigger is not a service, it is a build run by hand under a live watch loop. A lock left
        by a process that is gone is broken and reported rather than refused, because a caller
        cannot clear a file it was never told about.
        """
        path = self.workspace.build / LOCK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        held = _read_lock(path)
        if held is not None and not _alive(held[0]):
            gone = f"A run that is no longer there left {LOCK_FILE} behind, so this run took it."
            run.note(gone, level=Level.WARNING)
            path.unlink(missing_ok=True)
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as clash:
            owner = _read_lock(path)
            whose = f" (process {owner[0]}, run {owner[1]})" if owner else ""
            raise ProjectLocked(
                f"another writer holds this build directory{whose}.",
                hint="Wait for that run to finish, or stop it and run this again.",
                location=at(path, self.root),
            ) from clash
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as sink:
                sink.write(f"{os.getpid()} {run.id}\n")
            yield
        finally:
            path.unlink(missing_ok=True)


def _read_lock(path: Path) -> tuple[int, str] | None:
    """Who holds the lock, as a process and a run, or None when nobody does or the file says nothing."""
    try:
        pid, _, run = path.read_text(encoding="utf-8").strip().partition(" ")
        return int(pid), run
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> bool:
    """Whether a process is still there, asked without touching it."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


__all__ = ["Origin", "Project", "open", "section_numbers"]
