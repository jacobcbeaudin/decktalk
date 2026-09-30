"""The bridge from standard logging to the run's stream, which never prints and never recurses."""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock
from pydantic import TypeAdapter
from pytest_httpserver import HTTPServer
from werkzeug import Response

import decktalk
from decktalk import logs
from decktalk.errors import ErrorCode, NotBuiltError, ProjectLocked, ProviderError
from decktalk.events import Event, Level, Line, Log, RunDone
from decktalk.findings import Applicability, Code, CommandFix
from decktalk.logs import HANDLER, LOGGER, RunHandler, install, level_of, logging_into, within
from decktalk.machine import Machine, Run, Toolchain, apply_fix
from decktalk.media import browser, ffmpeg, origin
from decktalk.media.environment import children_see
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Scope
from decktalk.secret import Secret
from decktalk.settings import ToolsConfig
from decktalk.speech import http as _http
from decktalk.stages.narrate import _in_pool
from decktalk.toolchain import chromium_fetch
from support.fakes import FakeChromium
from support.logs import data_of
from support.projects import write_project
from support.runs import a_machine

log = logging.getLogger("decktalk.media.ffmpeg")


def lines_of(seen: list[Event]) -> list[Log]:
    return [line for line in seen if isinstance(line, Log)]


def test_a_record_written_inside_a_run_becomes_a_line_of_that_run(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with (
        here.events.subscribe(seen.append),
        here.run() as run,
        run.stage(Stage.ASSEMBLE),
        run.section(Stage.ASSEMBLE, 3),
    ):
        log.debug("ffmpeg exited %d.", 0, extra={"data": {"exit": 0, "seconds": 0.5}})
    [line] = lines_of(seen)
    assert (line.run, line.level, line.message, line.source) == (
        run.id,
        Level.DEBUG,
        "ffmpeg exited 0.",
        "media.ffmpeg",
    )
    assert (line.stage, line.section, line.data) == (Stage.ASSEMBLE, 3, {"exit": 0, "seconds": 0.5})


def test_a_stages_own_sentence_says_which_section_it_was_said_in(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run() as run, run.section(Stage.RECORD, 2):
        run.note("kept.")
    [line] = lines_of(seen)
    assert (line.source, line.stage, line.section) == (None, Stage.RECORD, 2)


def test_an_exception_on_a_record_is_named_and_its_traceback_is_not_kept(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run():
        try:
            raise OSError("the disk is full\nand this second line is detail")
        except OSError:
            log.warning("could not write", exc_info=True)
    [line] = lines_of(seen)
    assert line.data == {"error": "OSError: the disk is full"}
    assert "Traceback" not in line.model_dump_json()


@pytest.mark.parametrize(
    ("number", "level"),
    [
        (logging.DEBUG - 5, Level.DEBUG),
        (logging.DEBUG, Level.DEBUG),
        (logging.INFO, Level.INFO),
        (logging.WARNING, Level.WARNING),
        (logging.ERROR, Level.ERROR),
        (logging.CRITICAL, Level.ERROR),
    ],
)
def test_every_standard_level_lands_on_one_of_the_streams_four(number: int, level: Level) -> None:
    assert level_of(number) is level


def test_nothing_prints_when_no_run_is_bound() -> None:
    """A host that configured no logging gets silence, not Python's last-resort handler on bare stderr.

    It runs in a fresh interpreter, because pytest's own handlers on the root logger would answer for
    the package inside this one and hide a missing handler.
    """
    said = "\n".join(
        f"logging.getLogger({name!r}).log({level}, 'a sentence nobody bound a run for')"
        for name in ("decktalk", "decktalk.media.ffmpeg", "decktalk.toolchain.chromium_fetch", "decktalk.media.origin")
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)
    )
    ran = subprocess.run(
        [sys.executable, "-c", f"import logging\nimport decktalk\n{said}"], capture_output=True, text=True, check=True
    )
    assert (ran.stdout, ran.stderr) == ("", "")


def test_a_record_that_cannot_be_rendered_is_counted_and_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging, "raiseExceptions", True)
    # pytest's own capture handler on the root logger raises on a bad record, and a host's handler is
    # the host's business, so the record is kept to the package's handler alone.
    monkeypatch.setattr(LOGGER, "propagate", False)
    here = a_machine(tmp_path)
    before = HANDLER.failures
    with here.run():
        log.warning("%d cues", "not a number")
    assert HANDLER.failures == before + 1
    assert capsys.readouterr().err == ""


def test_a_renderer_that_logs_does_not_recurse_into_the_stream(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []

    def chatty(event: Event) -> None:
        seen.append(event)
        log.info("rendered a %s line", event.event)

    with here.events.subscribe(chatty), here.run() as run:
        run.note("one")
    assert [line.event for line in seen] == ["run.start", "log", "run.done"]


def test_two_runs_on_two_threads_each_keep_their_own_lines(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    both_bound = threading.Barrier(2)

    def one(section: int) -> None:
        with here.run() as run, run.section(Stage.RECORD, section):
            both_bound.wait(timeout=5)
            # A worker a stage starts runs under a copy of its parent's context, as the pools do.
            copy_context().run(log.info, "recorded %d", section)

    with here.events.subscribe(seen.append):
        threads = [threading.Thread(target=one, args=(number,)) for number in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    runs = {line.run: line for line in seen if line.event == "run.start"}
    said = lines_of(seen)
    assert len(said) == 2 and len(runs) == 2
    for line in said:
        assert line.message == f"recorded {line.section}"
    assert len({line.run for line in said}) == 2


def test_a_hosts_own_handler_hears_the_record_stamped_with_the_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """pytest's capture handler sits on the root logger, where a host's own handler would."""
    here = a_machine(tmp_path)
    with here.run() as run, run.section(Stage.RECORD, 4):
        log.info("stamped")
    [record] = [record for record in caplog.records if record.getMessage() == "stamped"]
    assert (record.decktalk_run, record.decktalk_stage, record.decktalk_section) == (run.id, "record", 4)


def test_a_level_the_host_chose_is_kept_and_the_handler_is_installed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LOGGER, "handlers", [])
    monkeypatch.setattr(LOGGER, "level", logging.WARNING)
    first = install()
    assert install() is first
    assert [type(handler) for handler in LOGGER.handlers] == [RunHandler]
    assert LOGGER.level == logging.WARNING


def test_the_package_logger_hears_debug_when_nobody_chose_a_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LOGGER, "handlers", [])
    monkeypatch.setattr(LOGGER, "level", logging.NOTSET)
    install()
    assert LOGGER.level == logging.DEBUG


def test_the_place_is_restored_when_a_block_closes() -> None:
    def receiver(record: logging.LogRecord) -> None:
        del record

    with logging_into(receiver, run="r1"):
        with within(stage=Stage.CUE, section=1):
            assert (logs.WHERE.get().run, logs.WHERE.get().stage, logs.WHERE.get().section) == ("r1", Stage.CUE, 1)
        assert (logs.WHERE.get().stage, logs.WHERE.get().section) == (None, None)
    assert logs.WHERE.get() == logs.Where()


def test_a_hosts_own_handler_never_sees_a_registered_secret(caplog: pytest.LogCaptureFixture) -> None:
    """An f-string over a revealed key reaches the root logger's handlers as well as the run's line."""
    canary = "sk_host_handler_canary_77c1"
    Secret(canary, "ELEVENLABS_API_KEY")
    log.warning(f"sent {canary}", extra={"data": {"header": canary}})
    heard = f"{caplog.records[-1].getMessage()} {data_of(caplog.records[-1])}"
    assert canary not in heard and "<secret ELEVENLABS_API_KEY>" in heard


# ---- every failure path leaves a record --------------------------------------------------------------
#
# Each row injects one fault inside a real run with a real events file, then reads the file back
# through the `Line` adapter, as a host would, and names the one record the fault must leave. A path
# that stops leaving its record fails its row, which is what keeps the table true as the code grows.

LINES = TypeAdapter(Line)


@dataclass(frozen=True)
class Record:
    """The record one fault must leave: its level, the module that wrote it and the keys its data holds."""

    level: Level
    source: str
    keys: frozenset[str] = frozenset()
    count: int = 1


@dataclass(frozen=True)
class Ended:
    """How the run a fault ended must say it ended, on its last line."""

    outcome: Outcome
    code: ErrorCode


def a_host_machine(root: Path, *, limit: float = 600.0) -> Machine:
    """A machine that read nothing, whose tools stop a call after `limit` seconds."""
    return Machine(
        environ={},
        tables={},
        config_path=root / "machine.toml",
        cwd=root,
        toolchain=Toolchain(tools=ToolsConfig(cache_dir=str(root / "cache"), timeout_seconds=limit)),
    )


def read_back(events: Path) -> list[Event]:
    """Every line of the one run whose events file is in `events`, read as a host reads it."""
    [path] = events.glob("*.jsonl")
    return [LINES.validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()]


def recorded(root: Path, fault: Callable[[Run], object], *, limit: float = 600.0) -> list[Event]:
    """Every line of the one run `fault` ran in, read back from its events file."""
    here = a_host_machine(root, limit=limit)
    events = root / "build" / "events"
    with contextlib.suppress(Exception, KeyboardInterrupt), here.run(root=root, events_dir=events) as run:
        fault(run)
    return read_back(events)


def a_tool(monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """ffmpeg replaced by a small Python process, so the pipes, the poll and the kill are a tool's own."""
    monkeypatch.setattr(ffmpeg, "ffmpeg_paths", lambda: ("ffmpeg", "ffprobe"))
    spawn = ffmpeg._spawn

    def fake(_cmd: list[str]) -> subprocess.Popen[bytes]:
        with children_see(os.environ):
            return spawn([sys.executable, "-c", script])

    monkeypatch.setattr(ffmpeg, "_spawn", fake)


def tool_exits_1(_run: Run, monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    a_tool(monkeypatch, "import sys; sys.stderr.write('no such file'); sys.exit(1)")
    ffmpeg.run("-i", "a.mp3", "out.mp3")


def tool_runs_too_long(_run: Run, monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    a_tool(monkeypatch, "import time; time.sleep(30)")
    ffmpeg.run("-i", "a.mp3", "out.mp3")


def tool_is_cancelled(run: Run, monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    a_tool(monkeypatch, "import time; time.sleep(30)")
    threading.Timer(0.2, run.cancel.cancel).start()
    ffmpeg.run("-i", "a.mp3", "out.mp3")


def reader_fails(_run: Run, monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    a_tool(monkeypatch, "import sys, time; sys.stdout.buffer.write(b'f' * 200000); sys.stdout.flush(); time.sleep(30)")

    def refuse(_chunk: bytes) -> None:
        raise ValueError("the frame was not the size it was planned at")

    ffmpeg.stream("-i", "film.mp4", "-", into=refuse)


def pinned_download_fails(_run: Run, monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    monkeypatch.setattr(ffmpeg.ffmpeg_fetch, "installed_pinned", lambda *_args: None)
    monkeypatch.setattr(ffmpeg.ffmpeg_fetch, "pinned_build", lambda *_args: object())
    monkeypatch.setattr(ffmpeg, "_path_pair", lambda: ("/usr/bin/ffmpeg", "/usr/bin/ffprobe"))

    def offline(**_kwargs: object) -> tuple[str, str]:
        raise OSError("network is unreachable")

    monkeypatch.setattr(ffmpeg.ffmpeg_fetch, "fetch_ffmpeg", offline)
    ffmpeg._resolve(ToolsConfig(), None)


def installer_fails(_run: Run, monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    failed = subprocess.CompletedProcess([], 1, b"", b"ERROR: host unreachable\n")
    monkeypatch.setattr(chromium_fetch.subprocess, "run", lambda cmd, **_kwargs: failed)
    chromium_fetch.fetch_chromium(env={})


def installer_hangs(_run: Run, monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    def hang(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd, float(kwargs["timeout"]))  # type: ignore[arg-type]

    monkeypatch.setattr(chromium_fetch.subprocess, "run", hang)
    chromium_fetch.fetch_chromium(env={})


def fix_command_fails(run: Run, monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
    failed = subprocess.CompletedProcess([], 2, b"", b"OSError: the cache is read-only\n")
    monkeypatch.setattr("decktalk.machine.subprocess.run", lambda argv, **_kwargs: failed)
    fix = CommandFix(title="t", applicability=Applicability.SAFE, command=("decktalk", "install"))
    apply_fix(run, Code.FILE_MISSING, fix, root=tmp, scope=Scope.MACHINE, unsafe=False)


def router_breaks(_run: Run, monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
    handlers: list[Callable[..., None]] = []
    target = type("Target", (), {"route": lambda _self, _pattern, handler: handlers.append(handler)})()
    origin.route_pages(target, origin.Allowed.of(tmp, ()), trusted=True)  # type: ignore[arg-type]

    def broken(*_args: object) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(origin, "local_target", broken)
    route = type("Route", (), {"fulfill": lambda _self, **_kwargs: None})()
    handlers[0](route, type("Request", (), {"url": f"{origin.ORIGIN}/deck/index.html?q=1"})())


def two_sections_fail(_run: Run, _monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    both_sent = threading.Barrier(2)

    def work(plan: SimpleNamespace) -> None:
        both_sent.wait(timeout=5)
        if plan.segment.index == 2:
            threading.Event().wait(0.1)
        raise ProviderError(f"section {plan.segment.index} failed")

    plans = [SimpleNamespace(segment=SimpleNamespace(index=number)) for number in (1, 2)]
    _in_pool(work, plans, workers=2)  # type: ignore[arg-type]


def browser_is_fetched_again(_run: Run, monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
    executable = tmp / "chrome"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    chromium = FakeChromium(executable, refusal="error while loading shared libraries: libnss3.so", refusals=1)
    fetched = subprocess.CompletedProcess([], 0, b"", b"")
    monkeypatch.setattr(chromium_fetch.subprocess, "run", lambda cmd, **_kwargs: fetched)
    browser.launch(chromium.driver(), policy=browser.TRUSTED)


def run_is_cancelled(run: Run, _monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    run.cancel.cancel()
    with run.section(Stage.RECORD, 1):
        pass


def run_is_interrupted(_run: Run, _monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    raise KeyboardInterrupt


def a_bug(_run: Run, _monkeypatch: pytest.MonkeyPatch, _tmp: Path) -> None:
    raise KeyError("page")


RETRY = frozenset({"wait_seconds", "wait_source", "attempt", "retries"})
KILL = frozenset({"argv", "reason", "seconds", "limit"})
CALL = frozenset({"argv", "exit", "seconds", "output_tail"})

FAILURES: dict[str, tuple[Callable[..., object], Record | None, Ended | None, float]] = {
    "ffmpeg exits non-zero": (
        tool_exits_1,
        Record(Level.WARNING, "media.ffmpeg", CALL),
        Ended(Outcome.FAILED, ErrorCode.TOOL),
        600.0,
    ),
    "ffmpeg runs past its limit": (
        tool_runs_too_long,
        Record(Level.WARNING, "media.ffmpeg", KILL),
        Ended(Outcome.FAILED, ErrorCode.TOOL),
        0.3,
    ),
    "ffmpeg is cancelled": (
        tool_is_cancelled,
        Record(Level.WARNING, "media.ffmpeg", KILL),
        Ended(Outcome.STOPPED, ErrorCode.CANCELLED),
        600.0,
    ),
    "ffmpeg's reader fails": (
        reader_fails,
        Record(Level.WARNING, "media.ffmpeg", KILL),
        Ended(Outcome.FAILED, ErrorCode.INTERNAL),
        600.0,
    ),
    "the pinned ffmpeg cannot be fetched": (
        pinned_download_fails,
        Record(Level.WARNING, "media.ffmpeg", frozenset({"reason", "using"})),
        None,
        600.0,
    ),
    "playwright install fails": (
        installer_fails,
        Record(Level.WARNING, "toolchain.chromium_fetch", CALL),
        Ended(Outcome.FAILED, ErrorCode.TOOL),
        600.0,
    ),
    "playwright install hangs": (
        installer_hangs,
        Record(Level.WARNING, "toolchain.chromium_fetch", frozenset({"argv", "reason", "limit"})),
        Ended(Outcome.FAILED, ErrorCode.TOOL),
        600.0,
    ),
    "a fix command fails": (fix_command_fails, Record(Level.WARNING, "machine", CALL), None, 600.0),
    "the origin cannot answer": (
        router_breaks,
        Record(Level.WARNING, "media.origin", frozenset({"url", "error"})),
        None,
        600.0,
    ),
    "two narration sections fail": (
        two_sections_fail,
        Record(Level.WARNING, "stages.narrate", frozenset({"section", "error"})),
        Ended(Outcome.FAILED, ErrorCode.PROVIDER),
        600.0,
    ),
    "Chromium will not launch and is fetched again": (
        browser_is_fetched_again,
        Record(Level.INFO, "media.browser", frozenset({"reason"})),
        None,
        600.0,
    ),
    "the run is cancelled": (run_is_cancelled, None, Ended(Outcome.STOPPED, ErrorCode.CANCELLED), 600.0),
    "the run is interrupted": (run_is_interrupted, None, Ended(Outcome.STOPPED, ErrorCode.CANCELLED), 600.0),
    "the run meets a bug": (a_bug, None, Ended(Outcome.FAILED, ErrorCode.INTERNAL), 600.0),
}


@pytest.mark.parametrize("name", list(FAILURES))
def test_every_failure_path_leaves_its_record_in_the_events_file(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fault, record, ended, limit = FAILURES[name]
    lines = recorded(tmp_path, lambda run: fault(run, monkeypatch, tmp_path), limit=limit)
    if record is not None:
        said = [line for line in lines if isinstance(line, Log) and line.source == record.source]
        matching = [line for line in said if line.level is record.level and record.keys <= set(line.data or {})]
        assert len(matching) == record.count, [line.model_dump() for line in said]
    last = lines[-1]
    assert isinstance(last, RunDone)
    if ended is not None:
        assert last.outcome is ended.outcome
        assert last.error is not None and last.error.code is ended.code
    # Nothing a failure path writes goes anywhere but the stream.
    assert capsys.readouterr() == ("", "")


@pytest.fixture
def voice_service(monkeypatch: pytest.MonkeyPatch) -> Iterator[HTTPServer]:
    """A threaded local voice, with every wait skipped, so a stalled reply holds one thread and no test time."""
    monkeypatch.setattr(_http, "pause", lambda _seconds: None)
    released = threading.Event()
    server = HTTPServer(host="127.0.0.1", threaded=True)
    server.start()
    server.released = released  # type: ignore[attr-defined]
    try:
        yield server
    finally:
        released.set()
        server.clear()
        server.stop()


def test_a_busy_voice_leaves_a_warning_per_retry_and_a_trace_per_attempt(tmp_path: Path, voice_service) -> None:
    for _ in range(2):
        voice_service.expect_oneshot_request("/speak").respond_with_data("", 429, {"Retry-After": "2"})
    voice_service.expect_request("/speak").respond_with_json({"ok": True})
    url = voice_service.url_for("/speak")
    lines = recorded(tmp_path, lambda _run: _http.post_json(url, {}, {}, timeout=5, retries=3))
    said = [line for line in lines if isinstance(line, Log) and line.source == "speech.http"]
    retries = [line.data for line in said if line.level is Level.WARNING]
    attempts = [line.data for line in said if line.level is Level.DEBUG]
    assert [(data["wait_seconds"], data["wait_source"]) for data in retries] == [(2.0, "retry-after")] * 2  # type: ignore[index]
    assert [data["status"] for data in attempts] == [429, 429, 200]  # type: ignore[index]


def test_a_stalled_voice_is_retried_and_ends_as_a_provider_refusal(tmp_path: Path, voice_service) -> None:
    def stall(_request: object) -> Response:
        def body() -> Iterator[bytes]:
            yield b"{"
            voice_service.released.wait(timeout=10)

        return Response(body(), 200, {"Content-Length": "100"})

    voice_service.expect_request("/speak").respond_with_handler(stall)
    url = voice_service.url_for("/speak")
    lines = recorded(tmp_path, lambda _run: _http.post_json(url, {}, {}, timeout=1, retries=1))
    retries = [line for line in lines if isinstance(line, Log) and line.level is Level.WARNING]
    assert len(retries) == 1 and "stopped answering" in retries[0].message
    last = lines[-1]
    assert isinstance(last, RunDone) and last.error is not None and last.error.code is ErrorCode.PROVIDER


def test_a_held_project_names_the_lock_on_its_last_line(tmp_path: Path) -> None:
    root = write_project(tmp_path)
    project = decktalk.open(root, machine=a_host_machine(root))
    project.workspace.build.mkdir(parents=True, exist_ok=True)
    held = threading.Event()
    done = threading.Event()

    def hold() -> None:
        with FileLock(project.workspace.build / ".lock"):
            held.set()
            done.wait(timeout=10)

    holder = threading.Thread(target=hold)
    holder.start()
    held.wait(timeout=5)
    try:
        with pytest.raises(ProjectLocked):
            project.cue()
    finally:
        done.set()
        holder.join()
    last = read_back(root / "build" / "events")[-1]
    assert isinstance(last, RunDone) and last.error is not None and last.error.code is ErrorCode.LOCKED


def test_a_stage_missing_its_input_names_it_on_its_last_line(tmp_path: Path) -> None:
    root = write_project(tmp_path)
    project = decktalk.open(root, machine=a_host_machine(root))
    with pytest.raises(NotBuiltError):
        project.cue()
    last = read_back(root / "build" / "events")[-1]
    assert isinstance(last, RunDone) and last.error is not None
    assert last.error.code is ErrorCode.NOT_BUILT and "takes.json" in last.error.message
