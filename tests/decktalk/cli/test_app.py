"""The one table: what a command's signature says, what the parser has, and what the help prints.

A dropped `Option` loses both the flag and its sentence with no error anywhere, so the sentences and
the metavars are asserted verbatim against the rendered help. The derivation of the shared flags is
asserted against the result each command declares, in both directions, so a command that starts
reporting judgements gains `--fail-on` by saying so on its result model and in no other place.
"""

from __future__ import annotations

import pytest
import typer

from decktalk.cli import catalog
from decktalk.cli.app import PROMPT_FLAGS, command
from decktalk.cli.options import Group
from decktalk.results import RESULTS, Result, WordsResult

from .conftest import commands

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
    "Buy what is missing without asking, or buy nothing and play a placeholder where a voiced take is missing.",
    "A free provider such as dtsp makes its takes either way once a voice is named.",
    "Unset, a terminal is asked and a run without one is refused.",
    "Start at this stage: narrate, cue, record, score, assemble or verify.",
    "Stop after this stage, inclusive.",
    "Run every stage but this one. Repeats.",
    "Only these sections: 3, 3,5 or 7-9. Repeats.",
    "error fails on an error, warning fails on any finding, never fails on none. Default",
    "Carry on past this finding code. Repeats.",
    "Build again from nothing, keeping every voiced take and every bought sound, and measure the film again.",
    "Override one setting here. Repeats. See config explain.",
    "Stay running, rebuild the changed section, never spend.",
    "-p, --json, --events, --color, --no-input, -v and -q work on every command.",
)
"""Every sentence `decktalk build --help` promises, which is the one help block the design wrote."""

BUILD_METAVARS = ("--max-cost N", "--from STAGE", "--to STAGE", "--skip STAGE", "--section N", "--set KEY=VALUE")
"""Every metavar `build` publishes, because a metavar is what an agent writes after the flag."""


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
    assert {str(row["group"]) for row in commands().values()} <= declared


@pytest.mark.parametrize("name", sorted(commands()))
def test_the_finding_flags_are_exactly_on_the_commands_that_judge(name: str) -> None:
    row = commands()[name]
    flags = {opt for param in row["params"] for opt in param["opts"]}
    model = _model(row)
    judges = model is not None and model.reports_findings
    assert ("--fail-on" in flags) is judges
    assert ("--allow" in flags) is judges


@pytest.mark.parametrize("name", sorted(commands()))
def test_the_spend_flags_are_exactly_on_the_commands_that_buy(name: str) -> None:
    row = commands()[name]
    flags = {opt for param in row["params"] for opt in param["opts"]}
    model = _model(row)
    spends = model is not None and model.spends
    assert ("--spend" in flags) is spends
    assert ("--no-spend" in flags) is spends
    assert ("--max-cost" in flags) is spends


def _model(row: dict[str, object]) -> type[Result] | None:
    """The result model one command answers with, read back off the registry by its name."""
    return RESULTS.get(str(row["result"])) if row["result"] else None


@pytest.mark.parametrize("name", sorted(commands()))
def test_every_command_help_names_every_field_its_result_carries_and_its_docs(run, name: str) -> None:
    said = flat(run(*name.split(), "--help").out)
    model = _model(commands()[name])
    for field, info in model.model_fields.items() if model else ():
        published = info.alias or field
        assert field in Result.model_fields or published in said, f"{name} --help leaves out {published}"
    assert f"#decktalk-{name.replace(' ', '-')}" in said


RATIONALE = {
    "narrate": "It names the transformation",
    "cue": "so the stage is called what everything around it is called",
    "score": "so that the unpaid draft loop stops at a recording",
    "assemble": "the editing room's word for joining shots into a cut",
    "verify": "the product's whole claim written as a measurement",
    "clip": "so the command that makes one is called what the file is called",
    "storyboard": "One panel of a storyboard is still a storyboard",
    "config list": "An agent cannot change a setting it cannot enumerate",
    "config unset": "editing a validated file is library work",
    "config explain": "the whole instruction set rests on",
}
"""One sentence of each command's design note, which its docstring keeps after the form feed Click cuts at."""


@pytest.mark.parametrize(("name", "note"), sorted(RATIONALE.items()))
def test_a_command_s_help_leaves_out_why_it_was_designed(run, name: str, note: str) -> None:
    """A reader of `--help` needs what the command does, and the reason behind its name is for its maintainer."""
    assert flat(note) not in flat(run(*name.split(), "--help").out)


def test_the_serve_help_says_its_output_stays_open_while_it_serves(run) -> None:
    """The command flushes its one object and then serves, so a caller reading to the end of the stream waits."""
    said = flat(run("serve", "--help").out)
    assert "closes stdout" not in said
    assert "standard output stays open while it serves" in said


@pytest.mark.parametrize("name", sorted(commands()))
def test_every_command_carries_the_globals_after_its_own_name(name: str) -> None:
    flags = {opt for param in commands()[name]["params"] for opt in param["opts"]}
    assert {"--json", "--events", "--color", "--no-input", "-v", "-q", "-p"} <= flags


def test_a_refused_command_line_is_one_usage_error_with_no_usage_block(run) -> None:
    ran = run("build", "--nope")
    assert ran.exit_code == 2
    assert "error[USAGE]: decktalk build:" in ran.err
    assert "Usage:" not in ran.err


def test_an_unknown_flag_names_the_flag_it_most_likely_meant(run) -> None:
    said = " ".join(run("build", "--secton", "3").err.split())
    assert "--secton is not a flag of this command. Did you mean --section?" in said


def test_an_unknown_flag_like_no_real_one_is_refused_without_a_guess(run) -> None:
    """Click offered --verbose for --bogus, which is a guess a reader would follow and regret."""
    said = run("build", "--bogus").err
    assert "--bogus is not a flag of this command." in said
    assert "--verbose" not in said


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
    flags = {opt for row in commands().values() for param in row["params"] for opt in param["opts"]}
    assert PROMPT_FLAGS <= flags


def test_a_sentence_for_a_flag_the_command_does_not_take_is_refused() -> None:
    def words(ctx: object) -> WordsResult:  # pragma: no cover  (refused before it is registered)
        raise AssertionError(ctx)

    with pytest.raises(TypeError, match="spend"):
        command(group=Group.PROJECT, to=typer.Typer(), helps={"spend": "Buy it."})(words)


def test_the_root_shows_the_globals_every_command_hides() -> None:
    shown = {opt for param in catalog.globals_() if not param["hidden"] for opt in param["opts"]}
    assert {"--project", "--json", "--events", "--color", "--no-input", "--verbose", "--quiet", "--version"} <= shown
    assert "--yes" not in shown
