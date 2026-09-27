"""This computer as one value, the run every call opens on it, and the gate a spend passes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from decktalk import machine as machine_module
from decktalk.errors import ApprovalRequired, Cancel, Cancelled, ErrorCode
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
from decktalk.media.ffmpeg import bound_tools
from decktalk.pipeline import Outcome, Stage
from decktalk.results import Layer, Scope, Spend, SpendState, StatusResult, Voicing
from decktalk.settings import ToolsConfig
from decktalk.toolchain.announce import announce
from decktalk.toolchain.cache import cache_dir, standard_cache_dir


def a_machine(tmp_path: Path, **environ: str) -> Machine:
    """A machine that read nothing, which is what every test here is handed rather than the real one."""
    return Machine(
        environ=environ,
        tables={},
        config_path=tmp_path / "config.toml",
        cwd=tmp_path,
        toolchain=Toolchain(tools=ToolsConfig(cache_dir=str(tmp_path / "cache"))),
    )


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


def test_a_provider_a_caller_supplied_answers_before_the_shipped_one(tmp_path: Path) -> None:
    """The map is a field, so two projects in one process cannot swap each other's voice."""
    mine = object()
    here = a_machine(tmp_path)
    assert object.__getattribute__(here, "providers") == {}
    swapped = Machine(
        environ={},
        tables={},
        config_path=tmp_path / "c.toml",
        cwd=tmp_path,
        toolchain=Toolchain(),
        providers={"elevenlabs": mine},
    )
    assert swapped.provider("elevenlabs") is mine


def test_an_override_reaches_the_machine_by_its_own_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every pair reaches both the machine and the project, and each takes the keys it owns."""
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "none.toml"))
    here = Machine.from_environment(overrides=(("tools.cache_dir", str(tmp_path / "elsewhere")),))
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
    assert asked["env"] == {"ONLY_THIS": "1"}


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


def applied(here: Machine, fix: EditFix, root: Path) -> tuple[bool, str]:
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


def test_a_knob_a_fix_names_is_written_into_the_machine_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = a_machine(tmp_path)
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "machine.toml"))
    fix = SettingFix(title="t", applicability=Applicability.SAFE, key="tools.ffmpeg", value="/usr/bin/ffmpeg")
    result = here.apply(a_finding(fix))
    assert result.fixes[0].applied, result.fixes[0].why
    assert "/usr/bin/ffmpeg" in (tmp_path / "machine.toml").read_text(encoding="utf-8")


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
