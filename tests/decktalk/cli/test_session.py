"""How a run ends: which rendering the terminal chose, what the exit code is, and what a prompt does."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from decktalk.cli.options import FailOn, When
from decktalk.cli.session import Globals, Session, Terminal
from decktalk.errors import ApprovalRequired, ErrorCode, InputError
from decktalk.findings import Certainty, Code
from decktalk.results import CheckResult, Layer, StatusResult
from support.spends import a_spend

from .conftest import ANSWERS, Fake, finding


def session(flags: Globals | None = None) -> Session:
    """One session with the flags a test is about, and nothing else set."""
    return Session(flags or Globals(), command="build")


def terminal(**state: bool) -> Terminal:
    """One terminal reading, with every question answered the way a test needs it."""
    base = {"is_terminal": True, "is_dumb": False, "no_color": False, "json": False, "events": False, "quiet": False}
    return Terminal(**{**base, **state})


def test_the_live_region_needs_a_terminal_with_one_stream_to_itself() -> None:
    assert terminal().live
    assert not terminal(is_terminal=False).live
    assert not terminal(is_dumb=True).live
    assert not terminal(no_color=True).live
    assert not terminal(json=True).live
    assert not terminal(events=True).live


def test_no_color_in_the_environment_outranks_every_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    for color in When:
        made = session(Globals(color=color))
        assert made.out.no_color and made.err.no_color and made.terminal.no_color, color


def test_color_never_turns_colour_off_without_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert session(Globals(color=When.NEVER)).terminal.no_color
    assert not session(Globals(color=When.ALWAYS)).terminal.no_color


@pytest.mark.parametrize(
    ("fail_on", "allow", "found", "code"),
    [
        (None, frozenset(), (), 0),  # a run that judged nothing
        (None, frozenset(), (Code.CUE_UNRESOLVED,), 1),  # a certain judgement under the default threshold
        (None, frozenset(), (Code.CUE_THIN_CHANGE,), 0),  # an uncertain one fails only under any
        (FailOn.ANY, frozenset(), (Code.CUE_THIN_CHANGE,), 1),
        (FailOn.NEVER, frozenset(), (Code.CUE_UNRESOLVED,), 0),
        (FailOn.CERTAIN, frozenset({Code.CUE_UNRESOLVED}), (Code.CUE_UNRESOLVED,), 0),  # an allowed code
    ],
    ids=["nothing", "sure", "unsure", "unsure-failing", "off", "allowed"],
)
def test_the_exit_code_fails_on_what_the_threshold_names_and_nothing_it_allows(
    fail_on: FailOn | None, allow: frozenset[Code], found: tuple[Code, ...], code: int
) -> None:
    made = session()
    if fail_on is not None:
        made.judging(fail_on=fail_on, allow=allow)
    assert made.exit_code(_status(*(finding(one) for one in found))) == code


@pytest.mark.parametrize(
    ("argv", "found"),
    [
        (("check",), ()),
        (("check",), (Code.CUE_THIN_CHANGE,)),
        (("check", "--fail-on", "any"), (Code.CUE_THIN_CHANGE,)),
        (("check",), (Code.CUE_UNRESOLVED,)),
        (("check", "--fail-on", "never"), (Code.CUE_UNRESOLVED,)),
        (("check", "--allow", Code.CUE_UNRESOLVED.value), (Code.CUE_UNRESOLVED,)),
        (("check", "--allow", Code.CUE_UNRESOLVED.value, "--fail-on", "any"), (Code.CUE_UNRESOLVED,)),
        (("build", "--no-spend", "--allow", Code.CUE_UNKNOWN.value), (Code.CUE_UNKNOWN,)),
        (("build", "--no-spend", "--fail-on", "any"), (Code.CUE_THIN_CHANGE,)),
    ],
    ids=["nothing", "unsure", "any-unsure", "sure", "never", "allowed", "allowed-any", "build-allowed", "build-any"],
)
def test_ok_under_json_is_true_exactly_when_the_exit_code_is_0(run, project, argv: tuple[str, ...], found) -> None:
    """A workflow that reads `.ok` and one that reads the exit code agree about the same run.

    The fake answers the way the library does for a caller that named no threshold, so `ok` on stdout
    is the command line's own reading of `--fail-on` and `--allow`.
    """
    judged = tuple(finding(code) for code in found)
    answer = ANSWERS[argv[0]].model_copy(
        update={"findings": judged, "ok": not any(one.certainty is Certainty.CERTAIN for one in judged)}
    )
    project(**{argv[0]: answer})
    ran = run(*argv, "--json")
    assert json.loads(ran.out)["ok"] is (ran.exit_code == 0), (ran.exit_code, ran.out)


def test_ok_under_json_is_false_whenever_the_command_could_not_run(run, project) -> None:
    project(check=InputError("decktalk.toml is not valid TOML."))
    ran = run("check", "--json", "--fail-on", "never")
    assert ran.exit_code == ErrorCode.INPUT.exit_code
    assert json.loads(ran.out)["ok"] is False


def test_a_thin_change_is_the_uncertain_judgement_the_table_uses() -> None:
    assert finding(Code.CUE_THIN_CHANGE).certainty is Certainty.UNCERTAIN


def test_a_refusal_takes_the_exit_code_its_own_code_carries() -> None:
    made = session()
    assert made.failed(InputError("decktalk.toml is not valid TOML.")) == ErrorCode.INPUT.exit_code
    assert made.failed(ApprovalRequired("no terminal approved it.")) == ErrorCode.APPROVAL.exit_code


def test_json_puts_one_object_on_stdout_and_nothing_else(capsys: pytest.CaptureFixture[str]) -> None:
    made = session(Globals(json_out=True))
    made.report(_status())
    written = capsys.readouterr()
    assert written.out.lstrip().startswith("{")
    assert written.out.rstrip().endswith("}")


def test_a_result_is_written_once_however_often_it_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    made = session(Globals(json_out=True))
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
    made = session(Globals(no_input=True))
    made.terminal = terminal()
    assert not made.asks


def test_a_flag_answers_before_a_terminal_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    made = session()
    made.terminal = terminal()
    monkeypatch.setattr(made, "confirm", lambda question, *, default=False: question == "Carry on?" or default)
    assert made.approve(False, "Carry on?") is False
    assert made.approve(True, "Stop?") is True
    assert made.approve(None, "Carry on?") is True
    made.terminal = terminal(is_terminal=False)
    assert made.approve(None, "Carry on?", default=True) is False


def test_a_spend_with_no_terminal_refuses_and_names_both_flags() -> None:
    made = session()
    made.terminal = terminal(is_terminal=False)
    made.spending(spend=None, max_cost=None)
    fake = Fake(check=_check())
    with pytest.raises(ApprovalRequired) as refused:
        made.spends(fake.project())
    assert "No terminal is here to approve it." in str(refused.value)
    assert refused.value.hint is not None
    assert "--spend" in refused.value.hint
    assert "--no-spend" in refused.value.hint


@pytest.mark.parametrize("is_terminal", [True, False])
def test_no_spend_never_asks_and_never_buys(monkeypatch: pytest.MonkeyPatch, is_terminal: bool) -> None:
    made = session()
    made.terminal = terminal(is_terminal=is_terminal)
    monkeypatch.setattr(made, "confirm", _never_asked)
    made.spending(spend=False, max_cost=None)
    fake = Fake(check=_check())
    assert made.spends(fake.project()) is False
    assert fake.calls == [], "a run told not to spend was priced as if it might"


def test_spend_buys_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    made = session()
    monkeypatch.setattr(made, "confirm", _never_asked)
    made.spending(spend=True, max_cost=None)
    assert made.spends(Fake().project()) is True


@pytest.mark.parametrize("is_terminal", [True, False])
def test_a_run_with_nothing_to_buy_is_never_asked_and_buys_nothing(
    monkeypatch: pytest.MonkeyPatch, is_terminal: bool
) -> None:
    made = session()
    made.terminal = terminal(is_terminal=is_terminal)
    monkeypatch.setattr(made, "confirm", _never_asked)
    made.spending(spend=None, max_cost=None)
    assert made.spends(Fake().project(), price=lambda: a_spend(0.0, 0.0, sections=())) is False


@pytest.mark.parametrize("is_terminal", [True, False])
def test_a_voice_that_bills_nothing_is_never_asked_and_is_bought_from(
    monkeypatch: pytest.MonkeyPatch, is_terminal: bool
) -> None:
    made = session()
    made.terminal = terminal(is_terminal=is_terminal)
    monkeypatch.setattr(made, "confirm", _never_asked)
    made.spending(spend=None, max_cost=None)
    free = a_spend(0.0, 0.0, sections=(1, 2)).model_copy(update={"price_per_1000_characters": 0.0})
    assert made.spends(Fake().project(), price=lambda: free) is True


def test_a_run_told_to_make_its_takes_again_is_asked_even_with_nothing_missing() -> None:
    made = session()
    made.terminal = terminal(is_terminal=False)
    made.spending(spend=None, max_cost=None)
    with pytest.raises(ApprovalRequired):
        made.spends(Fake().project(), price=lambda: a_spend(0.0, 0.0, sections=()), forced=True)


def test_no_spend_never_buys_from_a_voice_that_bills_nothing() -> None:
    made = session()
    made.spending(spend=False, max_cost=None)
    free = a_spend(0.0, 0.0, sections=(1, 2)).model_copy(update={"price_per_1000_characters": 0.0})
    assert made.spends(Fake().project(), price=lambda: free) is False


def test_a_price_of_zero_nobody_stated_is_still_asked_about() -> None:
    """The default rate is zero, which says nobody has priced speech, never that it is free."""
    made = session()
    made.terminal = terminal(is_terminal=False)
    made.spending(spend=None, max_cost=None)
    unstated = a_spend(0.0, 0.0, layer=Layer.DEFAULT).model_copy(update={"price_per_1000_characters": 0.0})
    with pytest.raises(ApprovalRequired):
        made.spends(Fake().project(), price=lambda: unstated)


def _never_asked(question: str, **_: object) -> bool:
    raise AssertionError(f"the run asked {question!r} and was meant to ask nothing")


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
        findings=tuple(found),
        run="r",
        name="demo",
        script="script.md",
        cues="cues.json",
        sections=(),
    )


def _check() -> CheckResult:
    """What `check` answers with when a session prices a run before refusing it."""
    return CheckResult(ok=True, run="r", judged=(), pages=False, frames=False, spend=a_spend())


def test_a_console_reads_the_terminal_rather_than_being_told_about_it() -> None:
    plain = Console(file=io.StringIO())
    assert not plain.is_terminal


def test_the_fix_prompt_counts_one_fix_in_the_singular(monkeypatch: pytest.MonkeyPatch) -> None:
    made = session()
    asked: list[str] = []
    monkeypatch.setattr(Session, "asks", property(lambda _: True))
    monkeypatch.setattr(made, "confirm", lambda question, **_: asked.append(question) or True)
    made.fixes_wanted((finding(fix=True),), None)
    assert asked == ["Apply 1 fix?"]


def test_the_storyboard_line_counts_one_panel_in_the_singular() -> None:
    drawn = SimpleNamespace(storyboard=Path("build/storyboard.html"), panels=("one",))
    line = session().storyboard_line(Fake(storyboard=drawn).project())
    assert line == "Storyboard build/storyboard.html, 1 panel."
