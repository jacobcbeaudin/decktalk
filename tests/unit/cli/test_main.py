"""`main`: what each ending looks like to a caller, in the exit code and in the one envelope."""

from __future__ import annotations

import logging

from decktalk.cli import dispatch, main
from decktalk.cli.schema import ErrorSlot, read_envelope
from decktalk.errors import ConfigError, ErrorCode, ToolError
from decktalk.pipeline import Stage
from decktalk.verdicts import Verdict


def test_a_usage_error_exits_2_and_says_so_in_the_envelope(capsys):
    assert main(["nonesuch"]) == 2
    assert "error[USAGE]:" in capsys.readouterr().err
    assert main(["verify", "--nope", "--json"]) == 2
    doc = read_envelope(capsys.readouterr().out)
    assert (doc.command, doc.ok, doc.exit_code) == (Stage.VERIFY.value, False, 2)
    assert doc.error is not None and doc.error.code is ErrorCode.USAGE and "--nope" in doc.error.message
    assert doc.error.hint and doc.payload is None and doc.findings.items == []


def test_an_argument_rule_is_a_usage_error_and_not_a_crash(capsys):
    assert main(["screenshots", "--after", "3.1eq", "--json"]) == 2
    error = read_envelope(capsys.readouterr().out).error
    assert error is not None and error.code is ErrorCode.USAGE
    assert "--after needs exactly one --slide" in error.message


def test_every_decktalk_error_exits_3_with_the_error_slot_filled(monkeypatch, capsys):
    """Exit 3 means stop and tell the user. A skill must never read it as a finding to fix."""

    def boom(opts):
        raise ToolError("ffmpeg is not on PATH")

    monkeypatch.setitem(dispatch.HANDLERS, "verify", boom)
    assert main(["verify", "--json"]) == 3
    doc = read_envelope(capsys.readouterr().out)
    assert (doc.ok, doc.exit_code) == (False, 3)
    assert doc.error == ErrorSlot(code=ErrorCode.TOOL, message="ffmpeg is not on PATH", hint=None, path=None, line=None)
    assert (doc.findings.certain, doc.findings.uncertain, doc.findings.items) == (0, 0, [])
    assert main(["verify"]) == 3
    assert capsys.readouterr().err == "error[TOOL]: ffmpeg is not on PATH\n"


def test_an_unexpected_exception_exits_3_under_the_internal_code(monkeypatch, capsys):
    """A bug is not a finding either, and `--exit-zero` cannot turn it into a pass."""

    def boom(opts):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setitem(dispatch.HANDLERS, "align", boom)
    assert main(["align", "--json", "--exit-zero"]) == 3
    doc = read_envelope(capsys.readouterr().out)
    assert doc.exit_code == 3 and doc.ok is False and doc.payload is None
    assert doc.error == ErrorSlot(code=ErrorCode.INTERNAL, message="division by zero", hint=None, path=None, line=None)
    assert main(["align"]) == 3
    assert capsys.readouterr().err == "error[INTERNAL]: division by zero\n"


def test_verbose_adds_the_traceback_of_a_bug_and_still_exits_3(monkeypatch, capsys):
    """A developer keeps the traceback, and exit 1 would tell a caller the project is what to fix."""

    def boom(opts):
        raise ZeroDivisionError("division by zero")

    monkeypatch.setitem(dispatch.HANDLERS, "align", boom)
    assert main(["align", "-v", "--json"]) == 3
    captured = capsys.readouterr()
    error = read_envelope(captured.out).error
    assert error is not None and error.code is ErrorCode.INTERNAL
    assert "Traceback" in captured.err and "ZeroDivisionError" in captured.err


def test_an_error_names_its_file_relative_to_the_project_it_was_asked_for(tmp_path, capsys):
    """`error.path` is project-relative, which is the only form a caller can act on."""
    assert main(["preflight", "-p", str(tmp_path / "nowhere"), "--json"]) == 3
    slot = read_envelope(capsys.readouterr().out).error
    assert slot is not None and (slot.code, slot.path) == (ErrorCode.CONFIG, "decktalk.toml")
    assert slot.message == "decktalk.toml is not there." and slot.hint is not None and "--project DIR" in slot.hint


def test_a_project_file_that_is_not_there_is_the_one_thing_status_still_reports(tmp_path, capsys):
    """Every other command stops, and `status` reports, because reading the file is its work."""
    assert main(["status", "-p", str(tmp_path / "nowhere"), "--json"]) == 1
    doc = read_envelope(capsys.readouterr().out)
    assert doc.error is None and doc.findings.certain == 1
    [row] = doc.findings.items
    assert (row.verdict, row.where) == (Verdict.MISSING, "decktalk.toml")


def test_a_project_file_that_will_not_parse_is_reported_and_never_called_missing(tmp_path, capsys):
    (tmp_path / "decktalk.toml").write_text("[project\nname = 'x'\n", encoding="utf-8")
    assert main(["status", "-p", str(tmp_path), "--json"]) == 1
    [row] = read_envelope(capsys.readouterr().out).findings.items
    assert (row.verdict, row.where) == (Verdict.UNREADABLE, "decktalk.toml")
    assert "is not valid TOML" in row.detail and "bracket left open" in row.detail
    assert main(["preflight", "-p", str(tmp_path), "--json"]) == 3


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
