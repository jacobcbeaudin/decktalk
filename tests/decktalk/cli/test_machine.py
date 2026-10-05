"""The three commands about this machine, driven through the real parser against a faked machine."""

from __future__ import annotations

import json

import pytest

from decktalk.cli import machine as commands
from decktalk.findings import Threshold

from .conftest import ANSWERS, Fake, finding

MISSING = ANSWERS["doctor"].model_copy(update={"ok": False, "findings": (finding(),)})
"""A doctor that found a tool missing, which it exits 1 for and offers to fix."""


def test_init_writes_the_project_and_reports_what_it_chose(run, machine, monkeypatch, tmp_path) -> None:
    made = Fake()
    monkeypatch.setattr(commands.machines, "init", lambda *args, **keywords: _record(made, *args, **keywords))
    machine()
    ran = run("--no-input", "init", str(tmp_path / "demo"))
    assert ran.exit_code == 0
    assert made.called("init")["skills"] is True
    assert "Wrote" in ran.out


def _record(fake: Fake, *args: object, **keywords: object) -> object:
    """Stand in for the one module function `init` calls, and answer with a real result."""
    fake.calls.append(("init", args, keywords))
    return ANSWERS["init"]


def test_init_takes_the_flags_it_was_given_over_the_defaults(run, machine, monkeypatch, tmp_path) -> None:
    made = Fake()
    monkeypatch.setattr(commands.machines, "init", lambda *args, **keywords: _record(made, *args, **keywords))
    machine()
    run("--no-input", "init", str(tmp_path / "demo"), "--name", "lesson", "--example", "lesson", "--no-skills")
    asked = made.called("init")
    assert asked["name"] == "lesson"
    assert asked["example"] == "lesson"
    assert asked["skills"] is False


def test_init_into_a_directory_that_holds_files_refuses_and_names_overwrite(run, machine, tmp_path) -> None:
    (tmp_path / "already.txt").write_text("here", encoding="utf-8")
    machine()
    ran = run("init", str(tmp_path))
    assert ran.exit_code == 2
    assert "error[APPROVAL]" in ran.err
    assert "--overwrite" in ran.err


def test_install_fetches_and_reports_the_cache(run, machine, answers) -> None:
    made = machine(install=answers["install"])
    ran = run("install")
    assert ran.exit_code == 0
    assert "cache" in ran.out
    assert made.called("install")


def test_doctor_reports_the_machine_and_fetches_nothing(run, machine, answers) -> None:
    made = machine(doctor=answers["doctor"])
    ran = run("doctor")
    assert ran.exit_code == 0
    assert made.called("doctor")["measure"] is False
    assert "Python" in ran.out


def test_doctor_measures_only_when_asked(run, machine, answers) -> None:
    made = machine(doctor=answers["doctor"])
    run("doctor", "--measure")
    assert made.called("doctor")["measure"] is True


def test_doctor_is_judged_by_the_threshold_its_flags_name(run, machine) -> None:
    """The machine is handed the session's threshold, so `ok` and the exit code agree on doctor too."""
    made = machine(doctor=MISSING)
    ran = run("doctor", "--json", "--fail-on", "never")
    assert made.called("doctor")["threshold"] == Threshold(stop_on=None)
    assert ran.exit_code == 0
    assert json.loads(ran.out)["ok"] is True


def test_doctor_applies_nothing_without_a_terminal_and_without_the_flag(run, machine) -> None:
    made = machine(doctor=MISSING, apply=None)
    ran = run("doctor")
    assert ran.exit_code == 1
    assert [name for name, _, _ in made.calls] == ["doctor"]


def test_doctor_fix_applies_and_reads_the_machine_again(run, machine) -> None:
    made = machine(doctor=MISSING, apply=None)
    run("doctor", "--fix")
    assert [name for name, _, _ in made.calls] == ["doctor", "apply", "doctor"]


def test_doctor_help_says_when_it_fetches(run) -> None:
    """The help is the contract a person reads first, so it names the offer a terminal makes and the flags."""
    said = " ".join(run("doctor", "--help").out.split())
    assert "fetches nothing, and writes nothing" not in said, said
    assert "offers once to fetch what is missing" in said, said
    assert "--fix fetches it without asking, under --json or --no-input too" in said, said
    assert "--no-input and --json without --fix" in said, said


@pytest.mark.parametrize("flag", ["--json", "--no-input"])
def test_doctor_fix_fetches_under_json_and_no_input_as_its_help_says(run, machine, flag: str) -> None:
    made = machine(doctor=MISSING, apply=None)
    run(flag, "doctor", "--fix")
    assert [name for name, _, _ in made.calls] == ["doctor", "apply", "doctor"]
