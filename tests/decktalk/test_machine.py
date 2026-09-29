"""This computer as one value, the run every call opens on it, and the gate a spend passes."""

from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decktalk import machine as machine_module
from decktalk.errors import ApprovalRequired, Cancel, Cancelled, ErrorCode, InputError
from decktalk.events import Event, Level, Log, RunDone, RunStart, StageDone, StageStart
from decktalk.findings import (
    Applicability,
    Certainty,
    Code,
    CommandFix,
    Edit,
    EditFix,
    Finding,
    Location,
    RuntimeFix,
    SettingFix,
)
from decktalk.machine import (
    CHROMIUM,
    FIX_TIMEOUT_SECONDS,
    InstalledTool,
    Machine,
    Toolchain,
    apply_fix,
    fixes_of,
    init,
)
from decktalk.media import ffmpeg as ffmpeg_module
from decktalk.media.environment import child_environment
from decktalk.media.ffmpeg import bound_tools
from decktalk.pipeline import Outcome, Stage
from decktalk.project import open as open_project
from decktalk.results import Layer, Scope, Spend, SpendState, StatusResult, Voicing
from decktalk.settings import BY_ID, ToolsConfig
from decktalk.speech import VoiceContext, get_provider
from decktalk.toolchain import assets
from decktalk.toolchain.announce import announce
from decktalk.toolchain.cache import cache_dir, standard_cache_dir
from support.paths import REPO
from support.runs import a_machine

from .conftest import FakeVoice


def spend(dollars: float, ceiling: float, *, layer: Layer = Layer.PROJECT) -> Spend:
    return Spend(
        state=SpendState.ESTIMATE,
        sections=(1,),
        characters=1000,
        dollars=dollars,
        ceiling_dollars=ceiling,
        price_per_1000_characters=0.3,
        price_layer=layer,
    )


# ---- the one reader of the environment -------------------------------------------------------

SRC = REPO / "src" / "decktalk"

READERS = ("machine.py", "cli")
"""Where the process environment and the home directory may be read: the machine, and its first client."""


PROCESS_READS = {("os", "environ"), ("os", "getenv"), ("Path", "home")}
"""The three ways a module reaches past its arguments for the process's environment or home directory."""


def reads_the_process(path: Path) -> bool:
    """Whether one module names any of the three process reads, as an attribute or as an import."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in PROCESS_READS:
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            if any(("os", alias.name) in PROCESS_READS for alias in node.names):
                return True
    return False


def test_only_the_machine_and_the_command_line_read_the_process() -> None:
    """A setting, a switch or a directory read from the process follows the host rather than the tenant."""
    offenders = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC).parts[0] not in READERS and reads_the_process(path)
    }
    assert offenders == set()


# ---- the toolchain ------------------------------------------------------------------------


def test_a_toolchain_takes_what_the_settings_name(tmp_path: Path) -> None:
    """The pair is a field, so the first project opened cannot pin the toolchain for every later one."""
    named = ToolsConfig(ffmpeg=str(tmp_path / "ff"), ffprobe=str(tmp_path / "fp"))
    for path in (tmp_path / "ff", tmp_path / "fp"):
        path.write_bytes(b"")
    assert Toolchain.of(named, cache=tmp_path / "cache").ffmpeg == tmp_path / "ff"


def test_the_cache_a_run_fetches_into_is_the_one_its_keys_name(tmp_path: Path) -> None:
    named = ToolsConfig(cache_dir=str(tmp_path / "elsewhere"))
    assert Toolchain.of(named, cache=tmp_path / "standard").cache_dir == tmp_path / "elsewhere"


def test_the_cache_is_the_machines_own_when_no_key_moves_it(tmp_path: Path) -> None:
    here = Machine(
        environ={},
        tables={},
        config_path=tmp_path / "config.toml",
        cwd=tmp_path,
        toolchain=Toolchain(cache=tmp_path / "standard"),
    )
    with here.run():
        assert cache_dir() == tmp_path / "standard"
    assert here.cache_dir == tmp_path / "standard"


def test_a_fetch_that_no_machine_bound_is_refused_rather_than_guessed() -> None:
    """A directory worked out from the process would put a host's tools where the host never said."""
    with pytest.raises(machine_module.ToolError, match="no machine named a directory"):
        cache_dir()


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        ("darwin", {"XDG_CACHE_HOME": "/ignored"}, "home/Library/Caches/decktalk"),
        ("linux", {}, "home/.cache/decktalk"),
        ("linux", {"XDG_CACHE_HOME": "/xdg"}, "/xdg/decktalk"),
        ("win32", {"LOCALAPPDATA": "/local"}, "/local/decktalk"),
        ("win32", {}, "home/AppData/Local/decktalk"),
    ],
)
def test_the_standard_cache_is_worked_out_from_the_environment_the_machine_holds(
    platform: str, environ: dict[str, str], expected: str
) -> None:
    assert standard_cache_dir(environ, Path("home"), platform) == Path(expected)


def test_a_toolchain_that_is_not_there_names_the_command_that_fetches_it() -> None:
    with pytest.raises(machine_module.ToolError) as refused:
        Toolchain().paths()
    assert refused.value.code is ErrorCode.TOOL
    assert "decktalk install" in (refused.value.hint or "")


def test_a_toolchain_that_is_there_fetches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> tuple[str, str]:
        raise AssertionError("a complete toolchain must never reach for the network")

    monkeypatch.setattr(machine_module, "fetch_ffmpeg", refuse)
    chain = Toolchain(ffmpeg=tmp_path / "a", ffprobe=tmp_path / "b")
    assert chain.fetched() is chain


def test_a_run_hands_its_pair_and_its_cancel_to_every_ffmpeg_call(tmp_path: Path) -> None:
    """A run that could not reach ffmpeg with its cancel token would wait out an encode it was told to stop."""
    pair = (tmp_path / "ff", tmp_path / "fp")
    here = Machine(
        environ={},
        tables={},
        config_path=tmp_path / "config.toml",
        cwd=tmp_path,
        toolchain=Toolchain(tools=ToolsConfig(cache_dir=str(tmp_path / "cache")), ffmpeg=pair[0], ffprobe=pair[1]),
    )
    with here.run() as run:
        bound = ffmpeg_module.TOOLS.get()
        assert bound is not None
        assert bound.cancel is run.cancel
        assert bound.paths() == (str(pair[0]), str(pair[1]))


def test_a_browser_is_built_from_the_machines_environment_and_never_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "the-process-language")
    here = a_machine(tmp_path, LANG="the-machines-language", ELEVENLABS_API_KEY="sk-not-a-key")
    with here.run():
        seen = child_environment()
    assert seen["LANG"] == "the-machines-language"
    assert "ELEVENLABS_API_KEY" not in seen


# ---- the run ------------------------------------------------------------------------------


def test_every_line_of_a_run_carries_its_run_and_counts_from_zero(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run() as run:
        run.note("one")
        run.note("two")
    assert [line.event for line in seen] == ["run.start", "log", "log", "run.done"]
    assert {line.run for line in seen} == {run.id}
    assert [line.seq for line in seen] == [0, 1, 2, 3]


def test_a_run_that_fails_closes_as_failed_and_lets_the_failure_through(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), pytest.raises(ZeroDivisionError), here.run():
        raise ZeroDivisionError
    assert isinstance(seen[-1], RunDone) and seen[-1].outcome is Outcome.FAILED


def test_a_run_with_a_project_writes_its_own_file_and_says_where(tmp_path: Path) -> None:
    """One file per run, so a watch loop beside a build by hand cannot overwrite the other's lines."""
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    with here.run(root=tmp_path, events_dir=events) as run:
        run.note("hello")
    written = events / f"{run.id}.jsonl"
    assert written.exists()
    assert "hello" in written.read_text(encoding="utf-8")


def test_a_run_says_the_path_its_own_lines_are_going_to(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run(root=tmp_path, events_dir=tmp_path / "build" / "events") as run:
        pass
    assert isinstance(seen[0], RunStart)
    assert seen[0].events_path == Path(f"build/events/{run.id}.jsonl")


def test_a_machine_run_holds_no_project_so_it_writes_no_file(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run():
        pass
    assert isinstance(seen[0], RunStart) and seen[0].events_path is None


def test_the_oldest_event_files_are_pruned_before_a_new_run_opens(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    events.mkdir(parents=True)
    for name in ("a", "b", "c"):
        (events / f"{name}.jsonl").write_text("{}\n", encoding="utf-8")
    with here.run(root=tmp_path, events_dir=events, keep_runs=1):
        pass
    assert len(list(events.glob("*.jsonl"))) == 2  # the one kept, and this run's own


def test_a_stage_opens_and_closes_on_the_stream(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run() as run, run.stage(Stage.NARRATE, index=1, count=2):
        pass
    started = next(line for line in seen if isinstance(line, StageStart))
    done = next(line for line in seen if isinstance(line, StageDone))
    assert (started.stage, started.index, started.count) == (Stage.NARRATE, 1, 2)
    assert done.outcome is Outcome.OK


def test_a_stage_that_raises_closes_as_failed(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run() as run:
        with pytest.raises(ValueError, match="no"), run.stage(Stage.RECORD):
            raise ValueError("no")
    assert next(line for line in seen if isinstance(line, StageDone)).outcome is Outcome.FAILED


def test_a_cancelled_run_stops_at_the_next_section_boundary(tmp_path: Path) -> None:
    """A stage checks between sections, so a cancelled run leaves whole artifacts rather than half of one."""
    here = a_machine(tmp_path)
    cancel = Cancel()
    with here.run(cancel=cancel) as run:
        with run.section(Stage.RECORD, 1):
            cancel.cancel()
        with pytest.raises(Cancelled), run.section(Stage.RECORD, 2):
            pass


def test_a_result_carries_the_run_the_judgements_and_the_files(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here.run(root=tmp_path) as run:
        run.wrote(tmp_path / "build" / "final" / "a.mp4")
        run.wrote(tmp_path / "build" / "final" / "a.mp4")
        result = run.result(StatusResult, name="t", script=Path("script.md"), cues=Path("cues.json"), sections=())
    assert result.run == run.id
    assert result.ok


def test_a_certain_judgement_is_what_makes_a_result_not_ok(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    certain = Finding(code=Code.CUE_UNRESOLVED, message="x", location=Location(where="cues.json"))
    unsure = Finding(code=Code.PAGE_SWAP_APART, message="y", location=Location(where="deck/index.html"))
    assert certain.certainty is Certainty.CERTAIN and unsure.certainty is Certainty.UNCERTAIN
    with here.run() as run:
        run.found(unsure)
        assert run.result(StatusResult, name="t", script=Path("s"), cues=Path("c"), sections=()).ok
        run.found(certain)
        assert not run.result(StatusResult, name="t", script=Path("s"), cues=Path("c"), sections=()).ok


# ---- the spend gate -------------------------------------------------------------------------


def test_nothing_is_bought_without_a_paid_voicing(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here.run() as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(spend(0.42, 0.42))
    assert refused.value.code is ErrorCode.APPROVAL
    assert "--spend" in (refused.value.hint or "")


def test_a_refusal_states_the_price_in_the_one_sentence_every_surface_uses(tmp_path: Path) -> None:
    """A run whose certain part is zero must not be said to spend $0.00, which its ceiling contradicts."""
    here = a_machine(tmp_path)
    unmatched = spend(0.0, 0.3)
    with here.run() as run, pytest.raises(ApprovalRequired) as unvoiced:
        run.approve(unmatched)
    with here.run(voice=Voicing.PAID, max_cost=0.1) as run, pytest.raises(ApprovalRequired) as capped:
        run.approve(unmatched)
    for refused in (unvoiced, capped):
        assert str(refused.value).startswith(unmatched.sentence)
        assert "$0.00" not in str(refused.value)


def test_a_paid_run_inside_its_ceiling_goes_through(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here.run(voice=Voicing.PAID, max_cost=1.0) as run:
        assert run.approve(spend(0.42, 0.9)).dollars == 0.42


def test_the_ceiling_is_compared_against_the_most_a_run_can_cost(tmp_path: Path) -> None:
    """Credits go one request at a time, so a cap that stopped a run halfway would be a lie."""
    here = a_machine(tmp_path)
    with here.run(voice=Voicing.PAID, max_cost=0.5) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(spend(0.42, 0.9))
    assert "0.90" in str(refused.value)


def test_a_cap_is_refused_while_nobody_has_stated_the_price(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here.run(voice=Voicing.PAID, max_cost=1.0) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(spend(0.42, 0.9, layer=Layer.DEFAULT))
    assert "price_per_1000_characters" in (refused.value.hint or "")


def test_every_priced_request_reaches_the_stream_before_it_is_judged(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run(voice=Voicing.PAID) as run:
        run.approve(spend(0.42, 0.42))
    assert [line.event for line in seen if line.event == "spend"] == ["spend"]


# ---- the machine's own calls -----------------------------------------------------------------


def test_the_credential_is_asked_about_and_never_read(tmp_path: Path) -> None:
    assert not a_machine(tmp_path).voice_key
    assert a_machine(tmp_path, ELEVENLABS_API_KEY="sk_real").voice_key


def test_from_environment_is_the_one_reading_of_this_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "none.toml"))
    here = Machine.from_environment()
    assert here.cwd == Path.cwd()
    assert here.config_path == tmp_path / "none.toml"
    assert here.tables == {}


# ---- a machine a host builds -----------------------------------------------------------------

HOST_SECRETS = {"ELEVENLABS_API_KEY": "sk-host-owned", "ELEVENLABS_VOICE_ID": "house-voice"}
"""What a host hands its voice job: the key and the voice, and nothing else from its own process."""


def a_host(tmp_path: Path, **choices: object) -> Machine:
    """A machine a host built from values it chose, with the voice job's two variables by default."""
    values: dict[str, object] = {
        "environ": HOST_SECRETS,
        "config_path": tmp_path / "host" / "machine.toml",
        "cwd": tmp_path,
        "cache_dir": tmp_path / "host" / "cache",
        **choices,
    }
    return Machine.of(**values)  # type: ignore[arg-type]


def test_what_reading_the_machine_noticed_is_a_warning_on_every_run(tmp_path: Path) -> None:
    """A misspelled variable or machine key in a log line never reached `--json`, so it is a line of the run."""
    config = tmp_path / "host" / "machine.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[video]\npresett = 'veryfast'\n", encoding="utf-8")
    here = a_host(tmp_path, environ={"DECKTALK_VIDEO_CRV": "20"}, config_path=config)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run():
        pass
    warned = [line.message for line in seen if isinstance(line, Log) and line.level is Level.WARNING]
    assert tuple(warned) == here.notes
    assert [("CRV" in note, "presett" in note) for note in warned] == [(True, False), (False, True)]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test the moment anything opens a connection or looks up a host name."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a host-supplied voice must never reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def a_starter(tmp_path: Path, here: Machine) -> Path:
    root = tmp_path / "tenant"
    init(root, machine=here, skills=False)
    return root


def test_a_host_machine_reads_nothing_from_the_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "the-process-file.toml"))
    monkeypatch.setenv("DECKTALK_ALLOW_ANY_API_BASE", "1")
    monkeypatch.setenv("DECKTALK_TOOLS_TIMEOUT_SECONDS", "11")
    here = a_host(tmp_path)
    assert here.environ == HOST_SECRETS
    assert here.config_path == tmp_path / "host" / "machine.toml"
    assert here.cache_dir == tmp_path / "host" / "cache"
    assert here.allow_any_api_base is False and here.dotenv is False
    assert here.toolchain.tools.timeout_seconds == BY_ID["tools.timeout_seconds"].default


def test_a_project_opened_on_a_host_machine_keeps_the_hosts_overrides(tmp_path: Path) -> None:
    """A host enforces the page policy through its machine, so a caller's own override must not drop it."""
    here = a_host(tmp_path, overrides=["record.page_policy=untrusted"])
    project = open_project(a_starter(tmp_path, here), machine=here, overrides=["video.crf=20"])
    assert project.settings.record.page_policy == "untrusted"
    assert project.settings.video.crf == 20


@pytest.mark.usefixtures("no_network", "fake_ffmpeg")
def test_the_voice_a_host_supplies_is_the_one_narrate_calls(tmp_path: Path) -> None:
    """A host that hands its machine a fake voice must never have a request reach the real one."""
    voice = FakeVoice()
    contexts: list[VoiceContext] = []

    def build(context: VoiceContext) -> FakeVoice:
        contexts.append(context)
        return voice

    here = a_host(tmp_path, providers={"elevenlabs": build})
    project = open_project(a_starter(tmp_path, here), machine=here)
    result = project.narrate(voice=Voicing.PAID)
    assert result.ok
    assert voice.requests, "the host's voice was never asked for a take"
    assert {request.voice_id for request in voice.requests} == {"house-voice"}
    assert [context.allow_any_api_base for context in contexts] == [False] * len(contexts)


@pytest.mark.usefixtures("no_network")
def test_a_host_machine_answers_no_voice_its_host_left_out(tmp_path: Path) -> None:
    here = a_host(tmp_path, providers={"house": lambda _context: FakeVoice()})
    with here.run(), pytest.raises(InputError, match="not a voice this machine answers for"):
        get_provider("elevenlabs", a_context())


def test_two_machines_in_one_process_answer_with_their_own_voices(tmp_path: Path) -> None:
    first, second = FakeVoice(name="first"), FakeVoice(name="second")
    one = a_host(tmp_path, providers={"elevenlabs": lambda _context: first})
    two = a_host(tmp_path, providers={"elevenlabs": lambda _context: second})
    with one.run():
        with two.run():
            assert get_provider("elevenlabs", a_context()) is second
        assert get_provider("elevenlabs", a_context()) is first


def test_the_machines_switch_is_stamped_on_every_voice_it_builds(tmp_path: Path) -> None:
    """A stage cannot widen where the key goes, and neither can a context built without the machine."""
    seen: list[VoiceContext] = []
    here = a_host(tmp_path, providers={"elevenlabs": seen.append}, allow_any_api_base=True)
    with here.run():
        get_provider("elevenlabs", a_context())
    assert seen[0].allow_any_api_base is True


def test_the_machines_retries_are_stamped_on_every_voice_it_builds(tmp_path: Path) -> None:
    """A busy voice is asked again as often as the machine says, which no project may change."""
    seen: list[VoiceContext] = []
    here = a_host(tmp_path, providers={"elevenlabs": seen.append}, overrides=("narration.retries=5",))
    with here.run():
        get_provider("elevenlabs", a_context())
    assert seen[0].retries == 5


@pytest.mark.usefixtures("no_network")
def test_a_tenants_env_file_is_never_read_under_a_host_machine(tmp_path: Path) -> None:
    """An upload could carry a `.env`, and the key it names would then pay for the tenant's take."""
    here = a_host(tmp_path, environ={}, providers={"elevenlabs": lambda _context: FakeVoice()})
    root = a_starter(tmp_path, here)
    (root / ".env").write_text("ELEVENLABS_API_KEY=sk-tenant\nELEVENLABS_VOICE_ID=tenant-voice\n", encoding="utf-8")
    project = open_project(root, machine=here)
    with pytest.raises(InputError, match="ELEVENLABS_VOICE_ID is not set"):
        project.narrate(voice=Voicing.PAID)


@pytest.mark.usefixtures("no_network", "fake_ffmpeg")
def test_the_authors_own_env_file_is_read_under_a_machine_that_allows_it(tmp_path: Path) -> None:
    voice = FakeVoice()
    here = a_host(tmp_path, environ={}, providers={"elevenlabs": lambda _context: voice}, dotenv=True)
    root = a_starter(tmp_path, here)
    (root / ".env").write_text("ELEVENLABS_API_KEY=sk-author\nELEVENLABS_VOICE_ID=author-voice\n", encoding="utf-8")
    assert open_project(root, machine=here).narrate(voice=Voicing.PAID).ok
    assert {request.voice_id for request in voice.requests} == {"author-voice"}


def a_context() -> VoiceContext:
    """A context no machine stamped, which is what a stage builds from its project's values."""
    return VoiceContext(
        secrets=None,  # type: ignore[arg-type]
        api_base="https://api.elevenlabs.io/v1",
        context_chars=1,
        speech_timeout_seconds=1,
        sound_timeout_seconds=1,
    )


def test_an_override_reaches_the_machine_by_its_own_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every pair reaches both the machine and the project, and each takes the keys it owns."""
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "none.toml"))
    here = Machine.from_environment(overrides=(f"tools.cache_dir={tmp_path / 'elsewhere'}",))
    assert here.cache_dir == tmp_path / "elsewhere"


def test_doctor_reports_every_component_and_fetches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = a_machine(tmp_path)
    monkeypatch.setattr(
        Machine,
        "_browser_row",
        lambda self: machine_module.InstalledTool(tool="chromium", version="140", path=None, fetched=False, bytes=None),
    )
    result = here.doctor()
    assert [tool.tool for tool in result.tools] == ["chromium", "ffmpeg", "ffprobe", "katex"]
    assert not result.ok  # this machine has no encoder, which a build needs
    assert {found.code for found in result.findings} == {Code.FILE_MISSING}
    assert result.bias_ms is None  # the bias is measured only when a caller asks


def test_the_browser_row_names_where_its_chromium_lives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A person told the browser is there still has to find it, so doctor names its path."""
    executable = tmp_path / "chrome"
    executable.write_bytes(b"")

    class Launched:
        version = "140.0"

        def close(self) -> None:
            return None

    class Driver:
        chromium = SimpleNamespace(launch=Launched, executable_path=str(executable))

        def __enter__(self) -> Driver:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr("playwright.sync_api.sync_playwright", Driver)
    row = a_machine(tmp_path)._browser_row()
    assert (row.version, row.path) == ("140.0", executable)


def test_a_measured_doctor_keeps_the_number_it_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one key no person may type is written by the one command that holds an honest value for it."""
    here = a_machine(tmp_path)
    monkeypatch.setattr(
        machine_module,
        "import_module",
        lambda _name: SimpleNamespace(measure_presentation_bias=lambda: 12.5),
    )
    result = here.doctor(measure=True)
    assert result.bias_ms == 12.5
    assert result.written == (here.config_path,)
    assert "presentation_bias_ms = 12.5" in here.config_path.read_text(encoding="utf-8")


def test_a_doctor_that_measured_nothing_writes_nothing(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    assert here.doctor().written == ()
    assert not here.config_path.exists()


def test_install_fetches_the_browser_and_the_encoder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []
    monkeypatch.setattr(machine_module.chromium_fetch, "fetch_chromium", lambda **_kw: fetched.append("chromium"))
    monkeypatch.setattr(machine_module, "fetch_ffmpeg", lambda: (str(tmp_path / "ffmpeg"), str(tmp_path / "ffprobe")))
    result = a_machine(tmp_path).install()
    assert fetched == ["chromium"]
    assert [tool.tool for tool in result.tools] == ["chromium", "ffmpeg", "ffprobe"]
    assert result.tools[1].path == tmp_path / "ffmpeg"
    assert result.ok


def test_install_reports_the_browser_it_just_fetched_rather_than_a_blank_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row said version null, so `install` printed the browser as missing while `doctor` run
    straight afterwards read the real version off the very browser the fetch had left behind."""
    monkeypatch.setattr(machine_module.chromium_fetch, "fetch_chromium", lambda **_kw: None)
    monkeypatch.setattr(machine_module, "fetch_ffmpeg", lambda: (str(tmp_path / "ffmpeg"), str(tmp_path / "ffprobe")))
    here = a_machine(tmp_path)
    monkeypatch.setattr(
        type(here),
        "_browser_row",
        lambda _self: InstalledTool(tool=CHROMIUM, version="141.0.1", path=None, fetched=False, bytes=None),
    )
    (browser, *_rest) = here.install().tools
    assert browser.version == "141.0.1"
    assert browser.fetched


# ---- applying a fix ---------------------------------------------------------------------------


INSTALL = ("decktalk", "install")
"""The one command a fix may run, which these tests stand in for with a fake subprocess."""


def a_finding(fix: object) -> Finding:
    return Finding(code=Code.FILE_MISSING, message="x", location=Location(where="a"), fix=fix)


def exits(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """Make every command a fix runs exit with `code`, so no test installs anything."""
    monkeypatch.setattr(
        "decktalk.machine.subprocess.run",
        lambda argv, **_: subprocess.CompletedProcess(argv, code, "", ""),
    )


def test_a_fix_is_taken_from_the_finding_whose_code_it_resolves() -> None:
    edit = CommandFix(title="t", applicability=Applicability.SAFE, command=INSTALL)
    assert fixes_of(a_finding(edit)) == ((Code.FILE_MISSING, edit),)
    assert fixes_of([a_finding(None), a_finding(edit)]) == ((Code.FILE_MISSING, edit),)


def test_a_fix_only_a_person_can_make_is_reported_and_never_applied(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    fix = CommandFix(title="t", applicability=Applicability.DISPLAY, command=INSTALL)
    with here.run() as run:
        outcome = apply_fix(run, Code.FILE_MISSING, fix, root=tmp_path, scope=Scope.MACHINE, unsafe=False)
    assert not outcome.applied and "only a person" in (outcome.why or "")


def test_a_fix_that_can_lose_work_is_applied_only_on_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = a_machine(tmp_path)
    exits(monkeypatch, 0)
    fix = CommandFix(title="t", applicability=Applicability.UNSAFE, command=INSTALL)
    with here.run() as run:
        held = apply_fix(run, Code.FILE_MISSING, fix, root=tmp_path, scope=Scope.MACHINE, unsafe=False)
        asked = apply_fix(run, Code.FILE_MISSING, fix, root=tmp_path, scope=Scope.MACHINE, unsafe=True)
    assert not held.applied and asked.applied


def test_a_command_that_fails_is_reported_rather_than_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = a_machine(tmp_path)
    exits(monkeypatch, 1)
    fix = CommandFix(title="t", applicability=Applicability.SAFE, command=INSTALL)
    with here.run() as run:
        outcome = apply_fix(run, Code.FILE_MISSING, fix, root=tmp_path, scope=Scope.MACHINE, unsafe=False)
    assert not outcome.applied and "exited 1" in (outcome.why or "")


def test_a_command_runs_as_this_interpreters_decktalk_under_a_timeout_and_the_machines_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`decktalk` on `PATH` may be another install, and an unbounded command holds `apply` for ever."""
    here = a_machine(tmp_path, ONLY_THIS="1")
    asked: dict[str, object] = {}

    def record(argv: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        asked.update(options, argv=argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("decktalk.machine.subprocess.run", record)
    fix = CommandFix(title="t", applicability=Applicability.SAFE, command=INSTALL)
    with here.run() as run:
        assert apply_fix(run, Code.FILE_MISSING, fix, root=tmp_path, scope=Scope.MACHINE, unsafe=False).applied
    assert asked["argv"] == [sys.executable, "-m", "decktalk", "install"]
    assert asked["timeout"] == FIX_TIMEOUT_SECONDS
    assert asked["env"] == {
        "ONLY_THIS": "1",
        "DECKTALK_CONFIG": str(here.config_path),
        "DECKTALK_TOOLS_CACHE_DIR": str(here.cache_dir),
    }


def test_a_command_that_runs_past_its_timeout_is_stopped_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = a_machine(tmp_path)

    def hang(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, FIX_TIMEOUT_SECONDS)

    monkeypatch.setattr("decktalk.machine.subprocess.run", hang)
    fix = CommandFix(title="t", applicability=Applicability.SAFE, command=INSTALL)
    with here.run() as run:
        outcome = apply_fix(run, Code.FILE_MISSING, fix, root=tmp_path, scope=Scope.MACHINE, unsafe=False)
    assert not outcome.applied and "was stopped" in (outcome.why or "")


def test_a_command_built_without_validation_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model refuses a foreign argv, and a model built past its validator meets the same refusal here."""
    here = a_machine(tmp_path)
    marker = tmp_path / "ran"

    def run_it(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        marker.write_text(" ".join(argv), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("decktalk.machine.subprocess.run", run_it)
    hostile = CommandFix.model_construct(
        kind="command", title="t", applicability=Applicability.SAFE, command=("sh", "-c", f"touch {marker}")
    )
    with here.run() as run:
        outcome = apply_fix(run, Code.FILE_MISSING, hostile, root=tmp_path, scope=Scope.MACHINE, unsafe=False)
    assert not outcome.applied and "not one of DeckTalk's own commands" in (outcome.why or "")
    assert not marker.exists()


def an_edit(file: str | Path, **locator: object) -> EditFix:
    """A safe fix of one edit, which is the shape a finding read from JSON hands `apply`."""
    edit = Edit.model_validate({"file": file, "new": "written by a fix", **locator})
    return EditFix(title="t", applicability=Applicability.SAFE, edits=(edit,))


def applied(here: Machine, fix: EditFix | RuntimeFix, root: Path) -> tuple[bool, str]:
    with here.run() as run:
        outcome = apply_fix(run, Code.CUE_MISSING, fix, root=root, scope=Scope.PROJECT, unsafe=False)
    return outcome.applied, outcome.why or ""


@pytest.mark.parametrize("escape", ["../outside.txt", "deeper/../../outside.txt"])
def test_an_edit_that_climbs_out_of_the_project_is_refused(tmp_path: Path, escape: str) -> None:
    root = tmp_path / "project"
    root.mkdir()
    done, why = applied(a_machine(tmp_path), an_edit(escape, line=1), root)
    assert not done and "outside the project" in why
    assert not (tmp_path / "outside.txt").exists()


def test_an_edit_that_names_an_absolute_path_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    target = tmp_path / "elsewhere.txt"
    done, why = applied(a_machine(tmp_path), an_edit(target, line=1), root)
    assert not done and "outside the project" in why
    assert not target.exists()


def test_an_edit_through_a_link_that_leaves_the_project_is_refused(tmp_path: Path) -> None:
    """A link spells a path inside the project and writes outside it, so the check follows the link."""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.txt").write_text("mine\n", encoding="utf-8")
    try:
        (root / "shared").symlink_to(outside, target_is_directory=True)
    except OSError:  # pragma: no cover  (Windows makes a link only in developer mode)
        pytest.skip("this machine does not let an unprivileged user make a link")
    done, why = applied(a_machine(tmp_path), an_edit("shared/notes.txt", line=1, old="mine"), root)
    assert not done and "outside the project" in why
    assert (outside / "notes.txt").read_text(encoding="utf-8") == "mine\n"


def a_runtime_fix(file: str) -> RuntimeFix:
    return RuntimeFix(title="t", applicability=Applicability.SAFE, file=Path(file))


@pytest.mark.parametrize("named", ["script.md", "../decktalk-runtime.js"])
def test_a_runtime_fix_replaces_no_file_but_a_runtime_copy_inside_the_project(tmp_path: Path, named: str) -> None:
    """A runtime fix arrives as JSON, so it may not turn the engine's runtime into any other file."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "script.md").write_text("mine\n", encoding="utf-8")
    done, why = applied(a_machine(tmp_path), a_runtime_fix(named), root)
    assert not done and ("not a copy of the runtime" in why or "outside the project" in why)
    assert (root / "script.md").read_text(encoding="utf-8") == "mine\n"
    assert not (tmp_path / assets.RUNTIME_FILE).exists()


def test_a_runtime_copy_that_links_to_another_file_is_never_written_through(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "deck").mkdir(parents=True)
    (root / "script.md").write_text("mine\n", encoding="utf-8")
    try:
        (root / "deck" / assets.RUNTIME_FILE).symlink_to(root / "script.md")
    except OSError:  # pragma: no cover  (Windows makes a link only in developer mode)
        pytest.skip("this machine does not let an unprivileged user make a link")
    done, why = applied(a_machine(tmp_path), a_runtime_fix(f"deck/{assets.RUNTIME_FILE}"), root)
    assert not done and "not a copy of the runtime" in why
    assert (root / "script.md").read_text(encoding="utf-8") == "mine\n"


def test_a_fix_with_one_refused_edit_writes_none_of_its_edits(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    fix = EditFix(
        title="t",
        applicability=Applicability.SAFE,
        edits=(Edit(file=Path("inside.txt"), line=1, new="x"), Edit(file=Path("../outside.txt"), line=1, new="x")),
    )
    done, _why = applied(a_machine(tmp_path), fix, root)
    assert not done
    assert not (root / "inside.txt").exists()


def test_a_fix_whose_second_edit_is_stale_leaves_its_first_file_whole(tmp_path: Path) -> None:
    """Every edit is checked before any file is written, so a fix changes all of its files or none."""
    (tmp_path / "first.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("moved\n", encoding="utf-8")
    fix = EditFix(
        title="t",
        applicability=Applicability.SAFE,
        edits=(
            Edit(file=Path("first.txt"), line=1, old="one", new="changed"),
            Edit(file=Path("second.txt"), line=1, old="two", new="changed"),
        ),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert not done and "no longer reads" in why
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".fixing")) == []


def test_two_edits_to_one_file_are_made_in_order_and_written_once(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    fix = EditFix(
        title="t",
        applicability=Applicability.SAFE,
        edits=(
            Edit(file=Path("notes.txt"), line=1, old="one", new="first"),
            Edit(file=Path("notes.txt"), line=2, old="two", new="second"),
        ),
    )
    done, _why = applied(a_machine(tmp_path), fix, tmp_path)
    assert done
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first\nsecond\n"


def test_a_link_planted_where_a_fix_writes_its_draft_is_never_written_through(tmp_path: Path) -> None:
    """A project may carry any file, so the draft a fix writes beside its target is always a new one."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("mine\n", encoding="utf-8")
    try:
        (root / f".notes.txt.{os.getpid()}.fixing").symlink_to(elsewhere)
    except OSError:  # pragma: no cover  (Windows makes a link only in developer mode)
        pytest.skip("this machine does not let an unprivileged user make a link")
    with pytest.raises(FileExistsError):
        applied(a_machine(tmp_path), an_edit("notes.txt", line=1, old="one"), root)
    assert elsewhere.read_text(encoding="utf-8") == "mine\n"
    assert (root / "notes.txt").read_text(encoding="utf-8") == "one\n"


def test_a_line_that_no_longer_reads_what_the_fix_expected_is_left_alone(tmp_path: Path) -> None:
    """A file edited after its finding was raised has moved its lines, and line n is now another line."""
    (tmp_path / "notes.txt").write_text("one\ninserted\ntwo\n", encoding="utf-8")
    done, why = applied(a_machine(tmp_path), an_edit("notes.txt", line=2, old="two"), tmp_path)
    assert not done and "no longer reads" in why
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "one\ninserted\ntwo\n"


def test_a_line_past_the_end_of_the_file_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("one\n", encoding="utf-8")
    done, why = applied(a_machine(tmp_path), an_edit("notes.txt", line=5, old="five"), tmp_path)
    assert not done and "no longer reads" in why


def test_a_knob_a_fix_names_is_written_into_the_file_the_machine_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process may name another file, and the machine a host built by hand is the one being fixed."""
    here = a_machine(tmp_path)
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "the-process-file.toml"))
    fix = SettingFix(title="t", applicability=Applicability.SAFE, key="tools.ffmpeg", value="/usr/bin/ffmpeg")
    result = here.apply(a_finding(fix))
    assert result.fixes[0].applied, result.fixes[0].why
    assert "/usr/bin/ffmpeg" in here.config_path.read_text(encoding="utf-8")
    assert not (tmp_path / "the-process-file.toml").exists()


@pytest.mark.parametrize(("spelled", "allowed"), [("1", True), ("yes", True), ("0", False), ("false", False)])
def test_the_api_base_switch_is_read_once_into_a_field_of_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelled: str, allowed: bool
) -> None:
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "machine.toml"))
    monkeypatch.setenv("DECKTALK_ALLOW_ANY_API_BASE", spelled)
    assert Machine.from_environment().allow_any_api_base is allowed


def test_a_machine_built_by_hand_keeps_the_key_on_elevenlabs_whatever_the_process_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECKTALK_ALLOW_ANY_API_BASE", "1")
    assert a_machine(tmp_path, DECKTALK_ALLOW_ANY_API_BASE="1").allow_any_api_base is False


def test_a_subscriber_that_raises_becomes_a_line_and_never_stops_the_run(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []

    def angry(event: Event) -> None:
        if event.event == "log" and event.message == "one":
            raise RuntimeError("no")
        seen.append(event)

    with here.events.subscribe(angry), here.run() as run:
        run.note("one")
    assert any(isinstance(line, Log) and line.level is Level.ERROR for line in seen)


def test_init_writes_a_project_and_says_what_it_wrote(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    result = init(tmp_path / "demo", machine=here, skills=False)
    assert result.root == Path("demo")
    assert result.name == "demo" and result.example == "starter" and not result.skills
    assert Path("decktalk.toml") in result.written
    assert (tmp_path / "demo" / "decktalk.toml").exists()


def test_a_run_binds_the_toolchain_and_the_download_listener_for_its_own_length(tmp_path: Path) -> None:
    """A fetcher and a filter sit below the event stream, so a run binds both rather than being passed."""
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here.run():
        assert bound_tools().cache_dir == str(tmp_path / "cache")
        announce("ffmpeg", 10, 100)
    assert bound_tools().cache_dir == ""
    fetched = [line for line in seen if line.event == "fetch"]
    assert [(line.tool, line.bytes, line.total_bytes) for line in fetched] == [("ffmpeg", 10, 100)]
