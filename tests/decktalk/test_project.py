"""One object opens a directory, twelve calls move it forward, and every call opens a run.

Every stage is faked at the one seam the facade reaches it through, which is the module-level
function named after the stage. What these tests judge is therefore the facade: the run it opens,
the lock it holds, the arguments it hands down and the result it insists on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from filelock import FileLock, Timeout

import decktalk
from decktalk.errors import ApprovalRequired, Cancel, ErrorCode, InputError, ProjectLocked
from decktalk.events import Event, Level, Log
from decktalk.files import replace_all
from decktalk.findings import Applicability, Code, Edit, EditFix, Finding, Location
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.page import PREVIEW_CUE_TIMES
from decktalk.pipeline import Stage
from decktalk.project import LOCK_FILE, OWNER_FILE, Origin, Project, section_numbers, stage_call
from decktalk.results import (
    BuildResult,
    CheckResult,
    CueResult,
    NarrateResult,
    RecordResult,
    Result,
    StatusResult,
)
from decktalk.results import Layer as SettingLayer
from decktalk.settings import ToolsConfig
from decktalk.stages import narrate as narrate_stage
from support.fakes import FakeChromium
from support.links import link
from support.projects import MINIMAL_TOML, write_project
from support.runs import a_machine
from support.spends import a_spend


def a_project(tmp_path: Path, toml: str = MINIMAL_TOML, **environ: str) -> Project:
    write_project(tmp_path, toml)
    return decktalk.open(tmp_path, machine=a_machine(tmp_path, **environ))


Call = tuple[Any, Run, dict[str, Any]]
"""One call the facade made into a stage: what it handed down, and under which run."""


@pytest.fixture
def fake_stages(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Call]]:
    """Every stage replaced by a module holding the one function the convention names.

    The facade imports `decktalk.stages.<name>` and calls `<name>(inputs, run, **options)`, so a
    fake module at that path is the whole seam and nothing of a real stage runs.
    """
    calls: dict[str, list[Call]] = {}
    answers: dict[str, type[Result]] = {
        "narrate": NarrateResult,
        "cue": CueResult,
        "record": RecordResult,
        "build": BuildResult,
        "status": StatusResult,
        "check": CheckResult,
    }

    def make(name: str, model: type[Result]) -> Callable[..., Result]:
        def stage(inputs: Inputs, run: Run, **options: object) -> Result:
            calls.setdefault(name, []).append((inputs, run, options))
            return run.result(model, **_filler(name))

        return stage

    for name, model in answers.items():
        a_stage(monkeypatch, name, make(name, model))
    return calls


def a_stage(monkeypatch: pytest.MonkeyPatch, name: str, call: object) -> None:
    """A module at `decktalk.stages.<name>` holding `call` under the stage's name, which is the whole seam."""
    module = types.ModuleType(f"decktalk.stages.{name}")
    setattr(module, name, call)
    monkeypatch.setitem(sys.modules, module.__name__, module)


def _filler(name: str) -> dict[str, Any]:
    """The fields each faked result needs beyond the ones the run fills, and nothing more."""
    priced = a_spend(0.0, 0.0, layer=SettingLayer.DEFAULT)
    return {
        "narrate": {"spending": False, "sections": (), "spend": priced, "seconds": 0.0},
        "cue": {"sections": (), "seconds": 0.0},
        "record": {"sections": (), "seconds": 0.0},
        "build": {"stages": (), "spending": False, "spend": priced, "seconds": 0.0},
        "status": {"name": "t", "script": Path("script.md"), "cues": Path("cues.json"), "sections": ()},
        "check": {"judged": (), "pages": True, "frames": True, "spend": priced},
    }[name]


# ---- opening ---------------------------------------------------------------------------------


def test_a_project_is_a_directory_and_open_is_what_opens_one(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    assert project.root == tmp_path.resolve()
    assert project._inputs.document.name == "t"
    assert project._inputs.workspace.build == tmp_path / "build"
    assert repr(project).startswith("Project(")


def test_the_project_file_itself_names_the_project_it_sits_in(tmp_path: Path) -> None:
    write_project(tmp_path)
    project = decktalk.open(tmp_path / "decktalk.toml", machine=a_machine(tmp_path))
    assert project.root == tmp_path.resolve()


def test_a_caller_that_names_nothing_is_read_from_the_machine_and_never_from_the_process(
    tmp_path: Path,
) -> None:
    """`DECKTALK_PROJECT` reaches the library through the machine, which is the one reader there is."""
    write_project(tmp_path)
    project = decktalk.open(machine=a_machine(tmp_path, DECKTALK_PROJECT=str(tmp_path)))
    assert project.root == tmp_path.resolve()


def test_a_directory_with_no_project_file_names_the_file_and_the_next_action(tmp_path: Path) -> None:
    with pytest.raises(InputError) as refused:
        decktalk.open(tmp_path, machine=a_machine(tmp_path))
    assert refused.value.code is ErrorCode.INPUT
    assert "decktalk init" in (refused.value.hint or "")


def test_reloading_reads_the_project_again(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    write_project(tmp_path, MINIMAL_TOML.replace('name = "t"', 'name = "renamed"'))
    assert project._inputs.document.name == "t"
    assert project.reload()._inputs.document.name == "renamed"


def test_an_override_given_for_one_run_reaches_the_settings(tmp_path: Path) -> None:
    write_project(tmp_path)
    project = decktalk.open(tmp_path, machine=a_machine(tmp_path), overrides=("video.crf=20",))
    assert project.settings.video.crf == 20
    assert project.reload().settings.video.crf == 20


@pytest.mark.parametrize("pair", ["record.page_policy=trusted", "record.browser_path=/bin/echo"])
def test_an_override_at_open_cannot_set_a_key_that_belongs_to_the_host_machine(tmp_path: Path, pair: str) -> None:
    """A host that forwards a tenant's pairs would otherwise hand the tenant its browser and its trust level."""
    write_project(tmp_path)
    host = Machine.of(
        environ={},
        config_path=tmp_path / "host.toml",
        cwd=tmp_path,
        cache_dir=tmp_path / "cache",
        overrides=("record.page_policy=untrusted",),
    )
    with pytest.raises(InputError, match="machine-scoped"):
        decktalk.open(tmp_path, machine=host, overrides=(pair,))
    assert decktalk.open(tmp_path, machine=host).settings.record.page_policy == "untrusted"


def test_an_override_given_to_open_without_a_machine_reaches_the_machine_it_makes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no machine given, the caller is the one who owns the machine it makes, so every key is theirs."""
    monkeypatch.setenv("DECKTALK_CONFIG", str(tmp_path / "machine.toml"))
    write_project(tmp_path)
    project = decktalk.open(tmp_path, overrides=("record.concurrency=2", "video.crf=20"))
    assert (project.settings.record.concurrency, project.settings.video.crf) == (2, 20)


def test_the_layers_say_which_layer_set_each_key(tmp_path: Path) -> None:
    project = a_project(tmp_path, MINIMAL_TOML + "\n[video]\ncrf = 21\n")
    assert project.layers.winner("video.crf").layer.value == "project"


def test_a_changed_file_names_the_sections_a_watch_loop_must_rebuild(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    assert project.sections_touching(tmp_path / "deck" / "index.html") == (1, 2)


# ---- the selection edge ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [("3", (3,)), ("3,5", (3, 5)), ("5-7", (5, 6, 7)), ("3,5-7", (3, 5, 6, 7)), ("3, 3", (3,))],
)
def test_a_section_selection_is_parsed_once_at_the_edge(written: str, expected: tuple[int, ...]) -> None:
    """The library takes a run of numbers, so a string is read into one spelling here and nowhere else."""
    assert section_numbers(written) == expected


def test_a_selection_that_names_no_section_is_refused_with_what_one_looks_like() -> None:
    with pytest.raises(InputError, match="does not name a section"):
        section_numbers("three")


def test_a_run_written_backwards_is_refused_rather_than_selecting_nothing() -> None:
    """A selection of no sections would run every stage on nothing and report success."""
    with pytest.raises(InputError, match="runs backwards") as refused:
        section_numbers("3,9-7")
    assert refused.value.hint == "Write the lower number first, as in 7-9."


# ---- the calls -----------------------------------------------------------------------------------


def test_every_verb_reaches_the_stage_of_its_own_name(tmp_path: Path, fake_stages: dict[str, list[Call]]) -> None:
    project = a_project(tmp_path)
    project.narrate()
    project.cue()
    project.record()
    assert sorted(fake_stages) == ["cue", "narrate", "record"]


def test_a_stage_is_handed_the_inputs_the_run_and_its_own_options(
    tmp_path: Path, fake_stages: dict[str, list[Call]]
) -> None:
    project = a_project(tmp_path)
    project.narrate(only=(1, 2), force=True)
    inputs, run, options = fake_stages["narrate"][0]
    assert inputs is project._inputs
    assert run.id and run.root == project.root
    assert options["only"] == (1, 2) and options["force"] is True


def test_a_result_that_is_not_the_one_the_command_is_named_after_is_a_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = {"name": "t", "script": Path("s"), "cues": Path("c"), "sections": ()}
    a_stage(monkeypatch, "cue", lambda _inputs, run, **_options: run.result(StatusResult, **answer))
    with pytest.raises(TypeError, match="cue answered with StatusResult"):
        a_project(tmp_path).cue()


def test_the_stage_seam_is_one_function_named_after_its_own_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    a_stage(monkeypatch, "verify", "the one function")
    assert stage_call(Stage.VERIFY.value) == "the one function"


@pytest.mark.usefixtures("fake_stages")
def test_a_call_opens_a_run_on_the_project_view_of_the_stream(tmp_path: Path) -> None:
    """A renderer attaches before the call it wants to watch, so the view follows the runs as they open."""
    project = a_project(tmp_path)
    seen: list[Event] = []
    with project.events.subscribe(seen.append):
        project.cue()
    assert [line.event for line in seen if line.event != "log"] == ["run.start", "run.done"]


@pytest.mark.usefixtures("fake_stages")
def test_a_writing_run_says_it_holds_the_build_directory(tmp_path: Path) -> None:
    """A host that sees two jobs collide learns the winner's side as well as the loser's."""
    project = a_project(tmp_path)
    seen: list[Event] = []
    with project.events.subscribe(seen.append):
        project.cue()
    [held] = [line for line in seen if isinstance(line, Log) and line.source == "project"]
    assert held.data == {"pid": os.getpid(), "lock": ".lock"}


@pytest.mark.usefixtures("fake_stages")
def test_what_the_load_noticed_is_a_warning_on_every_run(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A misspelled key in a log line never reached `--json` or the events file, so it is a line of the run."""
    write_project(tmp_path, MINIMAL_TOML + "\n[video]\npresett = 'veryfast'\n")
    with caplog.at_level("WARNING", logger="decktalk"):
        project = decktalk.open(tmp_path, machine=a_machine(tmp_path))
    seen: list[Event] = []
    with project.events.subscribe(seen.append):
        project.cue()
    warned = [line.message for line in seen if isinstance(line, Log) and line.level is Level.WARNING]
    assert tuple(warned) == project._inputs.notes
    assert any("presett" in note for note in warned)
    assert not [record for record in caplog.records if "presett" in record.getMessage()], "said once, on the run"


@pytest.mark.usefixtures("fake_stages")
def test_one_project_never_sees_another_project_lines(tmp_path: Path) -> None:
    machine = a_machine(tmp_path)
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    first = decktalk.open(write_project(tmp_path / "a", MINIMAL_TOML), machine=machine)
    second = decktalk.open(write_project(tmp_path / "b", MINIMAL_TOML), machine=machine)
    seen: list[Event] = []
    with first.events.subscribe(seen.append):
        second.cue()
    assert seen == []


@pytest.mark.usefixtures("fake_stages")
def test_a_run_writes_its_own_lines_beside_the_build(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    result = project.cue()
    assert (project._inputs.workspace.events_dir / f"{result.run}.jsonl").exists()


@pytest.mark.usefixtures("fake_stages")
def test_a_build_directory_linked_out_of_the_project_is_neither_pruned_nor_written(tmp_path: Path) -> None:
    """A downloaded project chose every name under its own `build/`, links included."""
    root, outside = tmp_path / "project", tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    victim = outside / "victim.jsonl"
    victim.write_text("not the project's\n", encoding="utf-8")
    project = a_project(root, MINIMAL_TOML + "\n[output]\nevents_keep_runs = 1\n")
    project._inputs.workspace.build.mkdir()
    project._inputs.workspace.events_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(InputError) as refused:
        project.status()
    assert refused.value.code is ErrorCode.INPUT
    assert "elsewhere" not in str(refused.value)
    assert [path.name for path in outside.iterdir()] == ["victim.jsonl"]


@pytest.mark.usefixtures("fake_stages")
def test_a_take_linked_out_of_the_build_directory_refuses_the_run_that_would_write_it(tmp_path: Path) -> None:
    root, outside = tmp_path / "project", tmp_path / "elsewhere.mp3"
    root.mkdir()
    outside.write_bytes(b"not the project's")
    project = a_project(root)
    project._inputs.workspace.narrate_dir.mkdir(parents=True)
    (project._inputs.workspace.narrate_dir / "narration.mp3").symlink_to(outside)
    with pytest.raises(InputError):
        project.narrate()
    assert outside.read_bytes() == b"not the project's"
    assert not project._inputs.workspace.events_dir.exists()


@pytest.mark.usefixtures("fake_stages")
def test_a_build_directory_that_is_itself_a_link_out_of_the_project_is_refused(tmp_path: Path) -> None:
    """The load holds the build directory to the project, and every run holds it there again."""
    root, outside = tmp_path / "project", tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    project = a_project(root)
    project._inputs.workspace.build.symlink_to(outside, target_is_directory=True)
    with pytest.raises(InputError):
        project.cue()
    assert list(outside.iterdir()) == []


@contextmanager
def held(build: Path, owner: str = "1 abc\n") -> Iterator[None]:
    """The build lock taken by another writer, with its note, for as long as the block runs."""
    with FileLock(build / LOCK_FILE):
        replace_all({build / OWNER_FILE: owner})
        yield


def free(build: Path) -> bool:
    """Whether nobody holds the build lock now, asked by taking it and giving it straight back."""
    try:
        with FileLock(build / LOCK_FILE, blocking=False):
            return True
    except Timeout:
        return False


@pytest.mark.usefixtures("fake_stages")
def test_a_reporting_call_takes_no_lock_and_a_writing_call_does(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    build = project._inputs.workspace.build
    project.status()
    assert not (build / LOCK_FILE).exists()
    project.cue()
    assert not (build / OWNER_FILE).exists()
    assert free(build)  # the lock is released however the call ends


@pytest.mark.usefixtures("fake_stages")
def test_a_check_that_opens_pages_holds_the_build_and_one_that_reads_alone_does_not(tmp_path: Path) -> None:
    """A check with pages freezes frames and draws the storyboard, which is a writer's work."""
    project = a_project(tmp_path)
    with held(project._inputs.workspace.build):
        project.check(pages=False)
        with pytest.raises(ProjectLocked):
            project.check()


@pytest.mark.usefixtures("fake_stages")
@pytest.mark.parametrize("planted", ["link", "directory"])
def test_a_lock_file_that_cannot_be_a_lock_is_a_refusal_that_names_it(tmp_path: Path, planted: str) -> None:
    """A link inside the build stays inside, so confinement lets it through, and the lock must refuse it."""
    project = a_project(tmp_path)
    build = project._inputs.workspace.build
    build.mkdir(parents=True, exist_ok=True)
    (build / "kept.json").write_text("{}", encoding="utf-8")
    if planted == "link":
        link(build / LOCK_FILE, build / "kept.json")
    else:
        (build / LOCK_FILE).mkdir()
    with pytest.raises(InputError, match=r"\.lock cannot be used as the build lock") as refused:
        project.cue()
    assert refused.value.code is ErrorCode.INPUT
    assert (build / "kept.json").read_text(encoding="utf-8") == "{}"


@pytest.mark.usefixtures("fake_stages")
def test_a_second_writer_is_refused_while_the_first_holds_the_build(tmp_path: Path) -> None:
    """The trigger is a build run by hand under a live watch loop, not a service."""
    project = a_project(tmp_path)
    with held(project._inputs.workspace.build), pytest.raises(ProjectLocked) as refused:
        project.cue()
    assert refused.value.code is ErrorCode.LOCKED
    assert "process 1, run abc" in str(refused.value)


@pytest.mark.usefixtures("fake_stages")
def test_a_note_nobody_holds_is_taken_and_reported(tmp_path: Path) -> None:
    """A caller cannot clear a file it was never told about, so this is a line and not a refusal."""
    project = a_project(tmp_path)
    note = project._inputs.workspace.build / OWNER_FILE
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("999999 gone\n", encoding="utf-8")
    seen: list[Event] = []
    with project.events.subscribe(seen.append):
        project.cue()
    assert any(isinstance(line, Log) and line.level is Level.WARNING for line in seen)
    assert not note.exists()


@pytest.mark.usefixtures("fake_stages")
def test_a_note_that_is_a_link_is_never_followed(tmp_path: Path) -> None:
    """A project may ship its build directory, so a note it planted cannot quote or overwrite a file elsewhere."""
    project = a_project(tmp_path)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("7 secret\n", encoding="utf-8")
    note = project._inputs.workspace.build / OWNER_FILE
    note.parent.mkdir(parents=True, exist_ok=True)
    note.symlink_to(elsewhere)
    with pytest.raises(InputError, match="leads outside the build directory") as refused:
        project.cue()
    assert "secret" not in str(refused.value)
    assert elsewhere.read_text(encoding="utf-8") == "7 secret\n"


HOLDER = """
import os, sys
from pathlib import Path
from filelock import FileLock
from decktalk.files import replace_all
from decktalk.project import LOCK_FILE, OWNER_FILE
build = Path(sys.argv[1])
lock = FileLock(build / LOCK_FILE)
lock.acquire()
replace_all({build / OWNER_FILE: f"{os.getpid()} crashed\\n"})
print("held", flush=True)
sys.stdin.read()
"""
"""A writer in another process that takes the lock the way DeckTalk does and then waits to be killed."""


@pytest.mark.usefixtures("fake_stages")
def test_the_system_frees_the_lock_of_a_writer_that_was_killed(tmp_path: Path) -> None:
    """A killed holder releases nothing itself, and the operating system releases the lock for it."""
    project = a_project(tmp_path)
    build = project._inputs.workspace.build
    build.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-c", HOLDER, str(build)]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as holder:
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            with pytest.raises(ProjectLocked, match="run crashed"):
                project.cue()
        finally:
            holder.kill()
    seen: list[Event] = []
    with project.events.subscribe(seen.append):
        project.cue()
    assert any(isinstance(line, Log) and OWNER_FILE in line.message for line in seen)


def test_a_cancel_token_reaches_the_stage_that_checks_it(tmp_path: Path, fake_stages: dict[str, list[Call]]) -> None:
    project = a_project(tmp_path)
    cancel = Cancel()
    project.record(cancel=cancel)
    _inputs, run, _options = fake_stages["record"][0]
    assert run.cancel is cancel


def test_spend_and_a_ceiling_reach_the_gate_rather_than_the_stage(
    tmp_path: Path, fake_stages: dict[str, list[Call]]
) -> None:
    project = a_project(tmp_path)
    project.build(spend=True, max_cost=2.5)
    _inputs, run, options = fake_stages["build"][0]
    assert run.spend is True and run.max_cost == 2.5
    assert "spend" not in options and "max_cost" not in options


def test_the_callers_threshold_reaches_the_build(tmp_path: Path, fake_stages: dict[str, list[Call]]) -> None:
    """`allow` and `stop_on` are how a caller says which findings may stop its run."""
    project = a_project(tmp_path)
    project.build(allow=[Code.PAGE_BLACK], stop_on=None)
    _inputs, _run, options = fake_stages["build"][0]
    assert options["allow"] == frozenset({Code.PAGE_BLACK})
    assert options["stop_on"] is None


# ---- applying a fix --------------------------------------------------------------------------------


def fixing(edit: Edit) -> Finding:
    """A finding whose one safe fix is `edit`."""
    fix = EditFix(title="Repair the row.", applicability=Applicability.SAFE, edits=(edit,))
    return Finding(code=Code.CUE_MISSING, message="x", location=Location(where=edit.file.as_posix()), fix=fix)


@pytest.mark.parametrize(
    ("before", "edit", "after"),
    [
        # A fix that scaffolds a missing row adds it, which is what makes `check --fix` idempotent.
        ("one\ntwo\n", Edit(file=Path("notes.txt"), line=2, new="three"), "one\nthree\ntwo\n"),
        ("one\ntwo\n", Edit(file=Path("notes.txt"), line=2, old="two", new="three"), "one\nthree\n"),
        # The cue file's own fix writes the whole of one, so the file it names is not there to be read.
        (None, Edit(file=Path("cues.json"), line=1, new='{"sections": {}}'), '{"sections": {}}\n'),
    ],
    ids=["adds", "replaces", "creates"],
)
def test_a_safe_edit_leaves_its_file_reading_what_it_said(
    tmp_path: Path, before: str | None, edit: Edit, after: str
) -> None:
    project = a_project(tmp_path)
    if before is not None:
        (tmp_path / edit.file).write_text(before, encoding="utf-8")
    result = project.apply(fixing(edit))
    assert result.fixes[0].applied and result.fixes[0].files == (edit.file,)
    assert (tmp_path / edit.file).read_text(encoding="utf-8") == after


def test_an_edit_into_a_file_that_is_not_there_says_so_rather_than_raising(tmp_path: Path) -> None:
    """A fix that changes a line needs the lines, and a caller is told that in a sentence it can print."""
    project = a_project(tmp_path)
    outcome = project.apply(fixing(Edit(file=Path("notes.txt"), line=4, old="two", new="three"))).fixes[0]
    assert not outcome.applied and "notes.txt" in (outcome.why or "")
    assert not (tmp_path / "notes.txt").exists()


def test_a_finding_with_no_fix_is_nothing_to_apply(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    found = Finding(code=Code.CUE_MISSING, message="x", location=Location(where="cues.json"))
    assert project.apply([found]).fixes == ()


# ---- the origin ---------------------------------------------------------------------------------


def test_an_origin_is_closed_by_leaving_the_block_it_was_opened_in(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    with project.serve(port=0) as origin:
        assert isinstance(origin, Origin)
        assert origin.result.url.startswith("http://127.0.0.1:") and origin.result.port > 0
    with pytest.raises(OSError, match="Bad file descriptor|closed"):
        origin._server.socket.getsockname()


def test_a_served_preview_reads_its_cue_times_from_the_alias_the_recorder_uses(tmp_path: Path) -> None:
    """A preview has no recorder to put its cues in its URL, so the origin answers the one alias."""
    project = a_project(tmp_path)
    with project.serve(port=0) as origin, urllib.request.urlopen(origin.result.url + PREVIEW_CUE_TIMES) as sent:
        assert json.loads(sent.read()) == project._inputs.preview_cues()


def test_a_voiced_build_under_the_untrusted_policy_is_refused_before_it_buys_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The storyboard is the first page a voiced build opens, so the refusal lands before narrate is reached."""
    write_project(tmp_path, MINIMAL_TOML)
    machine = Machine(
        environ={},
        tables={"record": {"page_policy": "untrusted"}},
        config_path=tmp_path / "config.toml",
        cwd=tmp_path,
        toolchain=Toolchain(tools=ToolsConfig(cache_dir=str(tmp_path / "cache"))),
    )
    monkeypatch.setattr(narrate_stage, "narrate", lambda *_a, **_k: pytest.fail("narrate was reached"))
    chromium = FakeChromium(Path(__file__))
    monkeypatch.setattr("playwright.sync_api.sync_playwright", chromium.started())
    with pytest.raises(ApprovalRequired, match="untrusted page"):
        decktalk.open(tmp_path, machine=machine).build(spend=True, max_cost=1.0)
    assert chromium.asked == []
