"""The command line as a whole: its tree, its entry point, its help and the JSON it prints.

Which command answers with which result, and which library call is behind it, is the one surface
table in `tests/contract/test_results.py`, which holds the command line to it in both directions.
"""

from __future__ import annotations

import json
import subprocess
import sys

import jsonschema

from decktalk import __version__
from decktalk import project as projects
from decktalk.cli import main
from decktalk.errors import NotBuiltError
from support.paths import REPO

from .conftest import commands

EXPECTED_COMMANDS = 18
"""How many commands the tree has, counting the nested group as the one command a reader types."""


def test_the_tree_has_eighteen_commands() -> None:
    top = {name.split(" ")[0] for name in commands()}
    assert len(top) == EXPECTED_COMMANDS


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
        committed = json.loads((REPO / "schemas" / "v1" / "results" / f"{name}.json").read_text(encoding="utf-8"))
        jsonschema.validate(written, committed)


def test_the_json_of_a_refusal_validates_against_the_committed_error_schema(run, project) -> None:
    project(words=NotBuiltError("build/narrate/takes.json is not there."))
    written = json.loads(run("words", "--json").out)
    committed = json.loads((REPO / "schemas" / "v1" / "results" / "error.json").read_text(encoding="utf-8"))
    jsonschema.validate(written, committed)
