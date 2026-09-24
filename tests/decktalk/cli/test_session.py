"""How a run ends: which rendering the terminal chose, what the exit code is, and what a prompt does."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from decktalk.cli.options import FailOn, When
from decktalk.cli.session import Globals, Session, Terminal
from decktalk.errors import ApprovalRequired, ErrorCode, InputError
from decktalk.findings import Certainty, Code
from decktalk.results import CheckResult, StatusResult, Voicing

from .conftest import Fake, finding, spend


def session(**flags: object) -> Session:
    """One session with the flags a test is about, and nothing else set."""
    return Session(Globals(**flags), command="build")  # ty: ignore[invalid-argument-type]


def terminal(**state: bool) -> Terminal:
    """One terminal reading, with every question answered the way a test needs it."""
    base = {"is_terminal": True, "is_dumb": False, "no_color": False, "json": False, "events": False, "quiet": False}
    return Terminal(**{**base, **state})  # ty: ignore[invalid-argument-type]


def test_the_live_region_needs_a_terminal_with_one_stream_to_itself() -> None:
    assert terminal().live
    assert not terminal(is_terminal=False).live
    assert not terminal(is_dumb=True).live
    assert not terminal(no_color=True).live
    assert not terminal(json=True).live
    assert not terminal(events=True).live


def test_a_run_that_judged_nothing_exits_zero() -> None:
    made = session()
    assert made.exit_code(_status()) == 0


def test_a_certain_judgement_fails_a_run_under_the_default_threshold() -> None:
    made = session()
    assert made.exit_code(_status(finding())) == 1


def test_an_uncertain_judgement_fails_only_under_any() -> None:
    made = session()
    soft = _status(finding(Code.CUE_THIN_CHANGE))
    assert soft.findings[0].certainty is Certainty.UNCERTAIN
    assert made.exit_code(soft) == 0
    made.judging(fail_on=FailOn.ANY, allow=frozenset())
    assert made.exit_code(soft) == 1


def test_never_fails_on_nothing() -> None:
    made = session()
    made.judging(fail_on=FailOn.NEVER, allow=frozenset())
    assert made.exit_code(_status(finding())) == 0


def test_an_allowed_code_is_carried_past() -> None:
    made = session()
    made.judging(fail_on=FailOn.CERTAIN, allow=frozenset({Code.CUE_UNRESOLVED}))
    assert made.exit_code(_status(finding())) == 0


def test_a_refusal_takes_the_exit_code_its_own_code_carries() -> None:
    made = session()
    assert made.failed(InputError("decktalk.toml is not valid TOML.")) == ErrorCode.INPUT.exit_code
    assert made.failed(ApprovalRequired("no terminal approved it.")) == ErrorCode.APPROVAL.exit_code


def test_json_puts_one_object_on_stdout_and_nothing_else(capsys: pytest.CaptureFixture[str]) -> None:
    made = session(json_out=True)
    made.report(_status())
    written = capsys.readouterr()
    assert written.out.lstrip().startswith("{")
    assert written.out.rstrip().endswith("}")


def test_a_result_is_written_once_however_often_it_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    made = session(json_out=True)
    made.report(_status())
    made.report(_status())
    assert capsys.readouterr().out.count('"schema"') == 1


def test_a_flag_after_the_command_name_wins_over_the_one_before_it() -> None:
    merged = Globals(color=When.AUTO).merged({"json_out": True, "color": When.NEVER, "quiet": False})
    assert merged.json_out
    assert merged.color is When.NEVER
    assert not merged.quiet


def test_nothing_is_asked_without_a_terminal() -> None:
    made = session()
    made.terminal = terminal(is_terminal=False)
    assert not made.asks


def test_nothing_is_asked_under_no_input_on_a_terminal() -> None:
    made = session(no_input=True)
    made.terminal = terminal()
    assert not made.asks


def test_a_spend_with_no_terminal_refuses_and_names_both_flags() -> None:
    made = session()
    made.terminal = terminal(is_terminal=False)
    made.spending(no_voice=False, spend=False, max_cost=None)
    fake = Fake(check=_check())
    with pytest.raises(ApprovalRequired) as refused:
        made.voicing(fake)  # ty: ignore[invalid-argument-type]
    assert "no terminal is here to approve it" in str(refused.value)
    assert refused.value.hint is not None
    assert "--spend" in refused.value.hint
    assert "--no-voice" in refused.value.hint


def test_no_voice_never_asks_and_never_buys() -> None:
    made = session()
    made.spending(no_voice=True, spend=False, max_cost=None)
    assert made.voicing(Fake()) is Voicing.PLACEHOLDER  # ty: ignore[invalid-argument-type]


def test_spend_buys_without_asking() -> None:
    made = session()
    made.spending(no_voice=False, spend=True, max_cost=None)
    assert made.voicing(Fake()) is Voicing.PAID  # ty: ignore[invalid-argument-type]


def test_a_contract_document_is_written_with_no_envelope(capsys: pytest.CaptureFixture[str]) -> None:
    made = session()
    assert made.document({"title": "a document"}) == 0
    written = capsys.readouterr().out
    assert '"schema"' not in written
    assert '"title"' in written


def _status(*found: object) -> StatusResult:
    """A reading of a project, with whatever judgements a test wants hung on it."""
    return StatusResult(
        ok=not found,
        findings=tuple(found),  # ty: ignore[invalid-argument-type]
        run="r",
        name="demo",
        script="script.md",  # ty: ignore[invalid-argument-type]
        cues="cues.json",  # ty: ignore[invalid-argument-type]
        sections=(),
    )


def _check() -> CheckResult:
    """What `check` answers with when a session prices a run before refusing it."""
    return CheckResult(ok=True, run="r", judged=(), pages=False, frames=False, spend=spend())


def test_a_console_reads_the_terminal_rather_than_being_told_about_it() -> None:
    plain = Console(file=io.StringIO())
    assert not plain.is_terminal
