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

Every call on a machine opens a run, and a run is what a stage reports through.

    __init__.py   the machine, its toolchain, `install`, `doctor` and `init`
    run.py        one call in progress, its spend gate and the threshold it carries
    fixes.py      the fix applier `apply` on a machine or a project carries a fix out with
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from importlib import import_module
from pathlib import Path
from typing import Any

from decktalk.errors import STOPS, Cancel, ErrorInfo, InputError, ToolError
from decktalk.events import (
    Events,
    JsonlSink,
    Level,
    RunDone,
    RunStart,
)
from decktalk.findings import (
    ERRORS_FAIL,
    Applicability,
    Code,
    CommandFix,
    Finding,
    Location,
    Threshold,
    judge,
)
from decktalk.inputs.env import reading_dotenv
from decktalk.inputs.paths import relative
from decktalk.inputs.workspace import EVENTS_SUFFIX
from decktalk.logs import logging_into
from decktalk.machine.fixes import apply_fixes
from decktalk.machine.run import Run, new_run
from decktalk.media.environment import child_environment, children_see
from decktalk.media.ffmpeg import installed_paths, using_tools
from decktalk.pipeline import Outcome
from decktalk.results import (
    ApiKeyState,
    ApplyResult,
    DoctorResult,
    InitResult,
    InstalledTool,
    InstallResult,
    Scope,
)
from decktalk.secret import register_environment
from decktalk.settings import BY_ID, MACHINE_FILE_VARIABLE, NarrationConfig, ToolsConfig
from decktalk.settings import Scope as SettingScope
from decktalk.settings.layers import (
    env_warnings,
    key_warnings,
    load,
    machine_config_path,
    machine_folder,
    read_machine_toml,
    route,
    scoped,
)
from decktalk.speech import PROVIDERS, SpeechFactory, SpeechProviders, VoiceInForce
from decktalk.speech.sound import SOUNDS, SoundFactory, SoundProviders
from decktalk.toolchain import assets, chromium_fetch
from decktalk.toolchain.announce import announcing
from decktalk.toolchain.cache import caching_in, standard_cache_dir, standard_data_dir
from decktalk.toolchain.ffmpeg_fetch import FFMPEG_VERSION, fetch_ffmpeg

log = logging.getLogger(__name__)

CHROMIUM = "chromium"
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
KATEX = "katex"


STORE_FOLDER = "takes"
"""The take store's folder inside DeckTalk's per-user data folder."""


def _refuse_store_in_cache(store: Path | None, cache: Path) -> None:
    """Refuse a take store inside the tool cache, which anyone may empty while the store holds voiced takes."""
    if store is not None and store.resolve().is_relative_to(cache.resolve()):
        raise InputError(
            f"[narration] store_dir is {store}, which is inside the tool cache at {cache}, and anyone may empty "
            "that while the take store holds voiced takes.",
            hint="Name a folder outside the tool cache, such as ~/decktalk-takes, or leave the key unset.",
        )


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
        """The toolchain these keys resolve to, resolved once and without fetching anything.

        `[tools]` naming half a build or a file that is not there is a `TOOL` refusal.
        """
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

    def fetch(self, *, cancel: Cancel | None = None) -> Toolchain:
        """This toolchain with the pinned build downloaded when it was not already there.

        `cancel` is the run's token, which a fetch waiting on another fetch of the same build polls. A
        download the network refuses is a `TOOL` refusal, never a bug in DeckTalk.
        """
        if self.complete:
            return self
        with self.bound(cancel=cancel):
            try:
                ffmpeg, ffprobe = fetch_ffmpeg(cancel=cancel, wait_seconds=self.tools.timeout_seconds)
            except OSError as exc:
                raise ToolError(
                    f"the pinned ffmpeg could not be downloaded ({exc}).",
                    hint="Run `decktalk install` again with network access, or install ffmpeg and ffprobe on PATH.",
                ) from exc
        return replace(self, ffmpeg=Path(ffmpeg), ffprobe=Path(ffprobe))


@dataclass(frozen=True)
class Machine:
    """Everything about this computer and this process, as one value nothing else reaches for."""

    environ: Mapping[str, str] = field(repr=False)
    tables: Mapping[str, Any] = field(repr=False)
    machine_file: Path
    cwd: Path
    toolchain: Toolchain
    events: Events = field(default_factory=Events, compare=False)
    overrides: tuple[str, ...] = ()
    speech_providers: SpeechProviders = field(
        default_factory=lambda: _speech(None, NarrationConfig().retries), repr=False, compare=False
    )
    """The voices this machine answers with by name, and how often each asks a busy provider again.

    `Machine.of` resolves it from the table a host handed in, or the closed set DeckTalk ships, at the
    retries this machine's own layers name. A host's table is code the host wrote and imported itself,
    and it replaces the shipped one rather than sitting over it, so a machine built with a fake voice
    can reach no real one by a name the host left out.
    """
    sound_providers: SoundProviders = field(
        default_factory=lambda: _sounds(None, None, NarrationConfig().retries), repr=False, compare=False
    )
    """The sound providers this machine answers with by name, and how often each asks again.

    A machine whose host gave neither table answers with the sound providers DeckTalk ships. A host
    that gave a voice table and no sound table gets none, so a machine built with a fake voice can
    reach no real sound provider either.
    """
    dotenv: bool = True
    """Whether a project's `.env` is read, which is true for the author's own machine and false for a host's."""
    notes: tuple[str, ...] = field(default=(), compare=False)
    """What reading the machine noticed, such as a misspelled variable or key, which every run says."""
    store: Path | None = None
    """The standard per-user folder this machine keeps its take store in, when `[narration] store_dir` names none.

    It is None for a machine a host built without one, so a host that runs other people's projects
    keeps no store across them unless it says so.
    """

    @classmethod
    def from_environment(cls, *, overrides: Iterable[str] = ()) -> Machine:
        """This machine as the process found it, which is the only reading of the environment there is."""
        environ = dict(os.environ)
        home = Path.home()
        return cls.of(
            environ=environ,
            machine_file=machine_config_path(environ, home),
            cwd=Path.cwd(),
            cache_dir=standard_cache_dir(environ, home),
            store_dir=standard_data_dir(environ, home) / STORE_FOLDER,
            overrides=overrides,
            dotenv=True,
        )

    @classmethod
    def of(
        cls,
        *,
        environ: Mapping[str, str],
        machine_file: Path,
        cwd: Path,
        cache_dir: Path,
        store_dir: Path | None = None,
        speech_providers: Mapping[str, SpeechFactory] | None = None,
        sound_providers: Mapping[str, SoundFactory] | None = None,
        overrides: Iterable[str] = (),
        dotenv: bool = False,
    ) -> Machine:
        """A machine built from values its caller chose, which is how a host runs other people's projects.

        Nothing here reads the process. `environ` is every variable a run on this machine may see,
        which for a render job is none and for a voice job is the key and the voice id. The machine
        file at `machine_file` is read when it is there and is where a machine-scope fix lands.
        `speech_providers` replaces the shipped voices by name, and `sound_providers` the shipped sound
        providers. A project's `.env` is left unread unless `dotenv` says otherwise, because it is what
        a tenant's upload would reach for, and every base URL a request goes to is a machine-scoped key,
        so no project can send the key or the script anywhere this machine did not name.
        """
        register_environment(environ)
        tables = read_machine_toml(machine_file)
        pairs = tuple(overrides)
        mine = _machine_overrides(pairs)
        loaded = load(project={}, machine=tables, machine_path=machine_file, environ=environ, overrides=mine)
        # Both machine folders are read here, where the machine file is known, so a relative one is
        # refused naming it, and the tools are handed the cache with its `~` already expanded.
        store = machine_folder(loaded, "narration.store_dir", environ) or store_dir
        named = machine_folder(loaded, "tools.cache_dir", environ)
        tools = loaded.settings.tools if named is None else replace(loaded.settings.tools, cache_dir=str(named))
        # The retries are a machine-scoped key, so the machine's own file, environment and overrides
        # decide them whole and no project can.
        retries = loaded.settings.narration.retries
        _refuse_store_in_cache(store, named or cache_dir)
        # A machine whose `[tools]` names no usable pair is still a machine: every run says why, so
        # `doctor` reports the key to mend rather than a missing encoder `install` could not fix.
        try:
            toolchain, refused = Toolchain.of(tools, cache=cache_dir), ()
        except ToolError as refusal:
            toolchain, refused = Toolchain(tools=tools, cache=cache_dir), (f"{refusal} {refusal.hint}",)
        return cls(
            environ=dict(environ),
            tables=tables,
            machine_file=machine_file,
            cwd=cwd,
            toolchain=toolchain,
            overrides=pairs,
            speech_providers=_speech(speech_providers, retries),
            sound_providers=_sounds(sound_providers, speech_providers, retries),
            dotenv=dotenv,
            notes=(*env_warnings(environ), *key_warnings(tables, machine_file.name), *refused),
            store=store_dir,
        )

    @property
    def cache_dir(self) -> Path:
        """Where this machine keeps the browser and the encoder it fetches."""
        return self.toolchain.cache_dir

    def child_environ(self) -> dict[str, str]:
        """The environment a DeckTalk command this machine starts runs with, so the command acts on this machine.

        A host's machine names its own file and cache rather than the ones its variables would, so the
        two are spelled into the child's environment, and a child `decktalk install` fetches into the
        cache this machine reads and writes to the file this machine holds.
        """
        child = {**self.environ, MACHINE_FILE_VARIABLE: str(self.machine_file)}
        if self.toolchain.tools.cache_dir or self.toolchain.cache is not None:
            child[BY_ID["tools.cache_dir"].environment] = str(self.cache_dir)
        return child

    @property
    def api_key_state(self) -> ApiKeyState:
        """Whether the API key a voiced run would use is set, which is asked and never revealed.

        The variable is the one the voice `[voice] provider` names declares, and a voice that declares
        none needs none. A machine has no project, so the provider is the one its own layers name.
        """
        mine = _machine_overrides(self.overrides)
        settings = load(project={}, machine=self.tables, environ=self.environ, overrides=mine).settings
        # The machine's default voice, which no project names: doctor runs with no project open.
        default_voice = VoiceInForce.of(settings)
        variable = default_voice.key_variable
        if variable is None:
            return ApiKeyState.NOT_NEEDED
        return ApiKeyState.SET if self.environ.get(variable) else ApiKeyState.MISSING

    # ---- opening a run ------------------------------------------------------------------

    @contextmanager
    def _run(
        self,
        *,
        id: str | None = None,
        cancel: Cancel | None = None,
        spend: bool = False,
        max_cost: float | None = None,
        root: Path | None = None,
        events_dir: Path | None = None,
        keep_runs: int | None = None,
        max_bytes: int | None = None,
        threshold: Threshold = ERRORS_FAIL,
    ) -> Iterator[Run]:
        """Open one run on the stream, write its lines beside the project, and close it however it ends.

        A run with no project streams and writes no file, because there is nowhere under a project
        to write it and a machine command must still reach a renderer. A caller that must know the
        id before the first line is written mints it itself and passes it, which is how a project
        adds the run to its own view in time for a renderer to see the run open. `threshold` is the
        caller's rule for which findings fail the run, which every result the run fills is judged by.
        """
        run = Run(
            self,
            id=id or new_run(),
            cancel=cancel or Cancel(),
            spend=spend,
            max_cost=max_cost,
            root=root,
            threshold=threshold,
        )
        events_path = events_dir / f"{run.id}{EVENTS_SUFFIX}" if events_dir is not None else None
        sink, written, pruned = None, None, ()
        if events_path is not None:
            if keep_runs is not None:
                pruned = JsonlSink.prune(events_path.parent, keep_runs)
            written = JsonlSink(events_path, max_bytes=max_bytes)
            sink = self.events.subscribe(written, runs=[run.id])
        self.events.emit(run.id, RunStart, events_file=relative(events_path, root) if events_path and root else None)
        # What the machine noticed when it was read is said on every run, because the machine was read
        # once and each run's events file is read on its own.
        for note in self.notes:
            run.note(note, level=Level.WARNING)
        outcome, error = Outcome.RAN, None
        try:
            # The toolchain, the download listener, the rule about `.env` and the environment a launched
            # browser is built from are what this run renders, fetches, reads secrets and opens pages
            # with. All of them sit below the event stream, so the run binds them for its own length
            # rather than threading a machine through every filter, fetcher and browser launch. The
            # providers and whether the run may spend are not among them: the providers travel on the
            # machine and the spend on the run.
            with (
                self.toolchain.bound(cancel=run.cancel),
                children_see(self.environ),
                announcing(run.fetching),
                reading_dotenv(self.dotenv),
                logging_into(run.logged, run=run.id),
            ):
                if pruned:
                    removed = [path.name for path in pruned]
                    log.debug("Removed %d older events files.", len(removed), extra={"data": {"removed": removed}})
                yield run
        except BaseException as failure:
            # The reason goes on the run's last line as well as up the stack, because the file under
            # build/events is read after the process that raised it is gone.
            outcome = Outcome.STOPPED if isinstance(failure, STOPS) else Outcome.FAILED
            error = ErrorInfo.of_failure(failure)
            raise
        finally:
            dropped = written.dropped if written is not None else 0
            self.events.emit(
                run.id,
                RunDone,
                outcome=outcome,
                elapsed_seconds=time.monotonic() - run.opened,
                error=error,
                dropped=dropped,
            )
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
        with self._run(cancel=cancel) as run:
            # Whether the browser already launches is the one answer that decides the fetch, the
            # system libraries and the row's `fetched`: a browser that launches has its libraries, so
            # a second `install` fetches nothing and names what was already there.
            browser = self._browser_row()
            if browser.version is None:
                chromium_fetch.fetch_chromium(env=child_environment(), with_deps=installs_system_libraries())
                # The row is asked again the way `doctor` asks for it, by launching what was just
                # fetched, so `install` cannot print the browser as missing a second after it
                # downloaded one.
                browser = self._browser_row().model_copy(update={"fetched": True})
            held = self.toolchain.complete
            tools = (browser, *_encoder_rows(self.toolchain.fetch(cancel=run.cancel), fetched=not held))
            return run.result(InstallResult, tools=tools, cache=self.cache_dir)

    def doctor(
        self, *, measure: bool = False, threshold: Threshold = ERRORS_FAIL, cancel: Cancel | None = None
    ) -> DoctorResult:
        """Report what this machine holds and what a run on it would use, fetching nothing.

        The encoder row looks for executables already on disk, because asking for them would
        download the pinned build, and the browser row launches the one this machine already has.
        No variable's value is reported, because a variable may hold a credential. `threshold` is
        which of its findings make `ok` false, as it is for a project's calls.
        """
        with self._run(cancel=cancel, threshold=threshold) as run:
            tools = (self._browser_row(), *_encoder_rows(self.toolchain), self._katex_row())
            findings = tuple(run.found(found) for found in _missing_findings(tools))
            return run.result(
                DoctorResult,
                findings=findings,
                tools=tools,
                cache=self.cache_dir,
                python=f"{sys.version.split()[0]} ({sys.executable})",
                platform=f"{platform.platform()} ({sys.platform})",
                api_key_state=self.api_key_state,
                bias_ms=self._bias(measure=measure),
            )

    def apply(self, findings: Finding | Iterable[Finding], *, unsafe: bool = False) -> ApplyResult:
        """Carry out the fixes a machine can make, which is changing a machine setting and running a command."""
        with self._run(root=self.cwd) as run:
            return apply_fixes(run, findings, root=self.cwd, scope=Scope.MACHINE, unsafe=unsafe)

    # ---- the rows `doctor` reports ---------------------------------------------------------

    def _browser_row(self) -> InstalledTool:
        """What browser this machine can launch, asked by launching it rather than by looking for a file.

        The row also names where that browser lives, because a person told the browser is there
        still has to find it to clear a cache or to hand it to a container. The driver looks in the
        browser directory of this machine's tool cache, which is where `install` fetches it.
        """
        with chromium_fetch.driver(chromium_fetch.browsers_in(self.cache_dir)) as playwright:
            try:
                browser = playwright.chromium.launch()
                version = browser.version
                browser.close()
            except Exception as failed:  # noqa: BLE001  (a browser that will not launch is a row, never a traceback)
                # The row says only that there is no browser, so the reason goes on the run's stream.
                log.warning("Chromium did not launch.", extra={"data": {"reason": str(failed).splitlines()[0]}})
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

    @staticmethod
    def _bias(*, measure: bool) -> float | None:
        """This host's presentation bias, measured only when asked, because measuring drives a browser.

        It is reported and never kept, because no stage reads it: it is what an author reads a
        section's offsets against to tell a machine that presents late from a deck that cues late.
        """
        if not measure:
            return None
        # Measuring drives a browser, so the layer that owns the browser owns the measurement, and
        # the module is loaded by the one caller that asks for it rather than by every report.
        return float(import_module("decktalk.media.bias").measure_presentation_bias())


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
    overwrite: bool = False,
) -> InitResult:
    """Write a project that already builds into `path`, and say what was written.

    This is a machine call rather than a project one, because there is no project until it returns.
    """
    # The packaged projects sit in the wheel beside the skills, and a machine that never writes
    # one should not carry them, so they are loaded by the one call that does.
    from decktalk.template import STARTER, write_project  # noqa: PLC0415

    here = machine or Machine.from_environment()
    root = here.cwd / Path(path).expanduser()
    with here._run(root=root) as run:
        written = write_project(root, name=name or root.name, example_name=example, skills=skills, force=overwrite)
        for wrote in written:
            run.wrote(wrote)
        return run.result(
            InitResult,
            root=relative(root, here.cwd),
            name=name or root.name,
            example=example or STARTER,
            skills_written=skills,
        )


def _missing_findings(tools: tuple[InstalledTool, ...]) -> tuple[Finding, ...]:
    """One judgement per component a build needs and this machine has not got."""
    return tuple(
        judge(
            Code.FILE_MISSING,
            f"{tool.tool} is not on this machine, so a build that needs it cannot run.",
            Location(where=tool.tool),
            fix=CommandFix(
                title=f"Fetch {tool.tool} into the cache.",
                applicability=Applicability.SAFE,
                command=("decktalk", "install"),
            ),
        )
        for tool in tools
        if tool.version is None and tool.path is None
    )


def _speech(given: Mapping[str, SpeechFactory] | None, retries: int) -> SpeechProviders:
    """The voices a machine answers with: its host's table when it gave one, else the closed set DeckTalk ships."""
    return SpeechProviders(factories=PROVIDERS if given is None else given, retries=retries)


def _sounds(
    given: Mapping[str, SoundFactory] | None, voices: Mapping[str, SpeechFactory] | None, retries: int
) -> SoundProviders:
    """The sound providers a machine answers with, which are none when its host gave voices and no sounds."""
    if given is not None:
        return SoundProviders(factories=given, retries=retries)
    return SoundProviders(factories=SOUNDS if voices is None else {}, retries=retries)


def _machine_overrides(overrides: tuple[str, ...]) -> tuple[str, ...]:
    """The machine-scoped pairs of a run of `--set` overrides, spelled the way the loader takes them."""
    return tuple(f"{key}={value}" for key, value in scoped(route(overrides), SettingScope.MACHINE).items())


def installs_system_libraries() -> bool:
    """True on the platform where `install` also installs the browser's system libraries, through sudo.

    It is the one rule both sides read: `install` passes it to the fetch, and the command line asks
    for the password it costs before it starts.
    """
    return sys.platform.startswith("linux")


__all__ = ["Machine", "Run", "Toolchain", "init", "installs_system_libraries"]
