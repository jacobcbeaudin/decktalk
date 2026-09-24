"""The surface table: one row per command, and the test is total in both directions.

Four sets used to be asserted against each other here, which meant four places to add a row and four
ways to forget one. `SURFACE` is that assertion as one table. Each row names a command, the callable
that implements it, the result that callable returns, whether the command opens a run and whether it
writes a file, and what drives it. A stage added without a command, a command added without a result,
or a result added without either, fails here by name.

Nothing in this file fakes a tool. A row whose command needs ffmpeg, Chromium or a paid voice names
the mirrored test that drives it through its real stage instead, and a test holds that file to
naming the result, so the table stays total without a second copy of the stage suite and without
reaching into fixtures this directory cannot see. Every other row is driven here for real, and each
one is read back the way a caller reads it: the JSON is one flat object with four reserved keys, the
result round-trips through its own schema, the library printed nothing, and a subscriber collected
typed events that validate back and pair.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import typer
from pydantic import BaseModel, TypeAdapter

import decktalk
from decktalk import settings
from decktalk.errors import DeckTalkError
from decktalk.events import EVENTS, Event, Line, RunDone, RunStart
from decktalk.findings import Applicability, Certainty, Code, Finding, Location, SettingFix
from decktalk.machine import Machine
from decktalk.pipeline import Stage
from decktalk.project import Origin, Project
from decktalk.results import (
    RESULTS,
    SCHEMA,
    ApplyResult,
    AssembleResult,
    BuildResult,
    CheckResult,
    ClipResult,
    ConfigExplainResult,
    ConfigGetResult,
    ConfigListResult,
    ConfigSetResult,
    ConfigUnsetResult,
    CueResult,
    DoctorResult,
    ErrorResult,
    InitResult,
    InstallResult,
    NarrateResult,
    RecordResult,
    Result,
    Scope,
    ServeResult,
    SoundscapeResult,
    StatusResult,
    StoryboardResult,
    VerifyResult,
    WordsResult,
)
from support.paths import REPO

SOME_CODE = Code.CUE_OFF
"""One finding code, for the rows where a finding is the input rather than the subject."""

RESERVED = ("schema", "ok", "findings", "error")
"""The four keys the base reserves, which every result carries and no subclass may spell again."""

RETIRED = ("command", "exit_code", "summary", "data", "result")
"""Keys the 0.4 envelope carried. The JSON is one flat object now, so none of them may come back."""

SCHEMAS = REPO / "schemas" / "v1" / "results"
"""Where the committed JSON Schema of each result lives, one file per name `decktalk schema` prints."""

HERE = "here"
"""What a row's driver says when this file drives it for real, which needs no tool of any kind."""

CLI_TESTS = "tests/decktalk/cli"
"""Where a row the command line owns whole is driven, which is a directory because T8 names its own files."""

APP_NAMES = ("decktalk.cli:app", "decktalk.cli.parser:app", "decktalk.cli.main:app")
"""Where the Typer app may live. The command set is read from it, so a miss names all three."""


@dataclass(frozen=True)
class Row:
    """One command, what implements it, what it returns, what it opens and what drives it."""

    command: str
    call: str  # a dotted path this file resolves, or an empty string when the CLI composes the result
    result: type[Result] | None
    opens_run: bool
    writes: bool
    driver: str  # `HERE`, or the test file or directory that drives this row through its real stage
    returns_its_result: bool = True  # false when the callable returns a library record the CLI renders
    note: str = ""  # why this row is not a plain callable returning its own result


SURFACE: tuple[Row, ...] = (
    Row("init", "decktalk:init", InitResult, True, True, HERE),
    Row("install", "decktalk.machine:Machine.install", InstallResult, True, False, "tests/decktalk/test_machine.py"),
    Row("doctor", "decktalk.machine:Machine.doctor", DoctorResult, True, True, HERE),
    Row("status", "decktalk.project:Project.status", StatusResult, True, False, HERE),
    Row("check", "decktalk.project:Project.check", CheckResult, True, True, HERE),
    Row("words", "decktalk.project:Project.words", WordsResult, True, False, "tests/decktalk/stages/test_words.py"),
    Row(
        "storyboard",
        "decktalk.project:Project.storyboard",
        StoryboardResult,
        True,
        True,
        "tests/decktalk/stages/test_storyboard.py",
    ),
    Row(
        "serve",
        "decktalk.project:Project.serve",
        ServeResult,
        True,
        False,
        HERE,
        returns_its_result=False,
        note="It returns an Origin the caller holds open, and the result is what that Origin carries.",
    ),
    Row("build", "decktalk.project:Project.build", BuildResult, True, True, "tests/decktalk/stages/test_build.py"),
    Row("clip", "decktalk.project:Project.clip", ClipResult, True, True, "tests/decktalk/stages/test_clip.py"),
    Row(
        Stage.NARRATE.value,
        "decktalk.project:Project.narrate",
        NarrateResult,
        True,
        True,
        "tests/decktalk/stages/narrate/test_narrate.py",
    ),
    Row(
        Stage.CUE.value,
        "decktalk.project:Project.cue",
        CueResult,
        True,
        True,
        "tests/decktalk/stages/cue/test_cue.py",
    ),
    Row(
        Stage.RECORD.value,
        "decktalk.project:Project.record",
        RecordResult,
        True,
        True,
        "tests/decktalk/stages/record/test_record.py",
    ),
    Row(
        Stage.SOUNDSCAPE.value,
        "decktalk.project:Project.soundscape",
        SoundscapeResult,
        True,
        True,
        "tests/decktalk/stages/soundscape/test_soundscape.py",
    ),
    Row(
        Stage.ASSEMBLE.value,
        "decktalk.project:Project.assemble",
        AssembleResult,
        True,
        True,
        "tests/decktalk/stages/assemble/test_assemble.py",
    ),
    Row(
        Stage.VERIFY.value,
        "decktalk.project:Project.verify",
        VerifyResult,
        True,
        False,
        "tests/decktalk/stages/verify/test_verify.py",
    ),
    Row(
        "config list",
        "",
        ConfigListResult,
        False,
        False,
        CLI_TESTS,
        note="The CLI joins the key registry with the project's own layers, so no one call returns this.",
    ),
    Row(
        "config get",
        "",
        ConfigGetResult,
        False,
        False,
        CLI_TESTS,
        note="The CLI joins the key registry with the project's own layers, so no one call returns this.",
    ),
    Row(
        "config set",
        "decktalk.settings:write",
        ConfigSetResult,
        False,
        True,
        HERE,
        returns_its_result=False,
        note="The writer returns a SettingWrite, which is the library's record, and the CLI renders it.",
    ),
    Row(
        "config unset",
        "decktalk.settings:unset",
        ConfigUnsetResult,
        False,
        True,
        HERE,
        returns_its_result=False,
        note="The remover is the writer's opposite and belongs beside it, because editing a file is library work.",
    ),
    Row(
        "config explain",
        "decktalk.explain:explain",
        ConfigExplainResult,
        False,
        False,
        HERE,
        returns_its_result=False,
        note="The explainer returns an Explanation, which is the library's record, and the CLI renders it.",
    ),
    Row(
        "schema",
        "",
        None,
        False,
        False,
        CLI_TESTS,
        note="It prints the contract document itself, which is the one exemption from the reserved keys.",
    ),
)
"""Every command, the callable that implements it, the result it returns and what drives it."""


NO_COMMAND: tuple[Row, ...] = (
    Row(
        "",
        "decktalk.project:Project.apply",
        ApplyResult,
        True,
        True,
        HERE,
        note="A fix is applied from a finding a caller already holds, so a command would have nothing to take.",
    ),
    Row(
        "",
        "",
        ErrorResult,
        False,
        False,
        CLI_TESTS,
        note="It is what a refused command line fills, so it belongs to every command and to none of them.",
    ),
)
"""Every result that no command returns, each with the sentence saying why it has no row above."""


ALL_ROWS = (*SURFACE, *NO_COMMAND)
MODEL_ROWS = tuple(row for row in ALL_ROWS if row.result is not None)
DRIVEN_ELSEWHERE = tuple(row for row in ALL_ROWS if row.driver != HERE)
IDS = [row.command or row.result.__name__ for row in MODEL_ROWS]


# ---- resolving what a row names ----------------------------------------------------------


def resolve(call: str) -> object:
    """The object a row's dotted path names, as `module:name` or `module:Class.method`."""
    module_name, _, attribute = call.partition(":")
    found: object = importlib.import_module(module_name)
    for part in attribute.split("."):
        found = getattr(found, part)
    return found


def import_everything() -> list[str]:
    """Import every module of the package, and give back the ones that would not import.

    A module that does not import could be hiding a result, so the walk reports rather than passes.
    """
    broken: list[str] = []
    for info in pkgutil.walk_packages(decktalk.__path__, f"{decktalk.__name__}."):
        # `__main__` runs the command line when it is imported, which is the whole of what it is for.
        if info.name.endswith(".__main__"):
            continue
        try:
            importlib.import_module(info.name)
        except ImportError as missing:
            broken.append(f"{info.name}: {missing}")
    return broken


def every_result_class() -> set[type[Result]]:
    """Every `Result` subclass the imported package holds, however deep it is subclassed."""

    def below(cls: type[Result]) -> set[type[Result]]:
        return {sub for child in cls.__subclasses__() for sub in {child, *below(child)}}

    return below(Result)


def models_within(model: type[BaseModel], seen: set[type[BaseModel]]) -> Iterator[type[BaseModel]]:
    """Every model reachable from one model's fields, which is the whole of what it publishes."""
    if model in seen:
        return
    seen.add(model)
    yield model
    for field in model.model_fields.values():
        annotation = field.annotation
        candidates = [annotation, *(getattr(annotation, "__args__", ()) or ())]
        for candidate in candidates:
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                yield from models_within(candidate, seen)


# ---- the table is total in both directions -----------------------------------------------


def test_every_result_class_is_in_exactly_one_table():
    """A result nothing drives and nothing returns is a shape no reader will ever meet."""
    broken = import_everything()
    declared = [row.result for row in ALL_ROWS if row.result is not None]
    assert len(declared) == len(set(declared)), "a result is claimed by two rows"
    assert set(declared) == every_result_class()
    assert broken == [], "these modules do not import, so a result could be hiding in one:\n" + "\n".join(broken)


def test_the_schema_names_and_the_tables_name_the_same_results():
    """`RESULTS` is what `decktalk schema NAME` reads, so it cannot hold a result no row claims."""
    assert set(RESULTS.values()) == {row.result for row in ALL_ROWS if row.result is not None}


@pytest.mark.parametrize("row", MODEL_ROWS, ids=IDS)
def test_every_row_names_a_result_with_a_committed_schema(row: Row):
    """An agent reads the contract from the schema directory, so every result has a file there."""
    assert issubclass(row.result, Result)
    name = next(key for key, model in RESULTS.items() if model is row.result)
    assert (SCHEMAS / f"{name}.json").is_file(), f"{name}.json is missing from schemas/v1/results/"


@pytest.mark.parametrize("row", [row for row in ALL_ROWS if row.call], ids=lambda row: row.call)
def test_every_row_names_a_callable_that_is_public_and_returns_its_result(row: Row):
    """The callable and the result are one fact, so a row that names the wrong one fails here."""
    try:
        found = resolve(row.call)
    except (ImportError, AttributeError) as missing:
        pytest.fail(f"{row.command or row.result} names {row.call}, which does not resolve: {missing}")
    assert callable(found), row.call
    assert not row.call.rpartition(".")[2].startswith("_"), f"{row.call} is private, so no caller may reach it"


@pytest.mark.parametrize(
    "row",
    [row for row in ALL_ROWS if row.call and row.result is not None and row.returns_its_result],
    ids=lambda row: row.call,
)
def test_a_plain_row_returns_the_result_it_declares(row: Row):
    """Most rows are a callable that returns its own result, which is the ordinary shape."""
    returned = getattr(resolve(row.call), "__annotations__", {}).get("return")
    assert returned == row.result.__name__, f"{row.call} returns {returned} and the table says {row.result.__name__}"


def test_serve_returns_an_origin_that_carries_its_result():
    """R43 gives `serve` an object a caller holds open, so the result is what that object carries."""
    assert Project.serve.__annotations__.get("return") == Origin.__name__
    assert Origin.__init__.__annotations__.get("result") == ServeResult.__name__


@pytest.mark.parametrize("row", MODEL_ROWS, ids=IDS)
def test_the_run_and_the_written_fields_are_declared_exactly_where_the_table_says(row: Row):
    """Founder decision 13: four keys on the base, and these two declared by the commands that earn them."""
    declared = row.result.model_fields
    assert ("run" in declared) == row.opens_run, f"{row.result.__name__} and the table disagree about run"
    assert ("written" in declared) == row.writes, f"{row.result.__name__} and the table disagree about written"


def subject_of(row: Row) -> tuple[str, ...]:
    """What a file that drives this row has to name, which is its result, its callable or its command.

    The `schema` row carries no result at all, because it prints the contract document itself, which
    is the one envelope exemption in the product. Its command is the name a driver has to hold.
    """
    callable_name = row.call.rpartition(".")[2] or row.call.rpartition(":")[2]
    return tuple(name for name in (named(row), callable_name) if name)


def named(row: Row) -> str:
    """One row by the name a message calls it, which is its result unless it answers with none."""
    return row.result.__name__ if row.result is not None else row.command


@pytest.mark.parametrize("row", DRIVEN_ELSEWHERE, ids=[f"{row.command or 'error'}" for row in DRIVEN_ELSEWHERE])
def test_a_row_driven_elsewhere_names_a_file_that_drives_it(row: Row):
    """A row this file cannot drive without a tool names the test that runs its real stage."""
    path = REPO / row.driver
    files = sorted(path.glob("test_*.py")) if path.is_dir() else [path]
    assert files and all(one.is_file() for one in files), f"{row.driver} drives {named(row)} and is not there"
    text = "\n".join(one.read_text(encoding="utf-8") for one in files)
    wanted = subject_of(row)
    assert any(name in text for name in wanted), (
        f"{row.driver} is named as the driver of {named(row)} and names none of {wanted}"
    )


@pytest.mark.parametrize(
    "row", [row for row in ALL_ROWS if row.note], ids=[row.command or "apply" for row in ALL_ROWS if row.note]
)
def test_every_row_that_is_not_a_plain_call_says_why(row: Row):
    """An exception is designed when it is written down, and an unexplained one is an accident."""
    assert row.note.endswith("."), row.note


def typer_commands() -> set[str]:
    """Every command the Typer app publishes, with a group's subcommands spelled as the CLI takes them."""
    for name in APP_NAMES:
        try:
            app = resolve(name)
        except (ImportError, AttributeError):
            continue
        command = typer.main.get_command(app)
        found: set[str] = set()
        for label, sub in getattr(command, "commands", {}).items():
            children = getattr(sub, "commands", {})
            found |= {f"{label} {child}" for child in children} if children else {label}
        return found
    pytest.fail(f"no Typer app answered to any of {', '.join(APP_NAMES)}, so the command set cannot be read")


def test_the_command_set_of_the_app_is_the_surface_table():
    """The fourth set: a command the app publishes and the table does not know is undiscoverable."""
    assert typer_commands() == {row.command for row in SURFACE}


# ---- what every result promises a reader -------------------------------------------------


@pytest.mark.parametrize("row", MODEL_ROWS, ids=IDS)
def test_the_schema_is_one_flat_object_with_the_four_reserved_keys(row: Row):
    """The founder's decided contract: its own fields plus four keys, with no envelope around them."""
    schema = row.result.model_json_schema(by_alias=True)
    properties = schema["properties"]
    assert set(RESERVED) <= set(properties), sorted(set(RESERVED) - set(properties))
    assert not set(RETIRED) & set(properties), sorted(set(RETIRED) & set(properties))
    assert properties["schema"]["const"] == SCHEMA
    for name, definition in schema.get("$defs", {}).items():
        nested = set(definition.get("properties", {}))
        assert not set(RESERVED) <= nested, f"{name} is a second envelope inside {row.result.__name__}"


@pytest.mark.parametrize("row", MODEL_ROWS, ids=IDS)
def test_every_field_a_result_publishes_carries_its_sentence(row: Row):
    """`Field(description=...)` is the one home of each key's sentence, so a key without one is mute."""
    mute = [
        f"{model.__name__}.{name}"
        for model in models_within(row.result, set())
        for name, field in model.model_fields.items()
        if not field.description
    ]
    assert mute == [], mute


@pytest.mark.parametrize(
    "row", [row for row in MODEL_ROWS if row.opens_run], ids=[r.command or "apply" for r in MODEL_ROWS if r.opens_run]
)
def test_the_run_id_is_declared_volatile(row: Row):
    """R11: a value that differs between two identical runs is declared, so a golden read drops it."""
    schema = row.result.model_json_schema(by_alias=True)
    assert schema["properties"]["run"].get("volatile") is True, f"{row.result.__name__}.run is not declared volatile"


@pytest.mark.parametrize("row", MODEL_ROWS, ids=IDS)
def test_a_measured_duration_is_declared_volatile(row: Row):
    """The same rule as the run id, because a wall-clock second is measured and never reproduced."""
    schema = row.result.model_json_schema(by_alias=True)
    for name in ("seconds", "film_seconds"):
        field = row.result.model_fields.get(name)
        if field is not None and field.json_schema_extra:
            assert schema["properties"][name].get("volatile") is True, name


def test_a_finding_carries_everything_a_reader_dispatches_on():
    """R9: the code, the sentence, the certainty, the object judged, the fix and the page that explains it."""
    declared = set(Finding.model_fields)
    assert {"code", "message", "certainty", "location", "fix", "url"} <= declared
    assert "where" in Location.model_fields
    assert Location.model_fields["where"].is_required(), "the object a finding judged is never null"


def test_ok_is_false_exactly_when_a_judgement_is_certain():
    """Must 5, in the shape 0.5.0 gives it: one rule every command shares rather than a flag per result."""
    sure = Finding(code=SOME_CODE, message="A cue landed late.", location=Location(where="1.1:first"))
    assert sure.certainty is Certainty.CERTAIN
    assert ErrorResult(ok=False, findings=(sure,)).ok is False
    assert ErrorResult(ok=True).ok is True


# ---- the rows this file drives for real --------------------------------------------------


@pytest.fixture
def machine(tmp_path: Path) -> Machine:
    """A machine whose cache is this test's own directory, so nothing reaches the author's real one."""
    return Machine.from_environment(overrides=(("tools.cache_dir", str(tmp_path / "cache")),))


@pytest.fixture
def collected(machine: Machine) -> Iterator[list[Event]]:
    """Every event the library emitted while a driven row ran, in the order a renderer would see it."""
    seen: list[Event] = []
    with machine.events.subscribe(seen.append):
        yield seen


@pytest.fixture
def project(tmp_path: Path, machine: Machine) -> Project:
    """A real project, written by `init`, which is the state every other driven row starts from."""
    decktalk.init(tmp_path / "film", machine=machine)
    return decktalk.open(tmp_path / "film", machine=machine)


def read_back(result: Result) -> dict[str, Any]:
    """One result as a caller meets it: dumped to JSON, validated back, and read as an object."""
    text = result.model_dump_json(by_alias=True)
    assert type(result).model_validate_json(text) == result, "a result does not round-trip through its own JSON"
    return json.loads(text)


def driven(row: Row, project: Project, machine: Machine) -> Result:
    """Run one row for real, with no tool and nothing faked."""
    if row.command == "init":
        return decktalk.init(project.root.parent / "second", machine=machine)
    if row.command == "doctor":
        return machine.doctor()
    if row.command == "status":
        return project.status()
    if row.command == "check":
        return project.check(pages=False)
    if row.command == "serve":
        with project.serve() as origin:
            return origin.result
    raise AssertionError(f"{row.command} is marked driven here and this file does not drive it")


RUNNABLE = (InitResult, DoctorResult, StatusResult, CheckResult, ServeResult)
"""The rows `driven` below runs in one loop, because each is a call with no argument of its own."""

OWN_DRIVER = (ConfigSetResult, ConfigUnsetResult, ConfigExplainResult, ApplyResult)
"""The rows with a driver of their own, because each takes an input or renders a record the loop cannot."""

DRIVEN_HERE = tuple(row for row in SURFACE if row.result in RUNNABLE)


def test_every_row_marked_driven_here_really_is_driven_here():
    """The mark means a real call in this file, so a row that carries it and runs nowhere fails."""
    assert {row.result for row in ALL_ROWS if row.driver == HERE} == {*RUNNABLE, *OWN_DRIVER}


@pytest.mark.parametrize("row", DRIVEN_HERE, ids=[row.command for row in DRIVEN_HERE])
def test_a_driven_row_returns_its_result_as_one_flat_object(
    row: Row, project: Project, machine: Machine, capsys: pytest.CaptureFixture[str]
):
    """The whole contract on a real call: the type, the four keys, the round trip and the silence."""
    capsys.readouterr()
    result = driven(row, project, machine)
    assert isinstance(result, row.result)
    payload = read_back(result)
    assert payload["schema"] == SCHEMA
    assert set(RESERVED) <= set(payload)
    assert not set(RETIRED) & set(payload)
    assert ("run" in payload) == row.opens_run
    assert ("written" in payload) == row.writes
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == "", "the library printed, and nothing in the library may print"


def test_a_driven_call_opens_a_run_and_closes_it(project: Project, collected: list[Event]):
    """Every top-level call opens a run, so a renderer that subscribed sees a start and a finish."""
    project.status()
    names = [event.event for event in collected]
    started = [name for name in names if EVENTS[name] is RunStart]
    finished = [name for name in names if EVENTS[name] is RunDone]
    assert len(started) == len(finished) == 1


def test_every_event_a_driven_call_emitted_validates_back(project: Project, collected: list[Event]):
    """An event is data on a wire, so every line a subscriber saw is readable by the same reader."""
    project.check(pages=False)
    reader = TypeAdapter(Line)
    assert collected, "a call that opened a run emitted nothing"
    for event in collected:
        assert reader.validate_json(event.model_dump_json()) is not None
        assert event.event in EVENTS, event.event


def test_the_settings_writer_reports_what_a_config_set_would_change(project: Project, capsys):
    """`config set` is the writer's record rendered, so the writer is what this row really drives."""
    capsys.readouterr()
    written = settings.write(project.root / "decktalk.toml", "video.width", "1280", scope=Scope.PROJECT, dry_run=True)
    assert written.key == "video.width"
    assert written.dry_run is True
    assert set(ConfigSetResult.model_fields) >= set(type(written).model_fields) - {"line", "shadowed"}
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == ""


def test_the_settings_remover_reports_what_a_config_unset_would_change(project: Project):
    """`config unset` takes the layer below back, and writing a validated file is library work."""
    removed = settings.unset(project.root / "decktalk.toml", "video.width", scope=Scope.PROJECT)
    assert removed.keys == ("video.width",)
    assert set(ConfigUnsetResult.model_fields) >= {"keys", "scope", "file"}


def test_the_explainer_reports_what_a_config_explain_would_render(project: Project, capsys):
    """`config explain` is the explainer's record rendered, so the explainer is what this row drives."""
    capsys.readouterr()
    explanation = decktalk.explain("video.width", project=project.root)
    assert explanation.key == "video.width"
    assert set(ConfigExplainResult.model_fields) >= {"key", "value", "default", "layer", "docs"}
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == ""


def test_applying_a_fix_reports_what_it_changed(project: Project, capsys: pytest.CaptureFixture[str]):
    """`apply` has no command, so this is its driver: a finding a caller holds, applied for real."""
    capsys.readouterr()
    finding = Finding(
        code=SOME_CODE,
        message="The frame is narrower than the deck draws.",
        location=Location(where="1.1:first"),
        fix=SettingFix(
            title="Widen the frame.",
            applicability=Applicability.SAFE,
            key="video.width",
            value="1280",
        ),
    )
    applied = project.apply(finding)
    assert isinstance(applied, ApplyResult)
    assert [outcome.applied for outcome in applied.fixes] == [True]
    assert Path("decktalk.toml") in applied.written
    payload = read_back(applied)
    assert set(RESERVED) <= set(payload)
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == ""


def test_a_refused_call_raises_a_typed_error_rather_than_returning_one(project: Project):
    """`ErrorResult` is what the command line fills from an exception, so the library raises instead."""
    with pytest.raises(DeckTalkError) as refused:
        project.words()
    assert refused.value.code is not None
    assert str(refused.value)
