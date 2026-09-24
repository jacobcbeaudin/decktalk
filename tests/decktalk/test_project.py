"""One object opens a directory, twelve calls move it forward, and every call opens a run.

Every stage is faked at the one seam the facade reaches it through, which is the module-level
function named after the stage. What these tests judge is therefore the facade: the run it opens,
the lock it holds, the arguments it hands down and the result it insists on.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import decktalk
from decktalk.errors import Cancel, ErrorCode, InputError, ProjectLocked
from decktalk.events import Event, Level, Log
from decktalk.findings import Applicability, Code, Edit, EditFix, Finding, Location
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.pipeline import Stage
from decktalk.project import LOCK_FILE, Origin, Project, section_numbers, stage_call
from decktalk.results import (
    BuildResult,
    CheckResult,
    CueResult,
    NarrateResult,
    RecordResult,
    Result,
    Spend,
    SpendState,
    StatusResult,
    Voicing,
)
from decktalk.results import Layer as SettingLayer
from support.projects import MINIMAL_TOML, write_project


def a_machine(tmp_path: Path, **environ: str) -> Machine:
    return Machine(
        environ=environ,
        tables={},
        config_path=tmp_path / "machine.toml",
        cwd=tmp_path,
        toolchain=Toolchain(),
    )


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
        module = types.ModuleType(f"decktalk.stages.{name}")
        setattr(module, name, make(name, model))
        monkeypatch.setitem(sys.modules, f"decktalk.stages.{name}", module)
    return calls


def _filler(name: str) -> dict[str, Any]:
    """The fields each faked result needs beyond the ones the run fills, and nothing more."""
    priced = Spend(
        state=SpendState.ESTIMATE,
        sections=(1,),
        characters=10,
        dollars=0.0,
        ceiling_dollars=0.0,
        price_per_1000_characters=0.0,
        price_layer=SettingLayer.DEFAULT,
    )
    return {
        "narrate": {"voice": Voicing.PLACEHOLDER, "sections": (), "spend": priced, "seconds": 0.0},
        "cue": {"sections": (), "seconds": 0.0},
        "record": {"sections": (), "seconds": 0.0},
        "build": {"stages": (), "voice": Voicing.PLACEHOLDER, "spend": priced, "seconds": 0.0},
        "status": {"name": "t", "script": Path("script.md"), "cues": Path("cues.json"), "sections": ()},
        "check": {"judged": (), "pages": True, "frames": True, "spend": priced},
    }[name]


# ---- opening ---------------------------------------------------------------------------------


def test_a_project_is_a_directory_and_open_is_what_opens_one(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    assert project.root == tmp_path.resolve()
    assert project.document.name == "t"
    assert project.workspace.build == tmp_path / "build"
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
    assert project.document.name == "t"
    assert project.reload().document.name == "renamed"


def test_an_override_given_for_one_run_reaches_the_settings(tmp_path: Path) -> None:
    write_project(tmp_path)
    project = decktalk.open(tmp_path, machine=a_machine(tmp_path), overrides=("video.crf=20",))
    assert project.settings.video.crf == 20
    assert project.reload().settings.video.crf == 20


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
    assert inputs is project.inputs
    assert run.id and run.root == project.root
    assert options["only"] == (1, 2) and options["force"] is True


def test_a_result_that_is_not_the_one_the_command_is_named_after_is_a_bug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = types.ModuleType("decktalk.stages.cue")
    module.cue = lambda inputs, run, **options: run.result(StatusResult, name="t", script=Path("s"),
                                                           cues=Path("c"), sections=())  # fmt: skip
    monkeypatch.setitem(sys.modules, "decktalk.stages.cue", module)
    with pytest.raises(TypeError, match="cue answered with StatusResult"):
        a_project(tmp_path).cue()


def test_the_stage_seam_is_one_function_named_after_its_own_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("decktalk.stages.verify")
    module.verify = "the one function"
    monkeypatch.setitem(sys.modules, "decktalk.stages.verify", module)
    assert stage_call(Stage.VERIFY.value) == "the one function"


@pytest.mark.usefixtures("fake_stages")
def test_a_call_opens_a_run_on_the_project_view_of_the_stream(tmp_path: Path) -> None:
    """A renderer attaches before the call it wants to watch, so the view follows the runs as they open."""
    project = a_project(tmp_path)
    seen: list[Event] = []
    with project.events.subscribe(seen.append):
        project.cue()
    assert [line.event for line in seen] == ["run.start", "run.done"]


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
    assert (project.workspace.events_dir / f"{result.run}.jsonl").exists()


@pytest.mark.usefixtures("fake_stages")
def test_a_reporting_call_takes_no_lock_and_a_writing_call_does(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    lock = project.workspace.build / LOCK_FILE
    project.status()
    assert not lock.exists()
    project.cue()
    assert not lock.exists()  # the lock is released however the call ends


@pytest.mark.usefixtures("fake_stages")
def test_a_check_that_opens_pages_holds_the_build_and_one_that_reads_alone_does_not(tmp_path: Path) -> None:
    """A check with pages freezes frames and draws the storyboard, which is a writer's work."""
    project = a_project(tmp_path)
    lock = project.workspace.build / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1 abc\n", encoding="utf-8")
    project.check(pages=False)
    with pytest.raises(ProjectLocked):
        project.check()


@pytest.mark.usefixtures("fake_stages")
def test_a_second_writer_is_refused_while_the_first_holds_the_build(tmp_path: Path) -> None:
    """The trigger is a build run by hand under a live watch loop, not a service."""
    project = a_project(tmp_path)
    lock = project.workspace.build / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1 abc\n", encoding="utf-8")
    with pytest.raises(ProjectLocked) as refused:
        project.cue()
    assert refused.value.code is ErrorCode.LOCKED


@pytest.mark.usefixtures("fake_stages")
def test_a_lock_left_by_a_run_that_is_gone_is_broken_and_reported(tmp_path: Path) -> None:
    """A caller cannot clear a file it was never told about, so this is a line and not a refusal."""
    project = a_project(tmp_path)
    lock = project.workspace.build / LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("999999 gone\n", encoding="utf-8")
    seen: list[Event] = []
    with project.events.subscribe(seen.append):
        project.cue()
    assert any(isinstance(line, Log) and line.level is Level.WARNING for line in seen)


def test_a_cancel_token_reaches_the_stage_that_checks_it(tmp_path: Path, fake_stages: dict[str, list[Call]]) -> None:
    project = a_project(tmp_path)
    cancel = Cancel()
    project.record(cancel=cancel)
    _inputs, run, _options = fake_stages["record"][0]
    assert run.cancel is cancel


def test_a_voicing_and_a_ceiling_reach_the_gate_rather_than_the_stage(
    tmp_path: Path, fake_stages: dict[str, list[Call]]
) -> None:
    project = a_project(tmp_path)
    project.build(voice=Voicing.PAID, max_cost=2.5)
    _inputs, run, options = fake_stages["build"][0]
    assert run.voice is Voicing.PAID and run.max_cost == 2.5
    assert "voice" not in options and "max_cost" not in options


# ---- applying a fix --------------------------------------------------------------------------------


def test_a_safe_edit_that_only_adds_puts_its_line_in_without_losing_one(tmp_path: Path) -> None:
    """A fix that scaffolds a missing row adds it, which is what makes `check --fix` idempotent."""
    project = a_project(tmp_path)
    (tmp_path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    fix = EditFix(
        title="Add the missing row.",
        applicability=Applicability.SAFE,
        edits=(Edit(file=Path("notes.txt"), line=2, new="three"),),
    )
    found = Finding(code=Code.CUE_MISSING, message="x", location=Location(where="notes.txt"), fix=fix)
    result = project.apply(found)
    assert result.fixes[0].applied and result.fixes[0].files == (Path("notes.txt"),)
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "one\nthree\ntwo\n"


def test_a_safe_edit_that_replaces_a_line_puts_its_own_there(tmp_path: Path) -> None:
    project = a_project(tmp_path)
    (tmp_path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    fix = EditFix(
        title="Repair the row.",
        applicability=Applicability.SAFE,
        edits=(Edit(file=Path("notes.txt"), line=2, old="two", new="three"),),
    )
    found = Finding(code=Code.CUE_MISSING, message="x", location=Location(where="notes.txt"), fix=fix)
    project.apply(found)
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "one\nthree\n"


def test_a_safe_edit_at_line_one_writes_the_file_the_fix_exists_to_create(tmp_path: Path) -> None:
    """The cue file's own fix writes the whole of one, so the file it names is not there to be read."""
    project = a_project(tmp_path)
    fix = EditFix(
        title="Write the cue file.",
        applicability=Applicability.SAFE,
        edits=(Edit(file=Path("cues.json"), line=1, new='{"sections": {}}'),),
    )
    found = Finding(code=Code.CUE_MISSING, message="x", location=Location(where="cues.json"), fix=fix)
    result = project.apply(found)
    assert result.fixes[0].applied and result.fixes[0].files == (Path("cues.json"),)
    assert (tmp_path / "cues.json").read_text(encoding="utf-8") == '{"sections": {}}\n'


def test_an_edit_into_a_file_that_is_not_there_says_so_rather_than_raising(tmp_path: Path) -> None:
    """A fix that changes a line needs the lines, and a caller is told that in a sentence it can print."""
    project = a_project(tmp_path)
    fix = EditFix(
        title="Repair the row.",
        applicability=Applicability.SAFE,
        edits=(Edit(file=Path("notes.txt"), line=4, old="two", new="three"),),
    )
    found = Finding(code=Code.CUE_MISSING, message="x", location=Location(where="notes.txt"), fix=fix)
    outcome = project.apply(found).fixes[0]
    assert not outcome.applied and "notes.txt" in outcome.why
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
        assert origin.url.startswith("http://127.0.0.1:") and origin.port > 0
        assert origin.result.root == Path()
    with pytest.raises(OSError, match="Bad file descriptor|closed"):
        origin._server.socket.getsockname()
