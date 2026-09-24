"""The whole surface: every command, the library call behind it, and the result it answers with.

`SURFACE` is the one table this suite holds the command line to, and it is total in both directions.
A command with no row is named here, a row with no command is named here, and a row whose callable
is not on `Project`, on `Machine` or in the module it names is named here. That is what stops a
command from being added without a library call, and a library call from quietly losing its command.
"""

from __future__ import annotations

import json
import subprocess
import sys

import jsonschema
import pytest

from decktalk import __version__
from decktalk import machine as machines
from decktalk import project as projects
from decktalk import settings as knobs
from decktalk.cli import catalog, main
from decktalk.errors import NotBuiltError
from decktalk.explain import explain
from decktalk.machine import Machine
from decktalk.project import Project
from decktalk.results import (
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
    InitResult,
    InstallResult,
    NarrateResult,
    RecordResult,
    Result,
    ServeResult,
    SoundscapeResult,
    StatusResult,
    StoryboardResult,
    VerifyResult,
    WordsResult,
)
from support.paths import REPO

SURFACE: dict[str, tuple[object, str, type[Result] | None]] = {
    "init": (machines, "init", InitResult),
    "install": (Machine, "install", InstallResult),
    "doctor": (Machine, "doctor", DoctorResult),
    "status": (Project, "status", StatusResult),
    "check": (Project, "check", CheckResult),
    "words": (Project, "words", WordsResult),
    "storyboard": (Project, "storyboard", StoryboardResult),
    "serve": (Project, "serve", ServeResult),
    "config list": (knobs, "KEYS", ConfigListResult),
    "config get": (knobs, "value_of", ConfigGetResult),
    "config set": (knobs, "write", ConfigSetResult),
    "config unset": (knobs, "unset", ConfigUnsetResult),
    "config explain": (sys.modules[explain.__module__], "explain", ConfigExplainResult),
    "schema": (catalog, "document", None),
    "narrate": (Project, "narrate", NarrateResult),
    "cue": (Project, "cue", CueResult),
    "record": (Project, "record", RecordResult),
    "soundscape": (Project, "soundscape", SoundscapeResult),
    "assemble": (Project, "assemble", AssembleResult),
    "verify": (Project, "verify", VerifyResult),
    "build": (Project, "build", BuildResult),
    "clip": (Project, "clip", ClipResult),
}
"""Every command, the library name that implements it, and the result it answers with.

`schema` answers with the contract document itself rather than a result, which is the one envelope
exemption in the product, so its row carries no model.
"""

EXPECTED_COMMANDS = 18
"""How many commands the tree has, counting the nested group as the one command a reader types."""


def commands() -> dict[str, dict[str, object]]:
    """Every command the parser really has, by the words a caller types to reach it."""
    return {str(row["command"]): row for row in catalog.walk()}


def test_every_command_has_a_row_and_every_row_has_a_command() -> None:
    assert set(commands()) == set(SURFACE)


def test_the_tree_has_eighteen_commands() -> None:
    top = {name.split(" ")[0] for name in commands()}
    assert len(top) == EXPECTED_COMMANDS


@pytest.mark.parametrize("name", sorted(SURFACE))
def test_every_row_names_a_library_call_that_is_there(name: str) -> None:
    holder, attribute, _ = SURFACE[name]
    assert hasattr(holder, attribute), f"{name} names {attribute}, which is not there"


@pytest.mark.parametrize("name", sorted(SURFACE))
def test_every_command_answers_with_the_result_its_row_names(name: str) -> None:
    model = SURFACE[name][2]
    printed = commands()[name]["result"]
    assert printed == (None if model is None else _name_of(model))


def _name_of(model: type[Result]) -> str:
    """The name `decktalk schema NAME` prints a result under, read back off the library's registry."""
    return catalog.NAMES[model]


def test_importing_the_command_line_loads_no_stage() -> None:
    """A browser and an encoder are loaded by the call that needs them, so `--help` costs a signature.

    It is asked in an interpreter of its own, because this one has already imported the stages
    through the suite around it and would answer about the suite rather than about the command line.
    """
    asked = "import decktalk.cli, sys; print([n for n in sys.modules if n.startswith('decktalk.stages')])"
    done = subprocess.run([sys.executable, "-c", asked], capture_output=True, text=True, check=True, cwd=REPO)
    assert done.stdout.strip() == "[]", f"importing cli loaded {done.stdout.strip()}"


def test_the_project_facade_keeps_the_one_edge_the_cli_calls() -> None:
    assert callable(projects.open)
    assert callable(projects.section_numbers)


def test_bare_decktalk_prints_the_help_on_stderr_and_exits_zero(run) -> None:
    ran = run()
    assert ran.exit_code == 0
    assert ran.out == ""
    assert "Set up this machine:" in ran.err


def test_the_version_is_the_package_version(run) -> None:
    ran = run("--version")
    assert ran.exit_code == 0
    assert ran.out.strip() == __version__


def test_the_entry_point_is_the_one_main(run) -> None:
    assert callable(main)
    assert run("schema", "error").exit_code == 0


def test_the_json_of_a_command_validates_against_its_committed_schema(run, project, answers) -> None:
    project(status=answers["status"], check=answers["check"])
    for command, name in (("status", "status"), ("check", "check")):
        written = json.loads(run(command, "--json").out)
        committed = json.loads((REPO / "schema" / "results" / f"{name}.json").read_text(encoding="utf-8"))
        jsonschema.validate(written, committed)


def test_the_json_of_a_refusal_validates_against_the_committed_error_schema(run, project) -> None:
    project(words=NotBuiltError("build/narrate/takes.json is not there."))
    written = json.loads(run("words", "--json").out)
    committed = json.loads((REPO / "schema" / "results" / "error.json").read_text(encoding="utf-8"))
    jsonschema.validate(written, committed)
