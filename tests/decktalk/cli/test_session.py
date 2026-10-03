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
from decktalk.findings import ERRORS_FAIL, Code, Severity, Threshold
from decktalk.pipeline import Stage
from decktalk.results import BillingBasis, Layer, StatusResult
from support.costs import a_cost

from .conftest import ANSWERS, Fake, finding


def session(flags: Globals | None = None, *, spend: bool | None = None, threshold: Threshold = ERRORS_FAIL) -> Session:
    """One session with the flags, the spend answer and the threshold a test is about, and nothing else set."""
    return Session(flags or Globals(), command="build", spend=spend, threshold=threshold)


def terminal(**state: bool) -> Terminal:
    """One terminal reading, with every question answered the way a test needs it."""
    base = {"is_terminal": True, "is_dumb": False, "no_color": False}
    return Terminal(**{**base, **state})


@pytest.mark.parametrize(
    ("flags", "state", "live"),
    [
        (Globals(), {}, True),
        (Globals(), {"is_terminal": False}, False),
        (Globals(), {"is_dumb": True}, False),
        (Globals(), {"no_color": True}, False),
        (Globals(json_out=True), {}, False),
        (Globals(events=True), {}, False),
    ],
)
def test_the_live_region_needs_a_terminal_with_one_stream_to_itself(
    flags: Globals, state: dict[str, bool], live: bool
) -> None:
    made = session(flags)
    made.terminal = terminal(**state)
    assert made.live is live


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
        (None, frozenset(), (Code.CUE_UNRESOLVED,), 1),  # an error under the default threshold
        (None, frozenset(), (Code.CUE_THIN_CHANGE,), 0),  # a warning fails only under --fail-on warning
        (FailOn.WARNING, frozenset(), (Code.CUE_THIN_CHANGE,), 1),
        (FailOn.NEVER, frozenset(), (Code.CUE_UNRESOLVED,), 0),
        (FailOn.ERROR, frozenset({Code.CUE_UNRESOLVED}), (Code.CUE_UNRESOLVED,), 0),  # an allowed code
    ],
    ids=["nothing", "sure", "unsure", "unsure-failing", "off", "allowed"],
)
def test_the_exit_code_fails_on_what_the_threshold_names_and_nothing_it_allows(
    fail_on: FailOn | None, allow: frozenset[Code], found: tuple[Code, ...], code: int
) -> None:
    made = session() if fail_on is None else session(threshold=Threshold(stop_on=fail_on.stops_on, allow=allow))
    assert made.exit_code(_status(*(finding(one) for one in found))) == code


@pytest.mark.parametrize(
    ("argv", "found"),
    [
        (("check",), ()),
        (("check",), (Code.CUE_THIN_CHANGE,)),
        (("check", "--fail-on", "warning"), (Code.CUE_THIN_CHANGE,)),
        (("check",), (Code.CUE_UNRESOLVED,)),
        (("check", "--fail-on", "never"), (Code.CUE_UNRESOLVED,)),
        (("check", "--allow", Code.CUE_UNRESOLVED.value), (Code.CUE_UNRESOLVED,)),
        (("check", "--allow", Code.CUE_UNRESOLVED.value, "--fail-on", "warning"), (Code.CUE_UNRESOLVED,)),
        (("build", "--no-spend", "--allow", Code.CUE_UNKNOWN.value), (Code.CUE_UNKNOWN,)),
        (("build", "--no-spend", "--fail-on", "warning"), (Code.CUE_THIN_CHANGE,)),
    ],
    ids=["nothing", "unsure", "any-unsure", "sure", "never", "allowed", "allowed-any", "build-allowed", "build-any"],
)
def test_ok_under_json_is_true_exactly_when_the_exit_code_is_0(run, project, argv: tuple[str, ...], found) -> None:
    """A workflow that reads `.ok` and one that reads the exit code agree about the same run.

    The fake judges `ok` by the threshold the project was opened with, as the library does, and the
    command line writes it unchanged, so the agreement is the one threshold both are read from.
    """
    judged = tuple(finding(code) for code in found)
    answer = ANSWERS[argv[0]].model_copy(update={"findings": judged})
    project(**{argv[0]: answer})
    ran = run(*argv, "--json")
    assert json.loads(ran.out)["ok"] is (ran.exit_code == 0), (ran.exit_code, ran.out)


def test_ok_under_json_is_false_whenever_the_command_could_not_run(run, project) -> None:
    project(check=InputError("decktalk.toml is not valid TOML."))
    ran = run("check", "--json", "--fail-on", "never")
    assert ran.exit_code == ErrorCode.INPUT.exit_code
    assert json.loads(ran.out)["ok"] is False


def test_a_thin_change_is_the_warning_the_table_uses() -> None:
    assert finding(Code.CUE_THIN_CHANGE).severity is Severity.WARNING


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
    fake = Fake(price=a_cost())
    with pytest.raises(ApprovalRequired) as refused:
        made.spends(fake.project(), (Stage.NARRATE,))
    assert "No terminal is here to approve it." in str(refused.value)
    assert refused.value.hint is not None
    assert "--spend" in refused.value.hint
    assert "--no-spend" in refused.value.hint


def test_a_spend_that_could_not_be_priced_says_why_in_its_refusal() -> None:
    """A refusal with no price and no cause leaves a caller nothing to mend, so the reason travels with it."""
    made = session()
    made.terminal = terminal(is_terminal=False)
    unpriced = Fake(price=InputError("cues.json is not valid JSON."))
    with pytest.raises(ApprovalRequired) as refused:
        made.spends(unpriced.project(), (Stage.NARRATE,))
    assert "could not be priced" in str(refused.value)
    assert "cues.json is not valid JSON." in str(refused.value)


@pytest.mark.parametrize("is_terminal", [True, False])
def test_no_spend_never_asks_and_never_buys(monkeypatch: pytest.MonkeyPatch, is_terminal: bool) -> None:
    made = session(spend=False)
    made.terminal = terminal(is_terminal=is_terminal)
    monkeypatch.setattr(made, "confirm", _never_asked)
    fake = Fake(price=a_cost())
    assert made.spends(fake.project(), (Stage.NARRATE,)) is False
    assert fake.calls == [], "a run told not to spend was priced as if it might"


def test_spend_buys_without_asking(monkeypatch: pytest.MonkeyPatch) -> None:
    made = session(spend=True)
    monkeypatch.setattr(made, "confirm", _never_asked)
    assert made.spends(Fake().project(), (Stage.NARRATE,)) is True


@pytest.mark.parametrize("is_terminal", [True, False])
def test_a_run_with_nothing_to_buy_is_never_asked_and_buys_nothing(
    monkeypatch: pytest.MonkeyPatch, is_terminal: bool
) -> None:
    made = session()
    made.terminal = terminal(is_terminal=is_terminal)
    monkeypatch.setattr(made, "confirm", _never_asked)
    assert made.spends(Fake(price=a_cost(0.0, 0.0, sections=())).project(), (Stage.NARRATE,)) is False


@pytest.mark.parametrize("is_terminal", [True, False])
def test_a_voice_that_bills_nothing_is_never_asked_and_is_bought_from(
    monkeypatch: pytest.MonkeyPatch, is_terminal: bool
) -> None:
    made = session()
    made.terminal = terminal(is_terminal=is_terminal)
    monkeypatch.setattr(made, "confirm", _never_asked)
    free = a_cost(0.0, 0.0, sections=(1, 2), billing=BillingBasis.FREE)
    assert made.spends(Fake(price=free).project(), (Stage.NARRATE,)) is True


def test_a_run_told_to_replace_its_paid_takes_is_asked_even_with_nothing_missing() -> None:
    made = session()
    made.terminal = terminal(is_terminal=False)
    with pytest.raises(ApprovalRequired):
        made.spends(Fake(price=a_cost(0.0, 0.0, sections=())).project(), (Stage.NARRATE,), replace_voiced=True)


def test_no_spend_never_buys_from_a_voice_that_bills_nothing() -> None:
    made = session(spend=False)
    free = a_cost(0.0, 0.0, sections=(1, 2), billing=BillingBasis.FREE)
    assert made.spends(Fake(price=free).project(), (Stage.NARRATE,)) is False


@pytest.mark.parametrize("layer", [Layer.DEFAULT, Layer.PROJECT])
def test_a_price_of_zero_on_a_voice_that_bills_is_still_asked_about(layer: Layer) -> None:
    """Free is what the voice declares, so a zero rate, stated or the default, never skips the question."""
    made = session()
    made.terminal = terminal(is_terminal=False)
    zero = a_cost(0.0, 0.0, layer=layer).model_copy(update={"dollars_per_1000_characters": 0.0})
    with pytest.raises(ApprovalRequired):
        made.spends(Fake(price=zero).project(), (Stage.NARRATE,))


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
        cues_file="cues.json",
        sections=(),
    )


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


def test_the_storyboard_line_counts_one_panel_in_the_singular(capsys: pytest.CaptureFixture[str]) -> None:
    drawn = SimpleNamespace(storyboard=Path("build/storyboard.html"), panels=("one",))
    assert session(spend=True).spends(Fake(storyboard=drawn).project(), (Stage.NARRATE,), storyboard=True)
    assert "Storyboard build/storyboard.html, 1 panel." in capsys.readouterr().err


@pytest.mark.parametrize(
    ("spend", "storyboard", "drawn"),
    [(True, True, 1), (False, True, 0), (True, False, 0)],
    ids=["spend-checkpoint", "no-spend", "spend-without-checkpoint"],
)
def test_a_spend_flag_draws_the_storyboard_once_and_only_when_the_run_will_spend(
    spend: bool, storyboard: bool, drawn: int
) -> None:
    fake = Fake(storyboard=SimpleNamespace(storyboard=None, panels=()))
    assert session(spend=spend).spends(fake.project(), (Stage.NARRATE,), storyboard=storyboard) is spend
    assert [name for name, _, _ in fake.calls].count("storyboard") == drawn


def test_a_terminal_names_the_storyboard_before_the_price_and_the_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The person asked looks at the sheet before answering, so the line comes first."""
    asked: list[str] = []
    monkeypatch.setattr(Session, "asks", property(lambda _: True))
    made = session()
    monkeypatch.setattr(made, "confirm", lambda question, **_: asked.append(capsys.readouterr().err) or True)
    drawn = SimpleNamespace(storyboard=Path("build/storyboard.html"), panels=("one",))
    fake = Fake(storyboard=drawn, price=a_cost(2.14, 2.14, sections=(1,)))
    assert made.spends(fake.project(), (Stage.NARRATE,), storyboard=True)
    said = " ".join(asked[0].split())
    assert said.index("Storyboard build/storyboard.html") < said.index("$2.14")
    assert [name for name, _, _ in fake.calls].count("storyboard") == 1
