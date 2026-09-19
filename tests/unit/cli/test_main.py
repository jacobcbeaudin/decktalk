"""`main`: what each ending looks like to a caller, in the exit code and in the one envelope."""

from __future__ import annotations

import json
import logging

from decktalk.cli import dispatch, main
from decktalk.errors import ConfigError, ToolError


def test_a_usage_error_exits_2_and_says_so_in_the_envelope(capsys):
    assert main(["nonesuch"]) == 2
    assert "error[USAGE]:" in capsys.readouterr().err
    assert main(["verify", "--nope", "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert (doc["command"], doc["ok"], doc["exit_code"]) == ("verify", False, 2)
    assert doc["error"]["code"] == "USAGE" and "--nope" in doc["error"]["message"]
    assert doc["error"]["hint"] and doc["verify"] is None and doc["findings"]["items"] == []


def test_an_argument_rule_is_a_usage_error_and_not_a_crash(capsys):
    assert main(["screenshots", "--after", "3.1eq", "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["error"]["code"] == "USAGE" and "--after needs exactly one --slide" in doc["error"]["message"]


def test_every_decktalk_error_exits_3_with_the_error_slot_filled(monkeypatch, capsys):
    """Exit 3 means stop and tell the user. A skill must never read it as a finding to fix."""

    def boom(opts):
        raise ToolError("ffmpeg is not on PATH")

    monkeypatch.setitem(dispatch.HANDLERS, "verify", boom)
    assert main(["verify", "--json"]) == 3
    doc = json.loads(capsys.readouterr().out)
    assert (doc["ok"], doc["exit_code"]) == (False, 3)
    assert doc["error"] == {
        "code": "TOOL",
        "message": "ffmpeg is not on PATH",
        "hint": None,
        "path": None,
        "line": None,
    }
    assert doc["findings"] == {"certain": 0, "uncertain": 0, "items": []}
    assert main(["verify"]) == 3
    assert capsys.readouterr().err == "error[TOOL]: ffmpeg is not on PATH\n"


def test_an_unexpected_exception_exits_3_under_the_internal_code(monkeypatch, capsys):
    """A bug is not a finding either, and `--exit-zero` cannot turn it into a pass."""

    def boom(opts):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setitem(dispatch.HANDLERS, "align", boom)
    assert main(["align", "--json", "--exit-zero"]) == 3
    doc = json.loads(capsys.readouterr().out)
    assert doc["exit_code"] == 3 and doc["ok"] is False and doc["align"] is None
    assert doc["error"]["code"] == "INTERNAL" and doc["error"]["message"] == "division by zero"
    assert main(["align"]) == 3
    assert capsys.readouterr().err == "error[INTERNAL]: division by zero\n"


def test_verbose_adds_the_traceback_of_a_bug_and_still_exits_3(monkeypatch, capsys):
    """A developer keeps the traceback, and exit 1 would tell a caller the project is what to fix."""

    def boom(opts):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setitem(dispatch.HANDLERS, "align", boom)
    assert main(["align", "-v", "--json"]) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "INTERNAL"
    assert "Traceback" in captured.err and "ZeroDivisionError" in captured.err


def test_an_error_names_its_file_relative_to_the_project_it_was_asked_for(tmp_path, capsys):
    """`error.path` is project-relative, which is the only form a caller can act on."""
    assert main(["preflight", "-p", str(tmp_path / "nowhere"), "--json"]) == 3
    slot = json.loads(capsys.readouterr().out)["error"]
    assert (slot["code"], slot["path"]) == ("CONFIG", "decktalk.toml")
    assert slot["message"] == "decktalk.toml is not there." and "--project DIR" in slot["hint"]


def test_a_quiet_run_leaves_only_warnings_on_the_logger(tmp_path, capsys):
    main(["status", "-q", "-p", str(tmp_path / "nowhere")])
    assert logging.getLogger("decktalk").level == logging.WARNING
    main(["status", "-v", "-p", str(tmp_path / "nowhere")])
    assert logging.getLogger("decktalk").level == logging.DEBUG


def test_an_error_hint_reaches_a_person_too(monkeypatch, capsys):
    def boom(opts):
        error = ConfigError("cues.json is not valid JSON")
        error.hint = "run `decktalk status`"
        raise error

    monkeypatch.setitem(dispatch.HANDLERS, "align", boom)
    assert main(["align"]) == 3
    assert capsys.readouterr().err.splitlines() == [
        "error[CONFIG]: cues.json is not valid JSON",
        "  hint: run `decktalk status`",
    ]


def test_an_interrupt_exits_130_and_promises_no_envelope(monkeypatch, capsys):
    def stop(opts):
        raise KeyboardInterrupt

    monkeypatch.setitem(dispatch.HANDLERS, "record", stop)
    assert main(["record", "--json"]) == 130
    assert capsys.readouterr().out == ""
