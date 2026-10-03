"""This computer as one value: its toolchain, its voices, a host's machine and its own calls."""

from __future__ import annotations

import ast
import logging
import os
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from decktalk import machine as machine_module
from decktalk.errors import ErrorCode, InputError
from decktalk.events import Event, Level, RunLog, ToolFetch
from decktalk.findings import (
    Code,
)
from decktalk.machine import CHROMIUM, InstalledTool, Machine, Toolchain, init
from decktalk.media import ffmpeg as ffmpeg_module
from decktalk.media.environment import child_environment
from decktalk.media.ffmpeg import bound_tools
from decktalk.project import open as open_project
from decktalk.results import ApiKeyState, ClipResult, CueResult
from decktalk.settings import BY_ID, ToolsConfig
from decktalk.speech import SpeechContext
from decktalk.speech.sound import SOUNDS, SoundContext
from decktalk.stages.narrate.plan import speech_provider
from decktalk.toolchain import chromium_fetch
from decktalk.toolchain.announce import announce
from decktalk.toolchain.cache import cache_dir, standard_cache_dir, standard_data_dir
from support.fakes import BareBrowser, FakeChromium, FakeVoice
from support.logs import data_of
from support.paths import REPO
from support.runs import a_machine
from support.speech import NoSecrets

# ---- the one reader of the environment -------------------------------------------------------

SRC = REPO / "src" / "decktalk"

READERS = ("machine", "cli")
"""Where the process environment and the home directory may be read: the machine, and its first client."""


DRIVER_START = "toolchain/chromium_fetch.py"
"""The one module that names a variable in the process, because Playwright's driver copies the process's environment.

It names the machine's browser directory for the moment the driver starts and puts back what was
there, so what it reads is the host's value it is about to restore and never a setting.
"""

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
    assert offenders == {DRIVER_START}


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
    with here._run():
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
        ("win32", {"LOCALAPPDATA": "/local"}, "/local/decktalk/cache"),
        ("win32", {}, "home/AppData/Local/decktalk/cache"),
    ],
)
def test_the_standard_cache_is_worked_out_from_the_environment_the_machine_holds(
    platform: str, environ: dict[str, str], expected: str
) -> None:
    """On Windows the cache is a folder of its own beside the take store, so the store is never inside it."""
    assert standard_cache_dir(environ, Path("home"), platform) == Path(expected)


@pytest.mark.parametrize(
    ("platform", "environ", "expected"),
    [
        ("darwin", {"XDG_DATA_HOME": "/ignored"}, "home/Library/Application Support/decktalk"),
        ("linux", {}, "home/.local/share/decktalk"),
        ("linux", {"XDG_DATA_HOME": "/xdg"}, "/xdg/decktalk"),
        ("win32", {"LOCALAPPDATA": "/local", "APPDATA": "/roaming"}, "/local/decktalk"),
        ("win32", {}, "home/AppData/Local/decktalk"),
    ],
)
def test_the_standard_data_folder_is_kept_apart_from_the_cache(
    platform: str, environ: dict[str, str], expected: str
) -> None:
    """A cleaner empties a cache and a backup skips it, and the data folder holds the take store's paid records."""
    data = standard_data_dir(environ, Path("home"), platform)
    assert data == Path(expected)
    assert not data.is_relative_to(standard_cache_dir(environ, Path("home"), platform))


def test_a_machine_keeps_its_take_store_in_the_data_folder_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DECKTALK_MACHINE_FILE", str(tmp_path / "none.toml"))
    here = Machine.from_environment()
    assert here.store == machine_module.standard_data_dir(dict(os.environ), Path.home()) / "takes"
    assert not here.store.is_relative_to(standard_data_dir(dict(os.environ), Path.home())), "the suite wrote home"


def test_a_host_machine_keeps_no_take_store_unless_it_names_one(tmp_path: Path) -> None:
    """A host runs other people's projects, so a store that crosses them is a choice it makes."""
    assert a_host(tmp_path).store is None
    assert a_host(tmp_path, store_dir=tmp_path / "store").store == tmp_path / "store"


def test_a_take_store_inside_the_tool_cache_is_refused_at_load(tmp_path: Path) -> None:
    """Anyone may empty the tool cache, and the take store holds voiced takes."""
    with pytest.raises(InputError, match="tool cache"):
        a_host(tmp_path, store_dir=tmp_path / "host" / "cache" / "takes")
    with pytest.raises(InputError, match="tool cache"):
        a_host(tmp_path, overrides=(f"narration.store_dir={tmp_path / 'host' / 'cache' / 'takes'}",))


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


def test_a_fetch_the_network_refuses_is_a_tool_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`decktalk install` offline is the machine's problem, so it must never read as a bug to report."""

    def offline(**_: object) -> tuple[str, str]:
        raise OSError("offline")

    monkeypatch.setattr(machine_module, "fetch_ffmpeg", offline)
    with pytest.raises(machine_module.ToolError, match="offline") as refused:
        Toolchain().fetched()
    assert refused.value.code is ErrorCode.TOOL
    assert "network access" in (refused.value.hint or "")


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
    with here._run() as run:
        bound = ffmpeg_module.TOOLS.get()
        assert bound is not None
        assert bound.cancel is run.cancel
        assert bound.paths() == (str(pair[0]), str(pair[1]))


def test_a_browser_is_built_from_the_machines_environment_and_never_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "the-process-language")
    here = a_machine(tmp_path, LANG="the-machines-language", ELEVENLABS_API_KEY="sk-not-a-key")
    with here._run():
        seen = child_environment()
    assert seen["LANG"] == "the-machines-language"
    assert "ELEVENLABS_API_KEY" not in seen


# ---- the machine's own calls -----------------------------------------------------------------


def test_the_credential_is_asked_about_and_never_read(tmp_path: Path) -> None:
    assert a_machine(tmp_path).api_key_state is ApiKeyState.MISSING
    assert a_machine(tmp_path, ELEVENLABS_API_KEY="sk_real").api_key_state is ApiKeyState.SET


def test_the_credential_asked_about_is_the_one_the_voice_declares(tmp_path: Path) -> None:
    """A voice DeckTalk does not ship declares no variable, so nothing is reported missing for it."""
    assert a_machine(tmp_path, DECKTALK_VOICE_PROVIDER="house").api_key_state is ApiKeyState.NOT_NEEDED
    assert a_machine(tmp_path, DECKTALK_VOICE_PROVIDER="elevenlabs").api_key_state is ApiKeyState.MISSING


def test_from_environment_is_the_one_reading_of_this_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DECKTALK_MACHINE_FILE", str(tmp_path / "none.toml"))
    here = Machine.from_environment()
    assert here.cwd == Path.cwd()
    assert here.config_path == tmp_path / "none.toml"
    assert here.tables == {}


# ---- a machine a host builds -----------------------------------------------------------------

HOST_SECRETS = {"ELEVENLABS_API_KEY": "sk-host-owned", "DECKTALK_VOICE_ID": "house-voice"}
"""What a host hands its voice job: the key and the voice, and nothing else from its own process."""


def a_host(tmp_path: Path, **choices: object) -> Machine:
    """A machine a host built from values it chose, with the voice job's two variables by default."""
    values: dict[str, Any] = {
        "environ": HOST_SECRETS,
        "config_path": tmp_path / "host" / "machine.toml",
        "cwd": tmp_path,
        "cache_dir": tmp_path / "host" / "cache",
        **choices,
    }
    return Machine.of(**values)


def test_a_credential_a_host_hands_its_machine_never_reaches_a_line(tmp_path: Path) -> None:
    """A host passes its key in the machine's environment rather than in `.env`, and no `Secret` wraps it there."""
    canary = "sk_host_canary_5d0c2a9e61"
    here = a_host(tmp_path, environ={"ELEVENLABS_API_KEY": canary, "HOST_DB_PASSWORD": "pw_host_canary_8e4b"})
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run:
        run.note(f"sent {canary} with pw_host_canary_8e4b")
        logging.getLogger("decktalk.speech.http").debug("headers %s", {"xi-api-key": canary})
    said = "".join(line.model_dump_json() for line in seen)
    assert canary not in said and "pw_host_canary_8e4b" not in said
    assert "<secret ELEVENLABS_API_KEY>" in said and "<secret HOST_DB_PASSWORD>" in said


def test_what_reading_the_machine_noticed_is_a_warning_on_every_run(tmp_path: Path) -> None:
    """A misspelled variable or machine key in a log line never reached `--json`, so it is a line of the run."""
    config = tmp_path / "host" / "machine.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[video]\npresett = 'veryfast'\n", encoding="utf-8")
    here = a_host(tmp_path, environ={"DECKTALK_VIDEO_CRV": "20"}, config_path=config)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run():
        pass
    warned = [line.message for line in seen if isinstance(line, RunLog) and line.level is Level.WARNING]
    assert tuple(warned) == here.notes
    assert [("CRV" in note, "presett" in note) for note in warned] == [(True, False), (False, True)]


def test_tools_that_name_half_a_build_are_a_note_that_names_the_key_rather_than_a_missing_encoder(
    tmp_path: Path,
) -> None:
    """`install` cannot mend a key that names one half, so `doctor` must say which key to mend."""
    config = tmp_path / "host" / "machine.toml"
    config.parent.mkdir(parents=True)
    (tmp_path / "ffmpeg").write_bytes(b"")
    config.write_text(f"[tools]\nffmpeg = '{(tmp_path / 'ffmpeg').as_posix()}'\n", encoding="utf-8")
    here = a_host(tmp_path, config_path=config)
    assert here.toolchain.ffmpeg is None
    assert any("tools.ffprobe is not set" in note for note in here.notes)


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
    monkeypatch.setenv("DECKTALK_MACHINE_FILE", str(tmp_path / "the-process-file.toml"))
    monkeypatch.setenv("DECKTALK_TOOLS_TIMEOUT_SECONDS", "11")
    here = a_host(tmp_path)
    assert here.environ == HOST_SECRETS
    assert here.config_path == tmp_path / "host" / "machine.toml"
    assert here.cache_dir == tmp_path / "host" / "cache"
    assert here.dotenv is False
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
    contexts: list[SpeechContext] = []

    def build(context: SpeechContext) -> FakeVoice:
        contexts.append(context)
        return voice

    here = a_host(tmp_path, speech_providers={"elevenlabs": build})
    project = open_project(a_starter(tmp_path, here), machine=here)
    result = project.narrate(spend=True)
    assert result.ok
    assert voice.requests, "the host's voice was never asked for a take"
    assert {request.voice_id for request in voice.requests} == {"house-voice"}
    assert {context.base_url for context in contexts} == {BY_ID["elevenlabs.base_url"].default}


@pytest.mark.usefixtures("no_network")
def test_a_host_machine_answers_no_voice_its_host_left_out(tmp_path: Path) -> None:
    here = a_host(tmp_path, speech_providers={"house": lambda _context: FakeVoice()})
    with here._run() as run, pytest.raises(InputError, match="not a voice this machine answers for"):
        run.voices.provider("elevenlabs", a_context())


def test_two_machines_in_one_process_answer_with_their_own_voices(tmp_path: Path) -> None:
    first, second = FakeVoice(name="first"), FakeVoice(name="second")
    one = a_host(tmp_path, speech_providers={"elevenlabs": lambda _context: first})
    two = a_host(tmp_path, speech_providers={"elevenlabs": lambda _context: second})
    with one._run() as outer:
        with two._run() as inner:
            assert inner.voices.provider("elevenlabs", a_context()) is second
        assert outer.voices.provider("elevenlabs", a_context()) is first


def test_a_thread_started_without_the_runs_context_answers_with_the_hosts_voices(tmp_path: Path) -> None:
    """A worker pool that never copied the context still asks its run, so it cannot reach the shipped voice."""
    house = FakeVoice(name="house")
    here = a_host(tmp_path, speech_providers={"elevenlabs": lambda _context: house})
    project = open_project(a_starter(tmp_path, here), machine=here)
    answered: list[object] = []
    with here._run() as run:
        worker = threading.Thread(target=lambda: answered.append(speech_provider(run, project._inputs)))
        worker.start()
        worker.join()
    assert answered == [house]


def test_the_machines_retries_are_stamped_on_every_voice_it_builds(tmp_path: Path) -> None:
    """A busy voice is asked again as often as the machine says, which no project may change."""
    seen: list[SpeechContext] = []
    here = a_host(tmp_path, speech_providers={"elevenlabs": seen.append}, overrides=("narration.retries=5",))
    with here._run() as run:
        run.voices.provider("elevenlabs", a_context())
    assert seen[0].retries == 5


def test_a_machine_whose_host_gave_no_table_answers_with_the_shipped_sound_providers(tmp_path: Path) -> None:
    assert sorted(a_host(tmp_path).sounds.factories) == sorted(SOUNDS)


def test_a_host_that_gave_its_voices_and_no_sounds_reaches_no_shipped_sound_provider(tmp_path: Path) -> None:
    """A machine built with a fake voice must not reach the real sound service by a table its host left out."""
    here = a_host(tmp_path, speech_providers={"elevenlabs": lambda _context: FakeVoice()})
    assert dict(here.sounds.factories) == {}


def test_the_sound_providers_a_host_supplies_are_the_ones_every_run_carries(tmp_path: Path) -> None:
    seen: list[SoundContext] = []
    here = a_host(tmp_path, sound_providers={"house": seen.append}, overrides=("narration.retries=5",))
    with here._run() as run:
        run.sounds.provider("house", SoundContext(secrets=NoSecrets(), base_url="", timeout_seconds=1))
    assert seen[0].retries == 5


def keyed(voice: FakeVoice) -> Callable[[SpeechContext], FakeVoice]:
    """A provider that asks its project's secrets for the key before it answers, as the shipped one does."""

    def build(context: SpeechContext) -> FakeVoice:
        context.secrets.require("ELEVENLABS_API_KEY")
        return voice

    return build


def named_in_the_file(root: Path, voice_id: str) -> None:
    """Name the voice in the starter's own `[voice] id`, which is where a project commits it."""
    toml = root / "decktalk.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('id = ""', f'id = "{voice_id}"'), encoding="utf-8")


@pytest.mark.usefixtures("no_network")
def test_a_tenants_env_file_is_never_read_under_a_host_machine(tmp_path: Path) -> None:
    """An upload could carry a `.env`, and the key it names would then pay for the tenant's take."""
    here = a_host(tmp_path, environ={}, speech_providers={"elevenlabs": keyed(FakeVoice())})
    root = a_starter(tmp_path, here)
    named_in_the_file(root, "tenant-voice")
    (root / ".env").write_text("ELEVENLABS_API_KEY=sk-tenant\n", encoding="utf-8")
    project = open_project(root, machine=here)
    with pytest.raises(InputError, match="ELEVENLABS_API_KEY is not set"):
        project.narrate(spend=True)


@pytest.mark.usefixtures("no_network", "fake_ffmpeg")
def test_the_authors_own_env_file_is_read_under_a_machine_that_allows_it(tmp_path: Path) -> None:
    voice = FakeVoice()
    here = a_host(tmp_path, environ={}, speech_providers={"elevenlabs": keyed(voice)}, dotenv=True)
    root = a_starter(tmp_path, here)
    named_in_the_file(root, "author-voice")
    (root / ".env").write_text("ELEVENLABS_API_KEY=sk-author\n", encoding="utf-8")
    assert open_project(root, machine=here).narrate(spend=True).ok
    assert {request.voice_id for request in voice.requests} == {"author-voice"}


def a_context() -> SpeechContext:
    """A context no machine stamped, which is what a stage builds from its project's values."""
    return SpeechContext(
        secrets=NoSecrets(),
        base_url="https://api.elevenlabs.io/v1",
        context_chars=1,
        speech_timeout_seconds=1,
    )


@pytest.mark.parametrize("key", ["tools.cache_dir", "narration.store_dir"])
def test_a_relative_machine_folder_is_refused_at_load_naming_the_machine_file(tmp_path: Path, key: str) -> None:
    """A machine folder has no project to be relative to, so a relative one would move with the working directory."""
    table, name = key.split(".")
    config = tmp_path / "host" / "machine.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'[{table}]\n{name} = "relative/folder"\n', encoding="utf-8")
    with pytest.raises(InputError, match="relative") as refused:
        a_host(tmp_path)
    assert str(config) in str(refused.value)


def test_a_tool_cache_that_starts_with_a_tilde_is_under_the_machines_home(tmp_path: Path) -> None:
    here = a_host(tmp_path, environ={"HOME": str(tmp_path / "home")}, overrides=("tools.cache_dir=~/tools",))
    assert here.cache_dir == tmp_path / "home" / "tools"


def test_an_override_reaches_the_machine_by_its_own_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every pair reaches both the machine and the project, and each takes the keys it owns."""
    monkeypatch.setenv("DECKTALK_MACHINE_FILE", str(tmp_path / "none.toml"))
    here = Machine.from_environment(overrides=(f"tools.cache_dir={tmp_path / 'elsewhere'}",))
    assert here.cache_dir == tmp_path / "elsewhere"


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> None:
    """A browser row that answers without launching Chromium, which the rows below have no need of."""
    monkeypatch.setattr(
        Machine,
        "_browser_row",
        lambda self: machine_module.InstalledTool(tool="chromium", version="140", path=None, fetched=False),
    )


@pytest.mark.usefixtures("launched")
def test_doctor_reports_every_component_and_fetches_nothing(tmp_path: Path) -> None:
    result = a_machine(tmp_path).doctor()
    assert [tool.tool for tool in result.tools] == ["chromium", "ffmpeg", "ffprobe", "katex"]
    assert not result.ok  # this machine has no encoder, which a build needs
    assert {found.code for found in result.findings} == {Code.FILE_MISSING}
    assert result.bias_ms is None  # the bias is measured only when a caller asks


def test_the_browser_row_names_where_its_chromium_lives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A person told the browser is there still has to find it, so doctor names its path."""
    executable = tmp_path / "chrome"
    executable.write_bytes(b"")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", FakeChromium(executable).started())
    row = a_machine(tmp_path)._browser_row()
    assert (row.version, row.path) == (BareBrowser.version, executable)


def test_a_browser_that_will_not_launch_is_a_row_and_a_warning_that_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The row can only say there is no browser, so the first line of the launch error goes on the log."""

    executable = tmp_path / "chrome"
    executable.write_bytes(b"")
    refused = FakeChromium(executable, refusal="Executable doesn't exist\nat /nowhere/chrome")
    monkeypatch.setattr("playwright.sync_api.sync_playwright", refused.started())
    row = a_machine(tmp_path)._browser_row()
    assert (row.version, row.path) == (None, None)
    [said] = [record for record in caplog.records if record.name == "decktalk.machine"]
    assert (said.levelname, said.getMessage()) == ("WARNING", "Chromium did not launch.")
    assert data_of(said) == {"reason": "Executable doesn't exist"}


@pytest.mark.usefixtures("launched")
def test_a_measured_doctor_reports_the_number_and_keeps_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No stage reads the bias, so it is read off the report and never written into a settings file."""
    here = a_machine(tmp_path)
    monkeypatch.setattr(
        machine_module,
        "import_module",
        lambda _name: SimpleNamespace(measure_presentation_bias=lambda: 12.5),
    )
    assert here.doctor(measure=True).bias_ms == 12.5
    assert not here.config_path.exists()


def rows(*answers: InstalledTool) -> Callable[[Machine], InstalledTool]:
    """`_browser_row` answering each call with the next of `answers`, the last one for every call after."""
    queue = list(answers)
    return lambda _self: queue.pop(0) if len(queue) > 1 else queue[0]


ABSENT = InstalledTool(tool=CHROMIUM)
"""The browser row of a machine whose Chromium does not launch."""

PRESENT = InstalledTool(tool=CHROMIUM, version="141.0.1", path=None, fetched=False)
"""The browser row of a machine whose Chromium launches."""


def faked_installer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[list[str], dict[str, str]]]:
    """Stand in for `playwright install` and the ffmpeg download, keeping each installer command and its environment."""
    ran: list[tuple[list[str], dict[str, str]]] = []

    def run(cmd: list[str], *, env: dict[str, str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        ran.append((list(cmd), dict(env)))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(machine_module.chromium_fetch.subprocess, "run", run)
    monkeypatch.setattr(
        machine_module, "fetch_ffmpeg", lambda **_: (str(tmp_path / "ffmpeg"), str(tmp_path / "ffprobe"))
    )
    return ran


def test_install_fetches_the_browser_and_the_encoder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran = faked_installer(monkeypatch, tmp_path)
    monkeypatch.setattr(Machine, "_browser_row", rows(ABSENT, PRESENT))
    result = a_machine(tmp_path).install()
    assert len(ran) == 1
    assert [tool.tool for tool in result.tools] == ["chromium", "ffmpeg", "ffprobe"]
    assert result.tools[1].path == tmp_path / "ffmpeg"
    assert result.ok


def test_install_puts_chromium_in_the_tool_cache_beside_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`tools.cache_dir` moved ffmpeg alone, and Chromium went wherever Playwright kept it, so a job
    had to cache six directories. The installer is told the one cache, and `cache` names it."""
    ran = faked_installer(monkeypatch, tmp_path)
    monkeypatch.setattr(Machine, "_browser_row", rows(ABSENT, PRESENT))
    result = a_machine(tmp_path).install()
    [(_cmd, env)] = ran
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "cache" / "ms-playwright")
    assert result.cache == tmp_path / "cache"


def test_install_says_fetched_for_a_browser_it_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The row said version null, so `install` printed the browser as missing while `doctor` run
    straight afterwards read the real version off the very browser the fetch had left behind."""
    ran = faked_installer(monkeypatch, tmp_path)
    monkeypatch.setattr(Machine, "_browser_row", rows(ABSENT, PRESENT))
    (browser, *_rest) = a_machine(tmp_path).install().tools
    assert (browser.version, browser.fetched) == ("141.0.1", True)
    [(cmd, _env)] = ran
    assert (chromium_fetch.WITH_DEPS in cmd) == sys.platform.startswith("linux"), cmd


def test_install_says_a_browser_already_there_was_not_fetched_and_fetches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guide promises a second run names what was already there, and a warm run said fetched.
    A browser that launches has its system libraries too, so neither is fetched again."""
    ran = faked_installer(monkeypatch, tmp_path)
    monkeypatch.setattr(Machine, "_browser_row", rows(PRESENT))
    (browser, *_rest) = a_machine(tmp_path).install().tools
    assert (browser.version, browser.fetched) == ("141.0.1", False)
    assert ran == [], f"a browser that launches was fetched again: {ran}"


def test_doctor_names_one_cache_and_its_driver_looks_inside_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`cache` is the one directory a job keeps, so the browser `doctor` reports lives inside it."""
    executable = tmp_path / "cache" / "ms-playwright" / "chromium-1243" / "chrome"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    chromium = FakeChromium(executable)
    monkeypatch.setattr("playwright.sync_api.sync_playwright", chromium.started())
    result = a_machine(tmp_path).doctor()
    assert result.cache == tmp_path / "cache"
    assert chromium.looked_in == [str(result.cache / "ms-playwright")]
    [browser] = [tool for tool in result.tools if tool.tool == CHROMIUM]
    assert browser.path is not None and browser.path.is_relative_to(result.cache)


def test_a_machine_built_by_hand_keeps_the_key_on_elevenlabs_whatever_the_process_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base URL is a machine key, so only the environment the host chose can move it."""
    monkeypatch.setenv(BY_ID["elevenlabs.base_url"].environment, "http://127.0.0.1:9/v1")
    here = a_host(tmp_path)
    project = open_project(a_starter(tmp_path, here), machine=here)
    assert project.settings.elevenlabs.base_url == BY_ID["elevenlabs.base_url"].default


def test_a_subscriber_that_raises_becomes_a_line_and_never_stops_the_run(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []

    def angry(event: Event) -> None:
        if isinstance(event, RunLog) and event.message == "one":
            raise RuntimeError("no")
        seen.append(event)

    with here.events.subscribe(angry), here._run() as run:
        run.note("one")
    assert any(isinstance(line, RunLog) and line.level is Level.ERROR for line in seen)


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
    with here.events.subscribe(seen.append), here._run():
        assert bound_tools().cache_dir == str(tmp_path / "cache")
        announce("ffmpeg", 10, 100)
    assert bound_tools().cache_dir == ""
    fetched = [line for line in seen if isinstance(line, ToolFetch)]
    assert [(line.tool, line.bytes, line.total_bytes) for line in fetched] == [("ffmpeg", 10, 100)]


def test_a_run_fills_how_long_it_took_and_never_how_long_its_media_runs(tmp_path: Path) -> None:
    """`elapsed_seconds` is wall time and a clip's `seconds` is media length, so a run fills only the first."""
    with a_machine(tmp_path)._run() as run:
        cued = run.result(CueResult, sections=())
        assert cued.elapsed_seconds >= 0
        assert "seconds" not in CueResult.model_fields
        with pytest.raises(ValidationError, match=r"seconds\n\s+Field required"):
            run.result(
                ClipResult,
                section=1,
                file=Path("build/clips/01.mp4"),
                words=Path("build/clips/01.words.json"),
                start=0.0,
                end=1.0,
                hold_seconds=0.0,
                gain_db=0.0,
                estimated=False,
            )
