"""What the script would sound like, judged with the line each rule is broken on."""

from __future__ import annotations

from pathlib import Path

from decktalk.findings import Code, Severity
from decktalk.inputs.script import parse_script
from decktalk.stages.check.script import (
    placeholder_findings,
    placeholder_rows,
    script_findings,
    symbol_findings,
    unused_voices,
    voice_findings,
)

SCRIPT = """# Demo

## 1. One

The number is [NUMBER] and the rest is written out.

## 2. Two

Nothing here needs filling in.
"""

WHERE = Path("script.md")


def test_an_open_placeholder_is_found_with_its_line() -> None:
    assert placeholder_rows(SCRIPT) == [(5, "NUMBER")]


def test_a_filled_paragraph_holds_no_placeholder() -> None:
    assert placeholder_rows("## 1. One\n\nNothing to fill in here.\n") == []


def test_an_open_placeholder_is_an_error_naming_its_line() -> None:
    """A voiced run reads unfinished text out, and the take has already been paid for."""
    (found,) = placeholder_findings(SCRIPT, script=WHERE)
    assert found.code is Code.SCRIPT_UNFINISHED
    assert found.severity is Severity.ERROR
    assert found.location.line == 5
    assert found.location.section == 1
    assert "NUMBER" in found.message


def test_a_brace_is_judged_under_the_same_code_as_a_placeholder() -> None:
    """The voice reads a brace out too, which is the one rule `narrate` refuses on."""
    (found,) = placeholder_findings("## 1. One\n\nThe value is {value} today.\n", script=WHERE)
    assert found.code is Code.SCRIPT_UNFINISHED
    assert "brace" in found.message


def test_a_note_inside_a_paragraph_is_judged_because_it_becomes_a_pause() -> None:
    (found,) = placeholder_findings("## 1. One\n\nThe answer [as we saw] is here.\n", script=WHERE)
    assert found.code is Code.SCRIPT_UNFINISHED
    assert "pause" in found.message


def test_a_beat_inside_a_paragraph_is_left_alone() -> None:
    """A beat is a direction the parser turns into the pause the author asked for."""
    assert placeholder_findings("## 1. One\n\nOne thing [beat] and then another.\n", script=WHERE) == []


def test_a_digit_is_a_warning_against_its_section() -> None:
    """The author may want the voice to try "41", so the finding names it and decides nothing."""
    (section,) = parse_script("## 3. Three\n\nAbout 41 percent of them.\n")
    (found,) = symbol_findings([section], script=WHERE)
    assert found.code is Code.SCRIPT_SPOKEN_SYMBOL
    assert found.severity is Severity.WARNING
    assert found.location.section == 3
    assert "41" in found.message


def test_a_section_written_out_in_words_is_left_alone() -> None:
    (section,) = parse_script("## 3. Three\n\nAbout forty one percent of them.\n")
    assert symbol_findings([section], script=WHERE) == []


def test_the_two_rules_are_reported_by_one_call() -> None:
    sections = parse_script(SCRIPT)
    codes = {found.code for found in script_findings(SCRIPT, sections, script=WHERE)}
    assert codes == {Code.SCRIPT_UNFINISHED}


def test_every_judgement_names_the_script_it_is_about() -> None:
    sections = parse_script("## 1. One\n\nThe number is [NUMBER] at 41 percent.\n")
    found = script_findings("## 1. One\n\nThe number is [NUMBER] at 41 percent.\n", sections, script=WHERE)
    assert {one.location.file for one in found} == {WHERE}
    assert {one.code for one in found} == {Code.SCRIPT_UNFINISHED, Code.SCRIPT_SPOKEN_SYMBOL}


# ---- voices -------------------------------------------------------------------------------------

NAMES = ("narrator", "guest")
"""The voices of the project these scripts are judged against, the default first."""

VOICED = """## 1. Open

Nobody names me, so the default reads me.

## 2. Talk

Hello.

[voice gest]

Hi.

[voice.guest]

[guest]

Bye.
"""


def test_each_voice_code_is_raised_with_its_sentence() -> None:
    found = voice_findings(VOICED, NAMES, script=WHERE)
    assert [(one.code, one.severity, one.location.line, one.location.section) for one in found] == [
        (Code.SCRIPT_VOICE_UNMARKED, Severity.ERROR, 7, 2),
        (Code.SCRIPT_VOICE_UNKNOWN, Severity.ERROR, 9, 2),
        (Code.SCRIPT_VOICE_UNKNOWN, Severity.ERROR, 13, 2),
        (Code.SCRIPT_VOICE_BARE, Severity.WARNING, 15, 2),
    ]
    assert [one.message for one in found] == [
        "script.md line 7 starts a turn of section 2 that no [voice NAME] line names, and section 2 names a voice, "
        "so every turn of it names its own. Put [voice NAME] on its own line before it.",
        "script.md line 9 holds [voice gest], and no voice is named gest. The voices are narrator, guest. "
        "Did you mean 'guest'?",
        "script.md line 13 holds [voice.guest], which is not a voice direction. Write [voice NAME] on a line of "
        "its own, where NAME is one of narrator, guest.",
        "script.md line 15 holds [guest], a note that plays as a beat, and guest is a voice. Write [voice guest] "
        "on a line of its own to hand the turns after it to guest.",
    ]


def test_a_script_whose_voices_read_raises_no_voice_finding() -> None:
    script = "## 1. Talk\n\n[voice narrator]\n\nHello.\n\n[voice guest]\n\nHi.\n"
    assert voice_findings(script, NAMES, script=WHERE) == []


def test_a_declared_voice_no_turn_reads_is_a_warning() -> None:
    toml = '[voices.narrator]\nid = "n"\n\n[voices.guest]\nid = "g"\n\n[voices.aside]\nid = "a"\n'
    sections = parse_script("## 1. Open\n\nThe default reads me.\n\n## 2. Talk\n\n[voice guest]\n\nHi.\n")
    (found,) = unused_voices(
        sections, ("narrator", "aside", "guest"), "narrator", script=WHERE, project=Path("decktalk.toml"), text=toml
    )
    assert found.code is Code.VOICE_UNUSED
    assert found.severity is Severity.WARNING
    assert (
        found.message
        == "decktalk.toml declares the voice aside in [voices.aside], and no turn of script.md is read by it."
    )
    assert (found.location.where, found.location.file, found.location.line) == (
        "[voices.aside]",
        Path("decktalk.toml"),
        7,
    )
