"""Nine codes, seven classes, and a mapping from a refusal to an exit code that cannot miss a row."""

from __future__ import annotations

import pytest

from decktalk.errors import (
    BROKEN,
    INTERRUPTED,
    REFUSED,
    ApprovalRequired,
    Cancel,
    Cancelled,
    DeckTalkError,
    ErrorCode,
    ErrorInfo,
    InputError,
    NotBuiltError,
    ProjectLocked,
    ProviderError,
    ToolError,
)
from decktalk.findings import DOCS, Location

CLASSES = (InputError, NotBuiltError, ProviderError, ToolError, ProjectLocked, ApprovalRequired, Cancelled)
CODELESS = (ErrorCode.USAGE, ErrorCode.INTERNAL)


def test_there_are_nine_codes_and_they_are_the_ones_the_design_named() -> None:
    assert [code.value for code in ErrorCode] == [
        "INPUT",
        "NOT_BUILT",
        "PROVIDER",
        "TOOL",
        "LOCKED",
        "APPROVAL",
        "CANCELLED",
        "USAGE",
        "INTERNAL",
    ]


def test_there_are_seven_classes_and_each_is_a_kind_of_the_base() -> None:
    assert len(CLASSES) == 7
    for kind in CLASSES:
        assert issubclass(kind, DeckTalkError)


def test_every_class_carries_a_different_code() -> None:
    carried = [kind.code for kind in CLASSES]
    assert len(set(carried)) == len(carried)


def test_exactly_usage_and_internal_have_no_class_that_raises_them() -> None:
    raised = {kind.code for kind in CLASSES}
    assert set(ErrorCode) - raised == set(CODELESS)


def test_the_base_is_never_raised_bare_so_it_carries_no_code() -> None:
    assert "code" not in vars(DeckTalkError)


def test_the_exit_mapping_is_total_and_is_the_one_the_design_named() -> None:
    exits = {code: code.exit_code for code in ErrorCode}
    assert set(exits) == set(ErrorCode)
    assert {code for code, value in exits.items() if value == REFUSED} == {ErrorCode.USAGE, ErrorCode.APPROVAL}
    assert {code for code, value in exits.items() if value == INTERRUPTED} == {ErrorCode.CANCELLED}
    assert {code for code, value in exits.items() if value == BROKEN} == set(ErrorCode) - {
        ErrorCode.USAGE,
        ErrorCode.APPROVAL,
        ErrorCode.CANCELLED,
    }


def test_every_code_publishes_one_sentence_and_one_page() -> None:
    for code in ErrorCode:
        assert code.sentence.endswith("."), code.value
        assert ";" not in code.sentence, code.value
        assert code.url == f"{DOCS}/errors/{code.value}"


def test_a_raiser_carries_the_next_command_and_the_place_to_open() -> None:
    error = InputError(
        "decktalk.toml is not valid TOML.",
        hint="Fix line 59 of decktalk.toml, then run decktalk status.",
        location=Location(where="decktalk.toml", file="decktalk.toml", line=59),
    )
    assert error.code is ErrorCode.INPUT
    assert error.location is not None
    assert error.location.line == 59


def test_a_provider_refusal_says_whether_waiting_would_help() -> None:
    assert ProviderError("refused").retryable is False
    assert ProviderError("refused", retryable=True).retryable is True


def test_an_exception_becomes_data_in_one_place() -> None:
    error = NotBuiltError("build/narrate/takes.json is not there.", hint="Run decktalk build --to assemble.")
    info = ErrorInfo.of(error)
    assert info.code is ErrorCode.NOT_BUILT
    assert info.message == "build/narrate/takes.json is not there."
    assert info.docs == ErrorCode.NOT_BUILT.url
    assert ErrorInfo.model_validate_json(info.model_dump_json()) == info


def test_a_cancel_token_is_unset_until_it_is_set() -> None:
    token = Cancel()
    assert token.is_set() is False
    token.check()
    token.cancel()
    assert token.is_set() is True


def test_a_cancelled_run_raises_the_class_that_carries_the_interrupt_code() -> None:
    token = Cancel()
    token.cancel()
    with pytest.raises(Cancelled) as raised:
        token.check()
    assert raised.value.code.exit_code == INTERRUPTED


def test_every_error_field_publishes_one_sentence() -> None:
    for name, field in ErrorInfo.model_fields.items():
        assert field.description, name
