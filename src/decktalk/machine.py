"""This computer and this process, as one value, and the run every call opens on it.

Three things are ambient in a tool that reads the environment wherever it likes: which ffmpeg a
project uses, which speech provider answers to a name, and which directory is the project. All
three leak between two projects held in one process. `Machine.from_environment()` is the only place
in the package that reads `os.environ`, the per-machine settings file or the working directory, and
everything below it takes what it needs as an argument, so two projects in one process cannot reach
each other and a service can hold one machine per request.

A host that runs other people's projects builds its machine with `Machine.of` instead, from values
it chose: the environment a job may see, the per-machine file, the cache, and the voices it answers
with. Such a machine reads no project's `.env`, so a tenant's upload cannot supply a credential.

A `Run` is one call in progress: its id, the stream it writes to, the token that stops it and the
gate it passes before it spends anything. Every call on a machine and on a project opens one, which
is what makes the event stream total: `install` and `doctor` hold no project and would otherwise
leave a renderer silent through the two commands that download two hundred megabytes.

The spend gate lives here rather than on the command line, because a service refuses the same spend
for the same reason. No call buys anything without a paid voicing, and a ceiling is checked before
the first request rather than counted down as the credits go.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from decktalk.errors import ApprovalRequired, Cancel, DeckTalkError, InputError, ToolError
from decktalk.events import (
    Event,
    Events,
    Fetch,
    JsonlSink,
    Level,
    Log,
    Progress,
    RunDone,
    RunStart,
    SectionDone,
    SectionStart,
    StageDone,
    StageStart,
    Unit,
)
from decktalk.events import FindingEvent as FindingLine
from decktalk.events import SpendEvent as SpendLine
from decktalk.findings import (
    FIX_COMMANDS,
    Applicability,
    Certainty,
    Code,
    CommandFix,
    Edit,
    Finding,
    Location,
    SettingFix,
)
from decktalk.inputs.env import reading_dotenv
from decktalk.inputs.paths import at, relative
from decktalk.inputs.workspace import EVENTS_SUFFIX
from decktalk.media.environment import children_see
from decktalk.media.ffmpeg import installed_paths, using_tools
from decktalk.pipeline import Outcome, Stage
from decktalk.results import (
    ApplyResult,
    DoctorResult,
    FixOutcome,
    InitResult,
    InstalledTool,
    InstallResult,
    Layer,
    Result,
    Scope,
    Spend,
    Voicing,
)
from decktalk.settings import (
    ALLOW_ANY_API_BASE,
    BY_ID,
    CONFIG_VARIABLE,
    PROJECT_FILE,
    ToolsConfig,
    load,
    machine_config_path,
    read_machine_toml,
    route,
    scoped,
    write,
)
from decktalk.settings import Scope as SettingScope
from decktalk.speech import PROVIDERS, Voices, voicing
from decktalk.toolchain import assets, chromium_fetch
from decktalk.toolchain.announce import announcing
from decktalk.toolchain.cache import caching_in, standard_cache_dir
from decktalk.toolchain.ffmpeg_fetch import FFMPEG_VERSION, fetch_ffmpeg

if TYPE_CHECKING:  # pragma: no cover
    from decktalk.findings import Fix

VOICE_KEY = "ELEVENLABS_API_KEY"
"""The one credential a paid run needs, which `doctor` reports as set or not and never reads."""

RUN_DIGITS = 12
"""How much of a random id names a run, which is enough that two runs never share a file."""

CHROMIUM = "chromium"
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
KATEX = "katex"

FIX_TIMEOUT_SECONDS = 1800.0
"""The longest a command fix may run, which fetches a browser and an encoder in minutes and never in an hour."""

SWITCHED_OFF = frozenset(("", "0", "no", "false"))
"""The spellings of a switch variable that leave it off, so any other value turns it on."""

BIAS_KEY = "host.presentation_bias_ms"
"""The one key a command measures rather than a person chooses, which `doctor --measure` writes."""


def new_run() -> str:
    """A fresh run id, which names this run's events file and its rows in `status`."""
    return uuid.uuid4().hex[:RUN_DIGITS]


@dataclass(frozen=True)
class Toolchain:
    """What this machine renders with: the keys that name it, the pair they resolve to, and its cache.

    The pair is a field rather than a cached lookup, so the first project opened in a process cannot
    pin the toolchain for every project after it, which is what a module-level cache over the
    environment did. The keys travel with it because a run binds them for the length of the run, and
    every call between the machine and an audio filter reads them from there. The cache is the
    directory the machine worked out from its own environment, so a fetch lands where this machine
    keeps its tools rather than where the process that happens to run it would.
    """

    tools: ToolsConfig = field(default_factory=ToolsConfig)
    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    cache: Path | None = None
    """The standard per-user directory this machine keeps its tools in, when `[tools] cache_dir` names none."""

    @classmethod
    def of(cls, tools: ToolsConfig, *, cache: Path) -> Toolchain:
        """The toolchain these keys resolve to, resolved once and without fetching anything."""
        named = cls(tools=tools, cache=cache)
        with named.bound():
            found = installed_paths(tools)
        return replace(
            named,
            ffmpeg=Path(found[0]) if found else None,
            ffprobe=Path(found[1]) if found else None,
        )

    @property
    def cache_dir(self) -> Path:
        """Where this machine keeps what it fetches, which `[tools] cache_dir` moves."""
        if self.tools.cache_dir:
            return Path(self.tools.cache_dir)
        if self.cache is not None:
            return self.cache
        raise ToolError(
            "this machine names no directory to keep fetched tools in.",
            hint="Set tools.cache_dir on this machine, or build the machine with Machine.from_environment().",
        )

    @contextmanager
    def bound(self, *, cancel: Cancel | None = None) -> Iterator[None]:
        """Bind the keys, the pair, the cache directory and a run's cancel token below the machine.

        A toolchain built by hand for a test may name no cache at all, and it binds only its keys,
        so a run that fetches nothing never asks where a fetch would land. A pair this toolchain
        already found is handed down, so a run renders with it and resolves nothing a second time.
        `cancel` is the token every ffmpeg call polls while its tool works, which is how a cancelled
        run stops an encode rather than waiting for it.
        """
        known = self.tools.cache_dir or (str(self.cache) if self.cache is not None else "")
        paths = self.paths() if self.complete else None
        with caching_in(known), using_tools(self.tools, paths=paths, cancel=cancel):
            yield

    @property
    def complete(self) -> bool:
        """True when both executables are on this machine, which is what a build needs before it cuts."""
        return self.ffmpeg is not None and self.ffprobe is not None

    def paths(self) -> tuple[Path, Path]:
        """The pair, or a `TOOL` refusal naming the command that fetches it."""
        if self.ffmpeg is None or self.ffprobe is None:
            raise ToolError(
                "ffmpeg and ffprobe are not on this machine.",
                hint="Run `decktalk install`, which fetches the pinned build into the cache.",
            )
        return self.ffmpeg, self.ffprobe

    def fetched(self) -> Toolchain:
        """This toolchain with the pinned build downloaded when it was not already there."""
        if self.complete:
            return self
        with self.bound():
            ffmpeg, ffprobe = fetch_ffmpeg()
        return replace(self, ffmpeg=Path(ffmpeg), ffprobe=Path(ffprobe))


class Run:
    """One call in progress: its id, its stream, its cancel token and its spend gate.

    A stage is handed one of these and reports through it. It is the only thing a stage has that
    knows about the machine, so a stage can neither read the environment nor print.
    """

    def __init__(
        self,
        machine: Machine,
        *,
        id: str,
        cancel: Cancel,
        voice: Voicing = Voicing.PLACEHOLDER,
        max_cost: float | None = None,
        root: Path | None = None,
    ) -> None:
        self.id = id
        self.machine = machine
        self.cancel = cancel
        self.voice = voice
        self.max_cost = max_cost
        self.root = root
        self.written: list[Path] = []
        self.findings: list[Finding] = []

    # ---- the stream ---------------------------------------------------------------------

    def emit[E: Event](self, kind: type[E], **fields: object) -> E:
        """Put one line on the stream, with the four fields the library mints already on it."""
        return self.machine.events.emit(self.id, kind, **fields)

    def note(self, message: str, *, level: Level = Level.INFO) -> None:
        """One sentence the library would have printed, had the library printed anything."""
        self.emit(Log, level=level, message=message)

    def found(self, finding: Finding) -> Finding:
        """Record one judgement and report it as it was made, rather than holding it to the end."""
        self.findings.append(finding)
        self.emit(FindingLine, finding=finding)
        return finding

    def progress(
        self, stage: Stage, *, done: int, total: int, unit: Unit, label: str, section: int | None = None
    ) -> None:
        """How far through its own work a stage is, counted in the thing it is working on."""
        self.emit(Progress, stage=stage, section=section, done=done, total=total, unit=unit, label=label)

    def wrote(self, path: Path) -> Path:
        """Record one file this run wrote, which is what fills `written` without a stage listing it twice."""
        self.written.append(path)
        return path

    def check(self) -> None:
        """Raise `Cancelled` when the caller has asked the run to stop, which a stage calls between sections."""
        self.cancel.check()

    @contextmanager
    def stage(self, stage: Stage, *, index: int = 1, count: int = 1) -> Iterator[None]:
        """Open and close one stage on the stream, whatever the stage does inside."""
        started = _clock()
        self.emit(StageStart, stage=stage, index=index, count=count)
        try:
            yield
        except BaseException:
            self.emit(StageDone, stage=stage, outcome=Outcome.FAILED, seconds=_clock() - started)
            raise
        self.emit(StageDone, stage=stage, outcome=Outcome.OK, seconds=_clock() - started)

    @contextmanager
    def section(self, stage: Stage, section: int) -> Iterator[None]:
        """Open and close one section of one stage on the stream, and check the cancel token first."""
        self.check()
        started = _clock()
        self.emit(SectionStart, stage=stage, section=section)
        try:
            yield
        except BaseException:
            self.emit(SectionDone, stage=stage, section=section, outcome=Outcome.FAILED, seconds=_clock() - started)
            raise
        self.emit(SectionDone, stage=stage, section=section, outcome=Outcome.OK, seconds=_clock() - started)

    def fetching(self, tool: str, done_bytes: int, total_bytes: int | None = None) -> None:
        """A tool is arriving, which is the one moment a run stops for the network.

        The three arguments are the three fields of the line, so a fetcher reports and nothing in
        between translates. This is the listener the run binds for its own length.
        """
        self.emit(Fetch, tool=tool, bytes=done_bytes, total_bytes=total_bytes)

    # ---- the spend gate -----------------------------------------------------------------

    def approve(self, spend: Spend) -> Spend:
        """Let a priced request through, or refuse it before anything is bought.

        Every paid call passes through here, so no stage can spend without a paid voicing and no
        ceiling can be passed halfway. `--max-cost` is compared against the most the run can cost
        and never against the estimate, because credits are consumed one request at a time.
        """
        self.emit(SpendLine, spend=spend)
        if self.voice is not Voicing.PAID:
            raise ApprovalRequired(
                f"this run would spend ${spend.dollars:.2f} on speech and no voicing approved it.",
                hint="Pass --spend to approve it, or --no-voice to write placeholder narration.",
            )
        if self.max_cost is None:
            return spend
        if spend.price_layer is Layer.DEFAULT:
            raise ApprovalRequired(
                "--max-cost was given and nothing states what speech costs, so the cap would guard a made-up price.",
                hint="Set [voice] price_per_1000_characters to what your plan charges, then run it again.",
            )
        if spend.ceiling_dollars > self.max_cost:
            raise ApprovalRequired(
                f"this run can cost up to ${spend.ceiling_dollars:.2f}, which is over the "
                f"${self.max_cost:.2f} ceiling --max-cost set.",
                hint=f"Raise the ceiling to --max-cost {spend.ceiling_dollars:.2f}, or narrow the run with --section.",
            )
        return spend

    # ---- the result ---------------------------------------------------------------------

    def result[R: Result](
        self,
        model: type[R],
        *,
        findings: Iterable[Finding] | None = None,
        written: Iterable[Path] | None = None,
        **fields: object,
    ) -> R:
        """Fill one result: this run's id, the judgements it made and the files it wrote.

        `ok` is false when any judgement is certain, which is the one rule every command shares, so
        no stage decides for itself what counts as having found something. A stage that reported its
        judgements and its files through the run names neither here.
        """
        judged = tuple(findings) if findings is not None else tuple(self.findings)
        declared = model.model_fields
        if "run" in declared:
            fields.setdefault("run", self.id)
        if "written" in declared:
            paths = self.written if written is None else list(written)
            fields.setdefault("written", tuple(dict.fromkeys(self._relative(path) for path in paths)))
        fields.setdefault("ok", not any(found.certainty is Certainty.CERTAIN for found in judged))
        return model(findings=judged, **cast("dict[str, Any]", fields))

    def _relative(self, path: Path) -> Path:
        return relative(path, self.root) if self.root else path


@dataclass(frozen=True)
class Machine:
    """Everything about this computer and this process, as one value nothing else reaches for."""

    environ: Mapping[str, str] = field(repr=False)
    tables: Mapping[str, Any] = field(repr=False)
    config_path: Path
    cwd: Path
    toolchain: Toolchain
    events: Events = field(default_factory=Events, compare=False)
    overrides: tuple[str, ...] = ()
    providers: Mapping[str, Any] | None = field(default=None, repr=False, compare=False)
    """The voices this machine answers with by name, or None for the ones DeckTalk ships.

    A host's table replaces the shipped one rather than sitting over it, so a machine built with a
    fake voice can reach no real one by a name the host left out. Each value is a factory that takes
    a voice context and answers with a provider, and it is typed loosely on purpose, per the decision
    that no provider type is a published name.
    """
    dotenv: bool = True
    """Whether a project's `.env` is read, which is true for the author's own machine and false for a host's."""
    allow_any_api_base: bool = False
    """Whether `[elevenlabs] api_base` may name a host other than ElevenLabs, which only a machine decides.

    It is a field rather than a variable the speech layer reads, so a host that built its machine by
    hand decides it, and a key is never sent elsewhere because the process that runs a call happened
    to have the switch set.
    """

    @classmethod
    def from_environment(cls, *, overrides: Iterable[tuple[str, str]] = ()) -> Machine:
        """This machine as the process found it, which is the only reading of the environment there is."""
        environ = dict(os.environ)
        home = Path.home()
        return cls.of(
            environ=environ,
            config_path=machine_config_path(environ, home),
            cwd=Path.cwd(),
            cache_dir=standard_cache_dir(environ, home),
            overrides=overrides,
            dotenv=True,
            allow_any_api_base=environ.get(ALLOW_ANY_API_BASE, "").lower() not in SWITCHED_OFF,
        )

    @classmethod
    def of(
        cls,
        *,
        environ: Mapping[str, str],
        config_path: Path,
        cwd: Path,
        cache_dir: Path,
        providers: Mapping[str, Any] | None = None,
        overrides: Iterable[tuple[str, str]] = (),
        dotenv: bool = False,
        allow_any_api_base: bool = False,
    ) -> Machine:
        """A machine built from values its caller chose, which is how a host runs other people's projects.

        Nothing here reads the process. `environ` is every variable a run on this machine may see,
        which for a render job is none and for a voice job is the key and the voice id. The machine
        file at `config_path` is read when it is there and is where a machine-scope fix lands.
        `providers` replaces the shipped voices by name. A project's `.env` is left unread unless
        `dotenv` says otherwise, and the voice key goes to ElevenLabs and nowhere else unless
        `allow_any_api_base` says otherwise, because both are what a tenant's upload would reach for.
        """
        tables = read_machine_toml(config_path)
        pairs = tuple(f"{key}={value}" for key, value in overrides)
        mine = scoped(route(pairs), SettingScope.MACHINE)
        loaded = load(project={}, machine=tables, machine_path=config_path, environ=environ, overrides=_pairs(mine))
        return cls(
            environ=dict(environ),
            tables=tables,
            config_path=config_path,
            cwd=cwd,
            toolchain=Toolchain.of(loaded.settings.tools, cache=cache_dir),
            overrides=pairs,
            providers=providers,
            dotenv=dotenv,
            allow_any_api_base=allow_any_api_base,
        )

    @property
    def cache_dir(self) -> Path:
        """Where this machine keeps the browser and the encoder it fetches."""
        return self.toolchain.cache_dir

    @property
    def voices(self) -> Voices:
        """The voices this machine answers with, where its key may go and how often it asks again.

        The retries are a machine-scoped key, so the machine's own file, environment and overrides
        decide them whole and no project can.
        """
        mine = scoped(route(self.overrides), SettingScope.MACHINE)
        tuned = load(project={}, machine=self.tables, environ=self.environ, overrides=_pairs(mine)).settings
        return Voices(
            factories=PROVIDERS if self.providers is None else self.providers,
            allow_any_api_base=self.allow_any_api_base,
            retries=tuned.narration.retries,
        )

    def child_environ(self) -> dict[str, str]:
        """The environment a DeckTalk command this machine starts runs with, so the command acts on this machine.

        A host's machine names its own file and cache rather than the ones its variables would, so the
        two are spelled into the child's environment, and a child `decktalk install` fetches into the
        cache this machine reads and writes to the file this machine holds.
        """
        child = {**self.environ, CONFIG_VARIABLE: str(self.config_path)}
        if self.toolchain.tools.cache_dir or self.toolchain.cache is not None:
            child[BY_ID["tools.cache_dir"].environment] = str(self.cache_dir)
        return child

    @property
    def voice_key(self) -> bool:
        """True when the credential a paid run would use is set, which is asked and never revealed."""
        return bool(self.environ.get(VOICE_KEY))

    # ---- opening a run ------------------------------------------------------------------

    @contextmanager
    def run(
        self,
        *,
        id: str | None = None,
        cancel: Cancel | None = None,
        voice: Voicing = Voicing.PLACEHOLDER,
        max_cost: float | None = None,
        root: Path | None = None,
        events_dir: Path | None = None,
        keep_runs: int | None = None,
    ) -> Iterator[Run]:
        """Open one run on the stream, write its lines beside the project, and close it however it ends.

        A run with no project streams and writes no file, because there is nowhere under a project
        to write it and a machine command must still reach a renderer. A caller that must know the
        id before the first line is written mints it itself and passes it, which is how a project
        adds the run to its own view in time for a renderer to see the run open.
        """
        run = Run(self, id=id or new_run(), cancel=cancel or Cancel(), voice=voice, max_cost=max_cost, root=root)
        events_path = events_dir / f"{run.id}{EVENTS_SUFFIX}" if events_dir is not None else None
        sink = None
        if events_path is not None:
            if keep_runs is not None:
                JsonlSink.prune(events_path.parent, keep_runs)
            sink = self.events.subscribe(JsonlSink(events_path), runs=[run.id])
        started = _clock()
        self.events.emit(run.id, RunStart, events_path=relative(events_path, root) if events_path and root else None)
        outcome = Outcome.OK
        try:
            # The toolchain, the download listener, the voices, the rule about `.env` and the
            # environment a launched browser is built from are what this run renders, fetches, speaks,
            # reads secrets and opens pages with. All of them sit below the event stream, so the run
            # binds them for its own length rather than threading a machine through every filter,
            # fetcher, provider lookup and browser launch.
            with (
                self.toolchain.bound(cancel=run.cancel),
                children_see(self.environ),
                announcing(run.fetching),
                voicing(self.voices),
                reading_dotenv(self.dotenv),
            ):
                yield run
        except BaseException:
            outcome = Outcome.FAILED
            raise
        finally:
            self.events.emit(run.id, RunDone, outcome=outcome, seconds=_clock() - started)
            if sink is not None:
                sink.close()

    # ---- the three machine calls ----------------------------------------------------------

    def install(self, *, cancel: Cancel | None = None) -> InstallResult:
        """Fetch the browser and the encoder this machine needs, before a build asks for them.

        Nothing has to run this. A command that needs one fetches it. This is the way to do both up
        front instead: in a container layer, in a job that caches the download, on a machine that
        will be offline later, and on Linux, where it is also the one command that installs the
        browser's system libraries and so the one that may ask for a password.
        """
        with self.run(cancel=cancel) as run:
            chromium_fetch.fetch_chromium(with_deps=sys.platform.startswith("linux"))
            # The row is asked for the way `doctor` asks for it, by launching what was just fetched,
            # so `install` cannot print the browser as missing a second after it downloaded one.
            browser = self._browser_row().model_copy(update={"fetched": True})
            held = self.toolchain.complete
            tools = (browser, *_encoder_rows(self.toolchain.fetched(), fetched=not held))
            return run.result(InstallResult, tools=tools, cache=self.cache_dir)

    def doctor(self, *, measure: bool = False, cancel: Cancel | None = None) -> DoctorResult:
        """Report what this machine holds and what a run on it would use, fetching nothing.

        The encoder row looks for executables already on disk, because asking for them would
        download the pinned build, and the browser row launches the one this machine already has.
        No variable's value is reported, because a variable may hold a credential.
        """
        with self.run(cancel=cancel) as run:
            tools = (self._browser_row(), *_encoder_rows(self.toolchain), self._katex_row())
            findings = tuple(run.found(found) for found in _missing_findings(tools))
            return run.result(
                DoctorResult,
                findings=findings,
                tools=tools,
                cache=self.cache_dir,
                python=f"{sys.version.split()[0]} ({sys.executable})",
                platform=f"{platform.platform()} ({sys.platform})",
                voice_key=self.voice_key,
                bias_ms=self._bias(run, measure=measure),
            )

    def apply(self, fix: Finding | Iterable[Finding], *, unsafe: bool = False) -> ApplyResult:
        """Carry out the fixes a machine can make, which is turning a machine knob and running a command."""
        with self.run(root=self.cwd) as run:
            outcomes = tuple(
                apply_fix(run, code, found, root=self.cwd, scope=Scope.MACHINE, unsafe=unsafe)
                for code, found in fixes_of(fix)
            )
            return run.result(ApplyResult, fixes=outcomes)

    # ---- the rows `doctor` reports ---------------------------------------------------------

    def _browser_row(self) -> InstalledTool:
        """What browser this machine can launch, asked by launching it rather than by looking for a file.

        The row also names where that browser lives, because a person told the browser is there
        still has to find it to clear a cache or to hand it to a container.
        """
        try:
            # The browser driver is a heavy import and a machine without one is a row rather than a
            # refusal, so it is loaded by the one question that needs it.
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            return InstalledTool(tool=CHROMIUM)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
                version = browser.version
                browser.close()
            except Exception:  # noqa: BLE001  (a browser that will not launch is a row, never a traceback)
                return InstalledTool(tool=CHROMIUM)
            where = chromium_fetch.installed_chromium(playwright)
            path = Path(where) if where else None
            return InstalledTool(tool=CHROMIUM, version=version, path=path)

    def _katex_row(self) -> InstalledTool:
        """The maths the wheel carries, which a deck copies beside its pages."""
        missing = assets.katex_missing()
        if missing:
            return InstalledTool(tool=KATEX)
        return InstalledTool(tool=KATEX, version=assets.KATEX_VERSION, path=assets.katex_dir())

    def _bias(self, run: Run, *, measure: bool) -> float | None:
        """This host's presentation bias, measured only when asked, because measuring drives a browser.

        A measurement that is taken is kept. The key is refused to every hand that would type it,
        because a number typed into it is a guess subtracted from every later measurement, and this
        command is the one holding an honest value, so it writes through the door `settings.write`
        opens for exactly that and reports the file it wrote like any other.
        """
        if not measure:
            return None
        # Measuring drives a browser, so the layer that owns the browser owns the measurement, and
        # the module is loaded by the one caller that asks for it rather than by every report.
        measured = import_module("decktalk.media.browser").measure_presentation_bias
        bias = float(measured())
        write(self.config_path, BIAS_KEY, str(bias), scope=SettingScope.MACHINE, environ=self.environ, measured=True)
        run.wrote(self.config_path)
        return bias


def _encoder_rows(toolchain: Toolchain, *, fetched: bool = False) -> tuple[InstalledTool, ...]:
    """The encoder and the prober, which are one row each so a broken one names itself."""
    return tuple(
        InstalledTool(tool=name, version=FFMPEG_VERSION if path else None, path=path, fetched=fetched)
        for name, path in ((FFMPEG, toolchain.ffmpeg), (FFPROBE, toolchain.ffprobe))
    )


def init(
    path: Path | str,
    *,
    machine: Machine | None = None,
    name: str | None = None,
    example: str | None = None,
    skills: bool = True,
    force: bool = False,
) -> InitResult:
    """Write a project that already builds into `path`, and say what was written.

    This is a machine call rather than a project one, because there is no project until it returns.
    """
    # The packaged projects sit in the wheel beside the skills, and a machine that never writes
    # one should not carry them, so they are loaded by the one call that does.
    from decktalk.template import STARTER, write_project  # noqa: PLC0415

    here = machine or Machine.from_environment()
    root = Path(path).expanduser()
    root = root if root.is_absolute() else here.cwd / root
    with here.run(root=root) as run:
        written = write_project(root, name=name or root.name, example_name=example, skills=skills, force=force)
        for wrote in written:
            run.wrote(wrote)
        return run.result(
            InitResult,
            root=relative(root, here.cwd),
            name=name or root.name,
            example=example or STARTER,
            skills=skills,
        )


# ---- applying a fix ------------------------------------------------------------------------


def apply_fix(run: Run, code: Code, fix: Fix, *, root: Path, scope: Scope, unsafe: bool) -> FixOutcome:
    """Carry out one fix, or say in one sentence why it was left alone.

    A display fix is a change only a person can make, so it is reported and never applied, and an
    unsafe fix can lose the author's work, so it is applied only when the caller asked for that.
    """
    if fix.applicability is Applicability.DISPLAY:
        return FixOutcome(code=code, title=fix.title, applied=False, why="only a person can make this change.")
    if fix.applicability is Applicability.UNSAFE and not unsafe:
        why = "this fix can lose work, so it was applied only on request."
        return FixOutcome(code=code, title=fix.title, applied=False, why=why)
    try:
        files = _carry_out(run, fix, root=root, scope=scope)
    except DeckTalkError as refused:
        return FixOutcome(code=code, title=fix.title, applied=False, why=str(refused))
    for path in files:
        run.wrote(path)
    changed = tuple(relative(path, root) for path in files)
    return FixOutcome(code=code, title=fix.title, applied=True, files=changed)


def _carry_out(run: Run, fix: Fix, *, root: Path, scope: Scope) -> tuple[Path, ...]:
    """Make the change one fix describes, and give back every file it changed.

    Every edit is checked and made in memory before any file is touched, and every file is written
    under a temporary name before any of them replaces its target, so a fix whose second edit is
    refused has not already written its first and a fix that stops halfway leaves every file whole.
    """
    if isinstance(fix, SettingFix):
        return (_write_key(run, fix.key, fix.value, root=root, scope=scope),)
    if isinstance(fix, CommandFix):
        _run_command(run, fix, root=root)
        return ()
    targets = [(edit, _inside(root, edit.file)) for edit in fix.edits]
    staged: dict[Path, list[str]] = {}
    keys: list[tuple[str, str]] = []
    changed: list[Path] = []
    for edit, path in targets:
        if edit.key is not None:
            keys.append((edit.key, edit.new))
            changed.append(_write_key(run, edit.key, edit.new, root=root, scope=scope, dry_run=True))
            continue
        lines = staged[path] if path in staged else _lines_under(edit, path, root)
        staged[path] = _edited(edit, lines, path, root)
        changed.append(path)
    _replace_all({path: "".join(lines) for path, lines in staged.items()})
    for key, value in keys:
        _write_key(run, key, value, root=root, scope=scope)
    return tuple(dict.fromkeys(changed))


def _replace_all(texts: Mapping[Path, str]) -> None:
    """Write every text under a temporary name beside its file, then move each over its file.

    A failure while writing leaves every target as it was, and the moves that follow cannot be
    refused for anything a fix said, so the files change together. Each temporary file is created
    fresh, so a link a project planted under that name is refused rather than written through, and it
    takes the mode of the file it replaces, so a fix never changes who may read a file.
    """
    temporary = {path: path.with_name(f".{path.name}.{os.getpid()}.fixing") for path in texts}
    try:
        for path, text in texts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with temporary[path].open("x", encoding="utf-8") as handle:
                handle.write(text)
            if path.exists():
                shutil.copymode(path, temporary[path])
        for path, written in temporary.items():
            written.replace(path)
    finally:
        for written in temporary.values():
            written.unlink(missing_ok=True)


def _run_command(run: Run, fix: CommandFix, *, root: Path) -> None:
    """Run one of DeckTalk's own commands as this interpreter's DeckTalk, bounded in time.

    The set is checked again here, because a model can be built without validation and a fix is the
    one value that decides what this process launches. The command runs as `python -m decktalk`
    under the interpreter making the call rather than as whatever `decktalk` is first on `PATH`,
    which may be another install, and it sees the machine's own environment rather than the
    process's, so a host that built its machine by hand is the one that decides what it reads.
    """
    if fix.command not in FIX_COMMANDS:
        raise InputError(
            f"`{' '.join(fix.command)}` is not one of DeckTalk's own commands, so a fix may not run it.",
            hint="Run the command by hand if you mean it.",
        )
    argv = [sys.executable, "-m", *fix.command]
    try:
        finished = subprocess.run(
            argv,
            cwd=root,
            env=run.machine.child_environ(),
            check=False,
            capture_output=True,
            text=True,
            timeout=FIX_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as late:
        raise ToolError(
            f"`{' '.join(fix.command)}` ran for {FIX_TIMEOUT_SECONDS:.0f} seconds and was stopped.",
            hint=f"Run `{' '.join(fix.command)}` by hand to see where it waits.",
        ) from late
    if finished.returncode != 0:
        raise ToolError(
            f"{fix.command[0]} exited {finished.returncode}.",
            hint=f"Run `{' '.join(fix.command)}` by hand to see what it says.",
        )


def _inside(root: Path, named: Path) -> Path:
    """The file an edit names, refused unless it resolves inside the root once every link is followed.

    A fix can arrive as JSON from anywhere, so its path is the one part of it an attacker chooses.
    The check is made on the resolved path, because `..`, an absolute path and a symbolic link that
    points out of the project all spell a file inside it until they are resolved. The path handed
    back is spelled under the root the caller gave, so every result still names it relative to that.
    """
    home = root.resolve()
    resolved = (home / named).resolve()
    if not resolved.is_relative_to(home):
        raise InputError(
            f"{Path(named).as_posix()} is outside the project, so a fix may not change it.",
            hint="A fix only ever changes files inside the project it was made for.",
            location=Location(where=Path(named).as_posix()),
        )
    return root / resolved.relative_to(home)


def _write_key(run: Run, key: str, value: str, *, root: Path, scope: Scope, dry_run: bool = False) -> Path:
    """Set one settings key, or only check that it could be set, and name the file it lands in."""
    file = _settings_file(run, root, scope)
    return write(file, key, value, scope=scope, environ=run.machine.environ, dry_run=dry_run).file


def _edited(edit: Edit, lines: list[str], path: Path, root: Path) -> list[str]:
    """The lines of one file after one edit, addressed by the one locator the edit names."""
    if edit.pointer is not None:
        raise InputError(
            f"a pointer edit into {edit.file} has no applier yet.",
            hint="Make the change by hand, or run the command the finding names.",
            location=at(path, root),
        )
    index = (edit.line or 1) - 1
    if edit.old is not None:
        _still_reads(edit, lines, index, path, root)
    edited = list(lines)
    edited[index : index + (1 if edit.old is not None else 0)] = [edit.new + "\n"] if edit.new else []
    return edited


def _still_reads(edit: Edit, lines: list[str], index: int, path: Path, root: Path) -> None:
    """Refuse a replacement whose line no longer reads what the fix was made against.

    A fix is computed from the file as it was when the finding was raised. A file edited since then
    has moved its lines, and replacing line n by number would overwrite whatever line n holds now.
    """
    found = lines[index].rstrip("\r\n") if index < len(lines) else None
    if found != edit.old:
        raise InputError(
            f"line {edit.line} of {edit.file} no longer reads what this fix was made against, so it was left alone.",
            hint="Run the command that raised the finding again for a fix made against the file as it is now.",
            location=at(path, root),
        )


def _lines_under(edit: Edit, path: Path, root: Path) -> list[str]:
    """The lines one edit works on, which is an empty file when the edit is the one that writes it.

    A fix that writes a whole file states itself as an insert of the whole text at line one, as the
    cue file's does, so a file that is not there yet is that edit's starting point. Every other line
    edit needs the lines it names, and a caller who cannot be given them is told so in a sentence it
    can print rather than in an operating system error nothing above here would catch.
    """
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    if edit.old is None and (edit.line or 1) == 1:
        return []
    raise InputError(
        f"{edit.file} is not there, so it has no line {edit.line or 1} to change.",
        hint="Write the file first, or run the command the finding names.",
        location=at(path, root),
    )


def fixes_of(given: Finding | Iterable[Finding]) -> tuple[tuple[Code, Fix], ...]:
    """Every fix a caller handed over, each beside the code of the finding it resolves.

    A fix is taken from its finding rather than on its own, because what a fix resolves is the
    finding's own code and an outcome that could not name one would tell a caller nothing.
    """
    findings = (given,) if isinstance(given, Finding) else tuple(given)
    return tuple((found.code, found.fix) for found in findings if found.fix is not None)


def _missing_findings(tools: tuple[InstalledTool, ...]) -> tuple[Finding, ...]:
    """One judgement per component a build needs and this machine has not got."""
    return tuple(
        # The code owns the certainty and the docs page, so a raiser names the code and nothing else.
        Finding.model_validate(
            {
                "code": Code.FILE_MISSING,
                "message": f"{tool.tool} is not on this machine, so a build that needs it cannot run.",
                "location": Location(where=tool.tool),
                "fix": CommandFix(
                    title=f"Fetch {tool.tool} into the cache.",
                    applicability=Applicability.SAFE,
                    command=("decktalk", "install"),
                ),
            }
        )
        for tool in tools
        if tool.version is None and tool.path is None
    )


def _settings_file(run: Run, root: Path, scope: Scope) -> Path:
    """The file a settings write lands in, which is the project's own or the one this machine was built from.

    The machine's file is the one it holds, and never the one the process environment would name,
    so a fix applied through a machine a host built by hand lands in that host's file.
    """
    return root / PROJECT_FILE if scope is Scope.PROJECT else run.machine.config_path


def _pairs(overrides: Mapping[str, str]) -> tuple[str, ...]:
    """A routed override map spelled back the way the loader takes it, which is one string per pair."""
    return tuple(f"{key}={value}" for key, value in overrides.items())


def _clock() -> float:
    """A monotonic reading in seconds, which is what every elapsed number on the stream is taken from."""
    return time.monotonic()


__all__ = ["Machine", "Run", "Toolchain", "init"]
