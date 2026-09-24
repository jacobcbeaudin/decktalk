"""The one table: what a command's signature says, what the parser has, and what the help prints.

A dropped `Option` loses both the flag and its sentence with no error anywhere, so the sentences and
the metavars are asserted verbatim against the rendered help. The derivation of the shared flags is
asserted against the result each command declares, in both directions, so a command that starts
reporting judgements gains `--fail-on` by saying so in its return annotation and in no other place.
"""

from __future__ import annotations

import pytest

from decktalk.cli import catalog
from decktalk.cli.app import PROMPT_FLAGS
from decktalk.cli.options import JUDGES, SPENDS, Group
from decktalk.results import RESULTS, Result

TOP_LINES = (
    "Every picture lands on its word. DeckTalk turns a markdown script, HTML",
    "slides and your voice into one narrated mp4.",
    "  init        Create a project with a deck that already builds.",
    "  storyboard  Freeze every slide at every cue onto one page.",
    "  verify      Measure the finished mp4: every start, cut, seam and landing.",
    "  build       Run every stage in order, or a span of them.",
    "2 refused the command line, 3 could not run, 130 interrupted.",
    "Docs: https://docs.decktalk.ai/reference/cli",
)
"""Lines of `decktalk --help` a reader is promised, which no reformatting may quietly change."""

BUILD_SENTENCES = (
    "Run every stage in order, or a span of them with --from and --to.",
    "Placeholder narration: no API key and no spend.",
    "Voice what needs it without asking first.",
    "Start at this stage: narrate, cue, record, soundscape, assemble or verify.",
    "Stop after this stage, inclusive.",
    "Run every stage but this one. Repeats.",
    "Only these sections: 3, 3,5 or 7-9. Repeats.",
    "certain fails on a certain finding, any fails on any finding, never fails on none. Default",
    "Carry on past this finding code. Repeats.",
    "Build again from nothing, keeping every voiced take.",
    "Override one setting here. Repeats. See config explain.",
    "Stay running, rebuild the changed section, never spend.",
    "-p, --json, --events, --color, --no-input, -v and -q work on every command.",
)
"""Every sentence `decktalk build --help` promises, which is the one help block the design wrote."""

BUILD_METAVARS = ("--max-cost N", "--from STAGE", "--to STAGE", "--skip STAGE", "--section N", "--set KEY=VALUE")
"""Every metavar `build` publishes, because a metavar is what an agent writes after the flag."""


def rows() -> dict[str, dict[str, object]]:
    """Every command of the tree, by the words a caller types to reach it."""
    return {str(row["command"]): row for row in catalog.walk()}


def flat(text: str) -> str:
    """One help page as one line, because a sentence is promised and its wrapping is not."""
    return " ".join(text.split())


@pytest.mark.parametrize("line", TOP_LINES)
def test_the_top_help_says_what_it_promised(run, line: str) -> None:
    assert flat(line) in flat(run("--help").out)


@pytest.mark.parametrize("sentence", (*BUILD_SENTENCES, *BUILD_METAVARS))
def test_the_build_help_says_what_it_promised(run, sentence: str) -> None:
    assert flat(sentence) in flat(run("build", "--help").out)


def test_every_command_sits_in_a_declared_group() -> None:
    declared = {group.value for group in Group}
    assert {str(row["group"]) for row in rows().values()} <= declared


def test_every_command_answers_with_a_result_the_registry_knows() -> None:
    answered = {row["result"] for row in rows().values() if row["result"]}
    assert answered <= set(RESULTS)


@pytest.mark.parametrize("name", sorted(rows()))
def test_the_finding_flags_are_exactly_on_the_commands_that_judge(name: str) -> None:
    row = rows()[name]
    flags = {opt for param in row["params"] for opt in param["opts"]}  # ty: ignore[not-iterable]
    judges = _model(row) in JUDGES
    assert ("--fail-on" in flags) is judges
    assert ("--allow" in flags) is judges


@pytest.mark.parametrize("name", sorted(rows()))
def test_the_spending_flags_are_exactly_on_the_commands_that_buy(name: str) -> None:
    row = rows()[name]
    flags = {opt for param in row["params"] for opt in param["opts"]}  # ty: ignore[not-iterable]
    spends = _model(row) in SPENDS
    assert ("--spend" in flags) is spends
    assert ("--no-voice" in flags) is spends
    assert ("--max-cost" in flags) is spends


def _model(row: dict[str, object]) -> type[Result] | None:
    """The result model one command answers with, read back off the registry by its name."""
    return RESULTS.get(str(row["result"])) if row["result"] else None


@pytest.mark.parametrize("name", sorted(rows()))
def test_every_command_carries_the_globals_after_its_own_name(name: str) -> None:
    flags = {opt for param in rows()[name]["params"] for opt in param["opts"]}  # ty: ignore[not-iterable]
    assert {"--json", "--events", "--color", "--no-input", "-v", "-q", "-p"} <= flags


def test_a_refused_command_line_is_one_usage_error_with_no_usage_block(run) -> None:
    ran = run("build", "--nope")
    assert ran.exit_code == 2
    assert "error[USAGE]: decktalk build:" in ran.err
    assert "Usage:" not in ran.err


def test_yes_is_recognised_and_refused_naming_this_command_s_own_flags(run) -> None:
    ran = run("build", "--yes")
    assert ran.exit_code == 2
    assert "--yes answers nothing" in ran.err
    assert "--spend" in ran.err


def test_yes_on_a_command_with_no_prompt_says_so(run) -> None:
    ran = run("schema", "--yes")
    assert ran.exit_code == 2
    assert "this command asks nothing" in ran.err


def test_a_global_works_before_and_after_the_command_name(run, project, answers) -> None:
    project(status=answers["status"])
    before = run("--json", "status")
    after = run("status", "--json")
    assert before.out == after.out
    assert before.out.lstrip().startswith("{")


def test_every_flag_that_answers_a_prompt_is_one_the_tree_really_has() -> None:
    flags = {opt for row in rows().values() for param in row["params"] for opt in param["opts"]}
    assert PROMPT_FLAGS <= flags
