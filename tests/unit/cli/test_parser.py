"""The command table: one parser, one help screen, and the flags every command shares.

Nothing here runs a stage. These tests read the parser, which is where every argument rule lives, so
every rule is visible to `--help` and to the skills sync test.
"""

from __future__ import annotations

import pytest

from decktalk.cli.options import Options
from decktalk.cli.parser import BY_NAME, COMMANDS, UsageError, build_parser, command_parsers, epilog, sections
from decktalk.pipeline import Stage

# What each command needs on the line before its flags, so one loop can parse every command.
REQUIRED: dict[str, list[str]] = {
    "init": ["somewhere"],
    "clip": ["1", "--start", "0", "--end", "1", "--out", "media/a.mp4"],
}


def argv(name: str, *flags: str) -> list[str]:
    return [name, *REQUIRED.get(name, []), *flags]


def test_every_command_accepts_json_and_carries_it_into_its_options():
    """`--json` is global: no command is left with no machine output, and none names it twice."""
    parser = build_parser()
    for command in COMMANDS:
        args = parser.parse_args(argv(command.name, "--json"))
        assert args.json is True, command.name
        assert command.options.of(args).json is True, command.name


def test_json_is_taken_before_the_command_name_too():
    """An agent writes the flag where it thinks of it, and both spellings reach the same field."""
    for line in (["--json", "status"], ["status", "--json"]):
        assert build_parser().parse_args(line).json is True


def test_every_command_has_a_row_a_handler_and_one_purpose():
    """The table is the one list, so the parser, the epilog and the dispatch cannot disagree."""
    from decktalk.cli.dispatch import HANDLERS

    assert sorted(BY_NAME) == sorted(command_parsers()) == sorted(HANDLERS)
    for command in COMMANDS:
        assert command.purpose and command.purpose[0].islower(), command.name
        assert issubclass(command.options, Options), command.name


def test_the_help_epilog_is_generated_and_names_no_command_the_parser_lacks():
    """The first screen is generated from the table, so it names every command and no other."""
    text = epilog()
    named = {line.split()[1] for line in text.splitlines() if line.strip().startswith("decktalk ")}
    assert named == set(BY_NAME)
    for command in COMMANDS:
        assert f"decktalk {command.name}" in text and command.purpose in text
    assert epilog() in build_parser().format_help()


def test_a_command_takes_only_the_exit_flags_its_own_findings_can_reach():
    """`status` and `doctor` write only certain rows and `words` judges nothing, so the rest is refused."""
    for line in (["doctor", "--strict"], ["status", "--strict"], ["words", "--strict"], ["words", "--exit-zero"]):
        with pytest.raises(UsageError):
            build_parser().parse_args(line)
    assert build_parser().parse_args(["doctor", "--exit-zero"]).strict is False
    assert build_parser().parse_args(["status", "--exit-zero"]).strict is False
    quiet = build_parser().parse_args(["words"])
    assert quiet.strict is False and quiet.exit_zero is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3", [3]), ("3,5", [3, 5]), ("7-9", [7, 8, 9]), ("3, 5-6", [3, 5, 6]), ("2-2", [2])],
)
def test_only_accepts_a_number_a_list_and_a_range(text, expected):
    assert sections(text) == expected


def test_only_gathers_every_spelling_into_one_sorted_list():
    """One flag spelling works everywhere, and a range and a repeat gather into one sorted list."""
    args = build_parser().parse_args(["build", "--only", "7-9", "--only", "3,5", "--only", "3"])
    assert args.only == [3, 5, 7, 8, 9]
    assert build_parser().parse_args(["build"]).only is None


@pytest.mark.parametrize("bad", ["x", "9-7", "3-x"])
def test_only_refuses_what_is_not_a_section(bad):
    with pytest.raises(UsageError):
        build_parser().parse_args(["build", "--only", bad])


def test_the_argument_rules_live_in_the_parser_and_not_in_a_handler():
    """`--after` freezes one slide at one cue, so the rule is checked before any stage is reached."""
    from decktalk.cli.parser import parse

    for line in (
        ["screenshots", "--after", "3.1eq"],
        ["screenshots", "--slide", "3.1", "--slide", "3.2", "--after", "3.1eq"],
        ["screenshots", "--slide", "3.1", "--after", "3.1eq", "--section", "3"],
    ):
        with pytest.raises(UsageError, match="--after needs exactly one --slide"):
            parse(line)
    assert parse(["screenshots", "--slide", "3.1", "--after", "3.1eq"]).after == ["3.1eq"]
    # `--before` is the frozen moment just before a cue, and it obeys the same one-slide rule.
    for line in (
        ["screenshots", "--before", "3.1eq"],
        ["screenshots", "--slide", "3.1", "--slide", "3.2", "--before", "3.1eq"],
        ["screenshots", "--slide", "3.1", "--before", "3.1eq", "--section", "3"],
    ):
        with pytest.raises(UsageError, match="--before needs exactly one --slide"):
            parse(line)
    assert parse(["screenshots", "--slide", "3.1", "--before", "3.1eq"]).before == ["3.1eq"]
    # A backwards stage pair is a line the caller can fix, so the parser refuses it as a usage error.
    with pytest.raises(UsageError, match="the stage verify comes after the stage narrate"):
        parse(["build", "--from", "verify", "--to", "narrate"])
    line = parse(["build", "--from", "align", "--to", "verify"])
    assert (line.from_stage, line.to_stage) == (Stage.ALIGN, Stage.VERIFY)
    # A word that names no stage is refused with the five it could have been, in the order they run.
    with pytest.raises(UsageError, match="'measure' is not a stage: narrate, align, record, assemble, verify"):
        parse(["build", "--from", "measure"])


def test_a_refused_line_raises_rather_than_exiting_where_no_caller_can_see_it():
    """Exit code 2 is printed as an envelope under --json, which needs the message, not a SystemExit."""
    with pytest.raises(UsageError, match="invalid choice"):
        build_parser().parse_args(["nonesuch"])
    with pytest.raises(SystemExit) as exit_info:  # --help and --version still leave the process
        build_parser().parse_args(["--help"])
    assert exit_info.value.code == 0


def test_verbose_and_quiet_and_project_parse_on_either_side_of_the_command():
    parser = build_parser()
    for line in (["-v", "status"], ["status", "-v"], ["-p", "d", "status", "-v"], ["status", "-v", "-p", "d"]):
        args = parser.parse_args(line)
        assert getattr(args, "verbose", False) is True and getattr(args, "quiet", False) is False, line
    assert parser.parse_args(["status", "-p", "d"]).project == "d"
    assert parser.parse_args(["init", "d", "-q"]).quiet is True
