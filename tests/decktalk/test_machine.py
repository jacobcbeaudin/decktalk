"""This computer as one value, the run every call opens on it, and the gate a spend passes."""

from __future__ import annotations

import ast
import logging
import socket
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from decktalk import machine as machine_module
from decktalk.errors import ApprovalRequired, Cancel, Cancelled, ErrorCode, InputError
from decktalk.events import Event, Fetch, Level, Log, RunDone, RunStart, StageDone, StageStart
from decktalk.findings import (
    Applicability,
    Certainty,
    Code,
    CommandFix,
    Edit,
    EditFix,
    Finding,
    Location,
    SettingFix,
)
from decktalk.machine import (
    CHROMIUM,
    FIX_TIMEOUT_SECONDS,
    InstalledTool,
    Machine,
    Threshold,
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
from decktalk.results import Billing, FixOutcome, Layer, Scope, StatusResult
from decktalk.settings import BY_ID, ToolsConfig
from decktalk.speech import VoiceContext
from decktalk.speech.sound import SOUNDS, SoundContext
from decktalk.stages.narrate.plan import speech_provider
from decktalk.toolchain import chromium_fetch, command_line
from decktalk.toolchain.announce import announce
from decktalk.toolchain.cache import cache_dir, standard_cache_dir
from support.fakes import BareBrowser, FakeChromium, FakeVoice
from support.links import link
from support.logs import data_of
from support.paths import REPO
from support.runs import a_machine
from support.speech import NoSecrets
from support.spends import a_spend

# ---- the one reader of the environment -------------------------------------------------------

SRC = REPO / "src" / "decktalk"

READERS = ("machine.py", "cli")
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


# ---- the run ------------------------------------------------------------------------------


def test_every_line_of_a_run_carries_its_run_and_counts_from_zero(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run:
        run.note("one")
        run.note("two")
    assert [line.event for line in seen] == ["run.start", "log", "log", "run.done"]
    assert {line.run for line in seen} == {run.id}
    assert [line.seq for line in seen] == [0, 1, 2, 3]


def test_a_run_that_fails_closes_as_failed_and_lets_the_failure_through(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), pytest.raises(ZeroDivisionError), here._run():
        raise ZeroDivisionError
    assert isinstance(seen[-1], RunDone) and seen[-1].outcome is Outcome.FAILED
    assert seen[-1].error is not None and seen[-1].error.code is ErrorCode.INTERNAL
    assert seen[-1].error.message == "ZeroDivisionError: "


def test_a_refused_run_names_its_refusal_on_its_last_line(tmp_path: Path) -> None:
    """The events file is read after the process is gone, so its last line has to say why the run failed."""
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    with pytest.raises(InputError), here._run(root=tmp_path, events_dir=events) as run:
        raise InputError("cues.json is not JSON.", hint="Fix cues.json.")
    last = RunDone.model_validate_json((events / f"{run.id}.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last.outcome is Outcome.FAILED
    assert last.error is not None
    assert (last.error.code, last.error.message, last.error.hint) == (
        ErrorCode.INPUT,
        "cues.json is not JSON.",
        "Fix cues.json.",
    )


def test_a_run_says_on_its_last_line_how_many_lines_its_bounded_file_left_out(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    with here._run(root=tmp_path, events_dir=events, max_bytes=1) as run:
        for number in range(5):
            logging.getLogger("decktalk.media.ffmpeg").debug("call %d", number)
    lines = (events / f"{run.id}.jsonl").read_text(encoding="utf-8").splitlines()
    last = RunDone.model_validate_json(lines[-1])
    assert last.dropped == 5 and [line for line in lines if '"log"' in line] == []


def test_a_finished_run_carries_no_error(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run():
        pass
    assert isinstance(seen[-1], RunDone) and seen[-1].error is None


@pytest.mark.parametrize("stop", [Cancelled("The caller stopped this run."), KeyboardInterrupt()])
def test_a_cancelled_or_interrupted_run_ends_as_stopped_rather_than_failed(tmp_path: Path, stop: BaseException) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with (
        here.events.subscribe(seen.append),
        pytest.raises(type(stop)),
        here._run() as run,
        run.section(Stage.RECORD, 1),
    ):
        raise stop
    section = next(line for line in seen if line.event == "section.done")
    assert getattr(section, "outcome", None) is Outcome.STOPPED
    assert isinstance(seen[-1], RunDone) and seen[-1].outcome is Outcome.STOPPED
    assert seen[-1].error is not None and seen[-1].error.code is ErrorCode.CANCELLED


def test_a_run_with_a_project_writes_its_own_file_and_says_where(tmp_path: Path) -> None:
    """One file per run, so a watch loop beside a build by hand cannot overwrite the other's lines."""
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    with here._run(root=tmp_path, events_dir=events) as run:
        run.note("hello")
    written = events / f"{run.id}.jsonl"
    assert written.exists()
    assert "hello" in written.read_text(encoding="utf-8")


def test_a_run_says_the_path_its_own_lines_are_going_to(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run(root=tmp_path, events_dir=tmp_path / "build" / "events") as run:
        pass
    assert isinstance(seen[0], RunStart)
    assert seen[0].events_path == Path(f"build/events/{run.id}.jsonl")


def test_a_machine_run_holds_no_project_so_it_writes_no_file(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run():
        pass
    assert isinstance(seen[0], RunStart) and seen[0].events_path is None


def test_the_oldest_event_files_are_pruned_before_a_new_run_opens(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    events = tmp_path / "build" / "events"
    events.mkdir(parents=True)
    for name in ("a", "b", "c"):
        (events / f"{name}.jsonl").write_text("{}\n", encoding="utf-8")
    with here._run(root=tmp_path, events_dir=events, keep_runs=1):
        pass
    assert len(list(events.glob("*.jsonl"))) == 2  # the one kept, and this run's own


def test_a_stage_opens_and_closes_on_the_stream(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run, run.stage(Stage.NARRATE, index=1, count=2):
        pass
    started = next(line for line in seen if isinstance(line, StageStart))
    done = next(line for line in seen if isinstance(line, StageDone))
    assert (started.stage, started.index, started.count) == (Stage.NARRATE, 1, 2)
    assert done.outcome is Outcome.OK


def test_a_stage_that_raises_closes_as_failed(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run() as run:
        with pytest.raises(ValueError, match="no"), run.stage(Stage.RECORD):
            raise ValueError("no")
    assert next(line for line in seen if isinstance(line, StageDone)).outcome is Outcome.FAILED


def test_a_cancelled_run_stops_at_the_next_section_boundary(tmp_path: Path) -> None:
    """A stage checks between sections, so a cancelled run leaves whole artifacts rather than half of one."""
    here = a_machine(tmp_path)
    cancel = Cancel()
    with here._run(cancel=cancel) as run:
        with run.section(Stage.RECORD, 1):
            cancel.cancel()
        with pytest.raises(Cancelled), run.section(Stage.RECORD, 2):
            pass


def test_a_result_carries_the_run_the_judgements_and_the_files(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run(root=tmp_path) as run:
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
    with here._run() as run:
        run.found(unsure)
        assert run.result(StatusResult, name="t", script=Path("s"), cues=Path("c"), sections=()).ok
        run.found(certain)
        assert not run.result(StatusResult, name="t", script=Path("s"), cues=Path("c"), sections=()).ok


CERTAIN = Finding(code=Code.CUE_UNRESOLVED, message="x", location=Location(where="cues.json"))
UNSURE = Finding(code=Code.PAGE_SWAP_APART, message="y", location=Location(where="deck/index.html"))


@pytest.mark.parametrize(
    ("threshold", "found", "passes"),
    [
        (Threshold(), (UNSURE,), True),
        (Threshold(), (CERTAIN,), False),
        (Threshold(stop_on=Certainty.UNCERTAIN), (UNSURE,), False),
        (Threshold(stop_on=None), (CERTAIN, UNSURE), True),
        (Threshold(allow=frozenset({Code.CUE_UNRESOLVED})), (CERTAIN,), True),
        (Threshold(stop_on=Certainty.UNCERTAIN, allow=frozenset({Code.CUE_UNRESOLVED})), (CERTAIN, UNSURE), False),
    ],
    ids=["unsure", "sure", "any-unsure", "off", "allowed", "allowed-but-any"],
)
def test_a_result_is_ok_exactly_when_nothing_reaches_the_threshold_it_was_given(
    tmp_path: Path, threshold: Threshold, found: tuple[Finding, ...], passes: bool
) -> None:
    assert CERTAIN.certainty is Certainty.CERTAIN and UNSURE.certainty is Certainty.UNCERTAIN
    with a_machine(tmp_path)._run() as run:
        result = run.result(
            StatusResult, findings=found, threshold=threshold, name="t", script=Path("s"), cues=Path("c"), sections=()
        )
    assert result.ok is passes
    assert threshold.fails(found) is not passes


# ---- the spend gate -------------------------------------------------------------------------


def test_nothing_is_bought_unless_the_run_may_spend(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run() as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(a_spend(0.42, 0.42))
    assert refused.value.code is ErrorCode.APPROVAL
    assert "--spend" in (refused.value.hint or "")
    assert "--no-spend" in (refused.value.hint or "")


@pytest.mark.parametrize("max_cost", [None, 0.0])
def test_a_free_voice_is_never_asked_for_approval_even_by_a_run_that_may_not_spend(
    tmp_path: Path, max_cost: float | None
) -> None:
    """Spend gates money, so a voice that declares it bills nothing passes the gate whatever the run may spend."""
    here = a_machine(tmp_path)
    free = a_spend(0.0, 0.0, billing=Billing.FREE)
    assert free.free
    with here._run(spend=False, max_cost=max_cost) as run:
        assert run.approve(free) == free


def test_a_run_that_may_not_spend_refuses_a_price_of_zero_from_a_voice_that_bills(tmp_path: Path) -> None:
    """A zero price on a voice that bills is an estimate, and money is the gate's question."""
    here = a_machine(tmp_path)
    with here._run() as run, pytest.raises(ApprovalRequired):
        run.approve(a_spend(0.0, 0.0))


def test_free_is_what_the_voice_declares_and_never_a_rate_of_zero() -> None:
    """A zero rate on a voice that bills is somebody's statement about their plan, stated or not."""
    assert not a_spend(0.0, 0.0, layer=Layer.DEFAULT).model_copy(update={"price_per_1000_characters": 0.0}).free
    assert not a_spend(0.0, 0.0).model_copy(update={"price_per_1000_characters": 0.0}).free
    assert a_spend(0.0, 0.0, billing=Billing.FREE, layer=Layer.DEFAULT).free


def test_a_cap_lets_a_free_voice_through_with_no_rate_stated(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    free = a_spend(0.0, 0.0, billing=Billing.FREE, layer=Layer.DEFAULT)
    with here._run(spend=True, max_cost=0.0) as run:
        assert run.approve(free) == free


def test_a_cap_is_refused_for_a_voice_that_declares_no_bill_with_what_to_declare(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    undeclared = a_spend(0.0, 0.0, billing=Billing.UNDECLARED, layer=Layer.DEFAULT)
    with here._run(spend=True, max_cost=1.0) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(undeclared)
    assert "declares no bill" in str(refused.value)
    assert "per character, per second or free" in (refused.value.hint or "")


def test_a_refusal_states_the_price_in_the_one_sentence_every_surface_uses(tmp_path: Path) -> None:
    """A run whose certain part is zero must not be said to spend $0.00, which its ceiling contradicts."""
    here = a_machine(tmp_path)
    unmatched = a_spend(0.0, 0.3)
    with here._run() as run, pytest.raises(ApprovalRequired) as unvoiced:
        run.approve(unmatched)
    with here._run(spend=True, max_cost=0.1) as run, pytest.raises(ApprovalRequired) as capped:
        run.approve(unmatched)
    for refused in (unvoiced, capped):
        assert str(refused.value).startswith(unmatched.sentence)
        assert "$0.00" not in str(refused.value)


def test_a_paid_run_inside_its_ceiling_goes_through(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run(spend=True, max_cost=1.0) as run:
        assert run.approve(a_spend(0.42, 0.9)).dollars == 0.42


def test_the_ceiling_is_compared_against_the_most_a_run_can_cost(tmp_path: Path) -> None:
    """Credits go one request at a time, so a cap that stopped a run halfway would be a lie."""
    here = a_machine(tmp_path)
    with here._run(spend=True, max_cost=0.5) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(a_spend(0.42, 0.9))
    assert "0.90" in str(refused.value)


def test_the_ceiling_caps_everything_one_run_approves_and_not_each_approval(tmp_path: Path) -> None:
    """A build approves its takes and then its sounds, and `--max-cost` is the most the whole run may cost."""
    here = a_machine(tmp_path)
    sounds = a_spend(0.9, 0.9, billing=Billing.PER_SECOND)
    with here._run(spend=True, max_cost=1.0) as run:
        run.approve(a_spend(0.9, 0.9))
        with pytest.raises(ApprovalRequired) as refused:
            run.approve(sounds)
    said = str(refused.value)
    assert said.startswith(sounds.sentence)
    assert "$1.80" in said and "$1.00" in said, said
    assert "kept" in said, "a run refused partway says what it already bought stays"
    with here._run(spend=True, max_cost=1.0) as again:
        assert again.approve(sounds) == sounds, "the cap is per run, so the next run starts from nothing"


def test_a_cap_is_refused_while_nobody_has_stated_the_price(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    with here._run(spend=True, max_cost=1.0) as run, pytest.raises(ApprovalRequired) as refused:
        run.approve(a_spend(0.42, 0.9, layer=Layer.DEFAULT))
    assert "elevenlabs.price_per_1000_characters" in (refused.value.hint or "")


def test_every_priced_request_reaches_the_stream_before_it_is_judged(tmp_path: Path) -> None:
    here = a_machine(tmp_path)
    seen: list[Event] = []
    with here.events.subscribe(seen.append), here._run(spend=True) as run:
        run.approve(a_spend(0.42, 0.42))
    assert [line.event for line in seen if line.event == "spend"] == ["spend"]


# ---- the machine's own calls -----------------------------------------------------------------


def test_the_credential_is_asked_about_and_never_read(tmp_path: Path) -> None:
    assert not a_machine(tmp_path).voice_key
    assert a_machine(tmp_path, ELEVENLABS_API_KEY="sk_real").voice_key


def test_the_credential_asked_about_is_the_one_the_voice_declares(tmp_path: Path) -> None:
    """A voice DeckTalk does not ship declares no variable, so nothing is reported missing for it."""
    assert a_machine(tmp_path, DECKTALK_VOICE_PROVIDER="house").voice_key
    assert not a_machine(tmp_path, DECKTALK_VOICE_PROVIDER="elevenlabs").voice_key


def test_from_environment_is_the_one_reading_of_this_machine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "none.toml"))
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
    warned = [line.message for line in seen if isinstance(line, Log) and line.level is Level.WARNING]
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
    result = project.narrate(spend=True)
    assert result.ok
    assert voice.requests, "the host's voice was never asked for a take"
    assert {request.voice_id for request in voice.requests} == {"house-voice"}
    assert [context.allow_any_api_base for context in contexts] == [False] * len(contexts)


@pytest.mark.usefixtures("no_network")
def test_a_host_machine_answers_no_voice_its_host_left_out(tmp_path: Path) -> None:
    here = a_host(tmp_path, providers={"house": lambda _context: FakeVoice()})
    with here._run() as run, pytest.raises(InputError, match="not a voice this machine answers for"):
        run.voices.provider("elevenlabs", a_context())


def test_two_machines_in_one_process_answer_with_their_own_voices(tmp_path: Path) -> None:
    first, second = FakeVoice(name="first"), FakeVoice(name="second")
    one = a_host(tmp_path, providers={"elevenlabs": lambda _context: first})
    two = a_host(tmp_path, providers={"elevenlabs": lambda _context: second})
    with one._run() as outer:
        with two._run() as inner:
            assert inner.voices.provider("elevenlabs", a_context()) is second
        assert outer.voices.provider("elevenlabs", a_context()) is first


def test_a_thread_started_without_the_runs_context_answers_with_the_hosts_voices(tmp_path: Path) -> None:
    """A worker pool that never copied the context still asks its run, so it cannot reach the shipped voice."""
    house = FakeVoice(name="house")
    here = a_host(tmp_path, providers={"elevenlabs": lambda _context: house})
    project = open_project(a_starter(tmp_path, here), machine=here)
    answered: list[object] = []
    with here._run() as run:
        worker = threading.Thread(target=lambda: answered.append(speech_provider(run, project._inputs)))
        worker.start()
        worker.join()
    assert answered == [house]


def test_the_machines_switch_is_stamped_on_every_voice_it_builds(tmp_path: Path) -> None:
    """A stage cannot widen where the key goes, and neither can a context built without the machine."""
    seen: list[VoiceContext] = []
    here = a_host(tmp_path, providers={"elevenlabs": seen.append}, allow_any_api_base=True)
    with here._run() as run:
        run.voices.provider("elevenlabs", a_context())
    assert seen[0].allow_any_api_base is True


def test_the_machines_retries_are_stamped_on_every_voice_it_builds(tmp_path: Path) -> None:
    """A busy voice is asked again as often as the machine says, which no project may change."""
    seen: list[VoiceContext] = []
    here = a_host(tmp_path, providers={"elevenlabs": seen.append}, overrides=("narration.retries=5",))
    with here._run() as run:
        run.voices.provider("elevenlabs", a_context())
    assert seen[0].retries == 5


def test_a_machine_whose_host_gave_no_table_answers_with_the_shipped_sound_providers(tmp_path: Path) -> None:
    assert sorted(a_host(tmp_path).sounds.factories) == sorted(SOUNDS)


def test_a_host_that_gave_its_voices_and_no_sounds_reaches_no_shipped_sound_provider(tmp_path: Path) -> None:
    """A machine built with a fake voice must not reach the real sound service by a table its host left out."""
    here = a_host(tmp_path, providers={"elevenlabs": lambda _context: FakeVoice()})
    assert dict(here.sounds.factories) == {}


def test_the_sound_providers_a_host_supplies_are_the_ones_every_run_carries(tmp_path: Path) -> None:
    seen: list[SoundContext] = []
    here = a_host(
        tmp_path, sound_providers={"house": seen.append}, allow_any_api_base=True, overrides=("narration.retries=5",)
    )
    with here._run() as run:
        run.sounds.provider("house", SoundContext(secrets=NoSecrets(), api_base="", timeout_seconds=1))
    assert (seen[0].allow_any_api_base, seen[0].retries) == (True, 5)


def keyed(voice: FakeVoice) -> Callable[[VoiceContext], FakeVoice]:
    """A provider that asks its project's secrets for the key before it answers, as the shipped one does."""

    def build(context: VoiceContext) -> FakeVoice:
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
    here = a_host(tmp_path, environ={}, providers={"elevenlabs": keyed(FakeVoice())})
    root = a_starter(tmp_path, here)
    named_in_the_file(root, "tenant-voice")
    (root / ".env").write_text("ELEVENLABS_API_KEY=sk-tenant\n", encoding="utf-8")
    project = open_project(root, machine=here)
    with pytest.raises(InputError, match="ELEVENLABS_API_KEY is not set"):
        project.narrate(spend=True)


@pytest.mark.usefixtures("no_network", "fake_ffmpeg")
def test_the_authors_own_env_file_is_read_under_a_machine_that_allows_it(tmp_path: Path) -> None:
    voice = FakeVoice()
    here = a_host(tmp_path, environ={}, providers={"elevenlabs": keyed(voice)}, dotenv=True)
    root = a_starter(tmp_path, here)
    named_in_the_file(root, "author-voice")
    (root / ".env").write_text("ELEVENLABS_API_KEY=sk-author\n", encoding="utf-8")
    assert open_project(root, machine=here).narrate(spend=True).ok
    assert {request.voice_id for request in voice.requests} == {"author-voice"}


def a_context() -> VoiceContext:
    """A context no machine stamped, which is what a stage builds from its project's values."""
    return VoiceContext(
        secrets=NoSecrets(),
        api_base="https://api.elevenlabs.io/v1",
        context_chars=1,
        speech_timeout_seconds=1,
    )


def test_an_override_reaches_the_machine_by_its_own_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every pair reaches both the machine and the project, and each takes the keys it owns."""
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "none.toml"))
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


# ---- applying a fix ---------------------------------------------------------------------------


INSTALL = ("decktalk", "install")
"""The one command a fix may run, which these tests stand in for with a fake subprocess."""
INSTALL_FIX = CommandFix(title="t", applicability=Applicability.SAFE, command=INSTALL)


def a_finding(fix: object) -> Finding:
    return Finding(code=Code.FILE_MISSING, message="x", location=Location(where="a"), fix=fix)


def ran(here: Machine, fix: CommandFix, *, unsafe: bool = False) -> FixOutcome:
    """What applying a machine's fix in a run of its own came to."""
    with here._run() as run:
        return apply_fix(run, Code.FILE_MISSING, fix, root=here.cwd, scope=Scope.MACHINE, unsafe=unsafe)


def exits(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """Make every command a fix runs exit with `code`, so no test installs anything."""
    monkeypatch.setattr(
        "decktalk.machine.subprocess.run",
        lambda argv, **_: subprocess.CompletedProcess(argv, code, b"", b"Traceback\nOSError: the cache is read-only\n"),
    )


def test_a_fix_is_taken_from_the_finding_whose_code_it_resolves() -> None:
    assert fixes_of(a_finding(INSTALL_FIX)) == ((Code.FILE_MISSING, INSTALL_FIX),)
    assert fixes_of([a_finding(None), a_finding(INSTALL_FIX)]) == ((Code.FILE_MISSING, INSTALL_FIX),)


def test_a_fix_only_a_person_can_make_is_reported_and_never_applied(tmp_path: Path) -> None:
    outcome = ran(a_machine(tmp_path), INSTALL_FIX.model_copy(update={"applicability": Applicability.DISPLAY}))
    assert not outcome.applied and "only a person" in (outcome.why or "")


def test_a_fix_left_alone_is_a_warning_that_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor --fix` and `check --fix` print the stream, not the result, so the reason has to be a line."""
    here = a_machine(tmp_path)
    exits(monkeypatch, 1)
    seen: list[Event] = []
    with here.events.subscribe(seen.append):
        here.apply(a_finding(INSTALL_FIX))
    warned = [line.message for line in seen if isinstance(line, Log) and line.level is Level.WARNING]
    assert any(INSTALL_FIX.title in message and "exited 1" in message for message in warned)


def test_a_fix_that_can_lose_work_is_applied_only_on_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = a_machine(tmp_path)
    exits(monkeypatch, 0)
    fix = INSTALL_FIX.model_copy(update={"applicability": Applicability.UNSAFE})
    assert not ran(here, fix).applied and ran(here, fix, unsafe=True).applied


def test_a_command_that_fails_is_reported_rather_than_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits(monkeypatch, 1)
    outcome = ran(a_machine(tmp_path), INSTALL_FIX)
    assert not outcome.applied and "exited 1" in (outcome.why or "")
    assert "OSError: the cache is read-only" in (outcome.why or "")


def test_a_fix_command_leaves_its_command_exit_time_and_output_on_the_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    here = a_machine(tmp_path)
    exits(monkeypatch, 2)
    seen: list[Event] = []
    with here.events.subscribe(seen.append):
        ran(here, INSTALL_FIX)
    [line] = [line for line in seen if isinstance(line, Log) and line.source == "machine"]
    assert line.level is Level.WARNING and line.data is not None
    assert line.data["exit"] == 2 and str(line.data["argv"]).endswith("-m decktalk install")
    assert line.data["output_tail"] == "Traceback | OSError: the cache is read-only"


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
    assert ran(here, INSTALL_FIX).applied
    assert asked["argv"] == [sys.executable, "-m", "decktalk", "install"]
    assert asked["timeout"] == FIX_TIMEOUT_SECONDS
    assert asked["env"] == {
        "ONLY_THIS": "1",
        "DECKTALK_CONFIG": str(here.config_path),
        "DECKTALK_TOOLS_CACHE_DIR": str(here.cache_dir),
    }


def test_a_command_that_runs_past_its_timeout_is_stopped_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def hang(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, FIX_TIMEOUT_SECONDS)

    monkeypatch.setattr("decktalk.machine.subprocess.run", hang)
    outcome = ran(a_machine(tmp_path), INSTALL_FIX)
    assert not outcome.applied and "was stopped" in (outcome.why or "")
    [said] = [record for record in caplog.records if record.name == "decktalk.machine"]
    assert said.levelname == "WARNING" and "(timeout)" in said.getMessage()
    assert data_of(said) == {
        "argv": command_line([sys.executable, "-m", *INSTALL]),
        "reason": "timeout",
        "limit": FIX_TIMEOUT_SECONDS,
    }


def test_a_command_built_without_validation_is_still_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The model refuses a foreign argv, and a model built past its validator meets the same refusal here."""
    marker = tmp_path / "ran"

    def run_it(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        marker.write_text(" ".join(argv), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("decktalk.machine.subprocess.run", run_it)
    hostile = CommandFix.model_construct(
        kind="command", title="t", applicability=Applicability.SAFE, command=("sh", "-c", f"touch {marker}")
    )
    outcome = ran(a_machine(tmp_path), hostile)
    assert not outcome.applied and "not one of DeckTalk's own commands" in (outcome.why or "")
    assert not marker.exists()


def edit_fix(*edits: Edit) -> EditFix:
    """A safe fix of these edits, made in order."""
    return EditFix(title="t", applicability=Applicability.SAFE, edits=edits)


def an_edit(file: str | Path, **locator: object) -> EditFix:
    """A safe fix of one edit, which is the shape a finding read from JSON hands `apply`."""
    return edit_fix(Edit.model_validate({"file": file, "new": "written by a fix", **locator}))


def applied(here: Machine, fix: EditFix, root: Path) -> tuple[bool, str]:
    with here._run() as run:
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
    link(root / "shared", outside)
    done, why = applied(a_machine(tmp_path), an_edit("shared/notes.txt", line=1, old="mine"), root)
    assert not done and "outside the project" in why
    assert (outside / "notes.txt").read_text(encoding="utf-8") == "mine\n"


def test_a_fix_with_one_refused_edit_writes_none_of_its_edits(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    fix = edit_fix(Edit(file=Path("inside.txt"), line=1, new="x"), Edit(file=Path("../outside.txt"), line=1, new="x"))
    done, _why = applied(a_machine(tmp_path), fix, root)
    assert not done
    assert not (root / "inside.txt").exists()


def test_a_fix_whose_second_edit_is_stale_leaves_its_first_file_whole(tmp_path: Path) -> None:
    """Every edit is checked before any file is written, so a fix changes all of its files or none."""
    (tmp_path / "first.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("moved\n", encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("first.txt"), line=1, old="one", new="changed"),
        Edit(file=Path("second.txt"), line=1, old="two", new="changed"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert not done and "no longer reads" in why
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".fixing")) == []


def test_two_edits_to_one_file_are_made_in_order_and_written_once(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("notes.txt"), line=1, old="one", new="first"),
        Edit(file=Path("notes.txt"), line=2, old="two", new="second"),
    )
    done, _why = applied(a_machine(tmp_path), fix, tmp_path)
    assert done
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "first\nsecond\n"


def test_a_link_planted_where_a_fix_writes_its_draft_is_a_refusal_and_is_never_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A draft's name is random, and a name a project did plant is refused in a sentence, never followed."""
    monkeypatch.setattr("decktalk.files.secrets.token_hex", lambda _size: "planted")
    root = tmp_path / "project"
    root.mkdir()
    (root / "notes.txt").write_text("one\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("mine\n", encoding="utf-8")
    link(root / ".notes.txt.planted.draft", elsewhere)
    done, why = applied(a_machine(tmp_path), an_edit("notes.txt", line=1, old="one"), root)
    assert not done and "could not be read or written" in why
    assert elsewhere.read_text(encoding="utf-8") == "mine\n"
    assert (root / "notes.txt").read_text(encoding="utf-8") == "one\n"
    assert (root / ".notes.txt.planted.draft").is_symlink()


def test_a_fix_that_breaks_the_project_file_and_sets_a_key_in_it_changes_nothing(tmp_path: Path) -> None:
    """A key edit is staged with the line edits, so the file both change is judged whole before any lands."""
    (tmp_path / "script.md").write_text("one\n", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text('[project]\nname = "t"\n', encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("script.md"), line=1, old="one", new="changed"),
        Edit(file=Path("decktalk.toml"), line=1, old="[project]", new="[project"),
        Edit(file=Path("decktalk.toml"), key="video.width", new="1280"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert not done and "not valid TOML" in why
    assert (tmp_path / "script.md").read_text(encoding="utf-8") == "one\n"
    assert (tmp_path / "decktalk.toml").read_text(encoding="utf-8") == '[project]\nname = "t"\n'


def test_a_line_edit_and_a_key_edit_of_the_project_file_both_land_in_one_write(tmp_path: Path) -> None:
    (tmp_path / "decktalk.toml").write_text('[project]\nname = "t"\n', encoding="utf-8")
    fix = edit_fix(
        Edit(file=Path("decktalk.toml"), line=2, old='name = "t"', new='name = "renamed"'),
        Edit(file=Path("decktalk.toml"), key="video.width", new="1280"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert done, why
    written = (tmp_path / "decktalk.toml").read_text(encoding="utf-8")
    assert 'name = "renamed"' in written and "width = 1280" in written


def test_a_file_the_system_refuses_to_replace_puts_back_every_file_already_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second move that fails, as a full disk or an editor holding a file open makes it, is a refusal."""
    (tmp_path / "first.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("two\n", encoding="utf-8")
    moves: list[Path] = []
    real = Path.replace

    def full(self: Path, target: Path) -> Path:
        moves.append(Path(target))
        if len(moves) == 2:
            raise OSError(28, "No space left on device", str(target))
        return real(self, target)

    monkeypatch.setattr(Path, "replace", full)
    fix = edit_fix(
        Edit(file=Path("first.txt"), line=1, old="one", new="changed"),
        Edit(file=Path("second.txt"), line=1, old="two", new="changed"),
    )
    done, why = applied(a_machine(tmp_path), fix, tmp_path)
    assert (
        not done
        and why
        == "second.txt could not be read or written (No space left on device), so every file was left as it was."
    )
    assert (tmp_path / "first.txt").read_text(encoding="utf-8") == "one\n"
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "two\n"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.endswith(".draft")) == []


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_settings_fix_never_writes_through_a_project_file_linked_out_of_the_project(
    tmp_path: Path, kind: str
) -> None:
    """A project that arrives with its `decktalk.toml` linked elsewhere chose where a settings fix writes."""
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "victim.toml"
    outside.write_text('[project]\nname = "victim"\n', encoding="utf-8")
    link(root / "decktalk.toml", outside, hard=kind == "hard")
    fix = SettingFix(title="t", applicability=Applicability.SAFE, key="video.width", value="1280")
    with a_machine(tmp_path)._run() as run:
        outcome = apply_fix(run, Code.CUE_MISSING, fix, root=root, scope=Scope.PROJECT, unsafe=False)
    assert outside.read_text(encoding="utf-8") == '[project]\nname = "victim"\n'
    if kind == "symbolic":
        assert not outcome.applied and "outside the project" in (outcome.why or "")
    else:
        assert outcome.applied and "width = 1280" in (root / "decktalk.toml").read_text(encoding="utf-8")


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
        if isinstance(event, Log) and event.message == "one":
            raise RuntimeError("no")
        seen.append(event)

    with here.events.subscribe(angry), here._run() as run:
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
    with here.events.subscribe(seen.append), here._run():
        assert bound_tools().cache_dir == str(tmp_path / "cache")
        announce("ffmpeg", 10, 100)
    assert bound_tools().cache_dir == ""
    fetched = [line for line in seen if isinstance(line, Fetch)]
    assert [(line.tool, line.bytes, line.total_bytes) for line in fetched] == [("ffmpeg", 10, 100)]
