"""Every spoken word with its span, on the clock a cue phrase is written against.

What these tests hold is the clock and the spelling: zero is where the section starts, a section's
lead of silence moves every word after it, and the script's own punctuation and case come back from
the take index rather than from the voice, which strips them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import Take, Takes, Words, words_file
from decktalk.errors import Cancel, ErrorCode, InputError, NotBuiltError
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.results import Word
from decktalk.stages.words import section_words, words
from support.projects import write_project

TOML = """
[project]
name = "demo"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[[section]]
number = 2
page = "deck/index.html"
scene = "2"
lead_seconds = 1.25
"""

SPOKEN = {1: "Hello, there.", 2: "Second, section."}
"""What each section says, with the punctuation the script wrote and the voice drops."""


def a_run(root: Path) -> Run:
    machine = Machine(environ={}, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    return Run(machine, id="run-1", cancel=Cancel(), root=root)


def a_take(number: int, *, voiced: bool = True) -> Take:
    return Take(
        section=number,
        key=f"{number:02d}",
        chapter=f"Section {number}",
        hash=f"digest{number}",
        voiced=voiced,
        word_count=2,
        characters=len(SPOKEN[number]),
        estimated_seconds=1.0,
        duration_seconds=1.0,
        spoken=SPOKEN[number],
    )


def an_inputs(root: Path, *, numbers: tuple[int, ...] = (1, 2), voiced: bool = True) -> Inputs:
    write_project(root, TOML)
    inputs = Inputs.load(root, environ={})
    takes = Takes(
        script="script.md",
        model="eleven_multilingual_v2",
        output_format="mp3_44100_128",
        sections=tuple(a_take(number, voiced=voiced) for number in numbers),
    )
    takes.write(inputs.workspace.takes_path)
    for number in numbers:
        spoken = SPOKEN[number].split()
        rows = tuple(
            Word(word=token.strip(",."), start=round(index * 0.5, 3), end=round(index * 0.5 + 0.4, 3))
            for index, token in enumerate(spoken)
        )
        Words(words=rows).write(inputs.workspace.takes_dir / words_file(f"digest{number}"))
    return inputs


def test_every_spoken_section_is_reported_in_script_order(tmp_path: Path) -> None:
    result = words(an_inputs(tmp_path), a_run(tmp_path))
    assert [row.section for row in result.sections] == [1, 2]
    assert [row.key for row in result.sections] == ["01", "02"]


def test_the_words_carry_the_script_spelling_the_voice_dropped(tmp_path: Path) -> None:
    result = words(an_inputs(tmp_path), a_run(tmp_path))
    assert [word.word for word in result.sections[0].words] == ["Hello,", "there."]


def test_the_clock_is_the_section_own_and_a_lead_of_silence_moves_every_word(tmp_path: Path) -> None:
    """A section's lead is silence before its first word, so every word after it sits that much later."""
    inputs = an_inputs(tmp_path)
    result = words(inputs, a_run(tmp_path))
    assert result.sections[0].words[0].start == inputs.settings.narration.lead_seconds
    assert result.sections[1].words[0].start == 1.25


def test_a_build_with_placeholder_narration_says_its_times_are_estimates(tmp_path: Path) -> None:
    result = words(an_inputs(tmp_path, voiced=False), a_run(tmp_path))
    assert all(row.estimated for row in result.sections)


def test_only_keeps_the_sections_it_names(tmp_path: Path) -> None:
    result = words(an_inputs(tmp_path), a_run(tmp_path), only=[2])
    assert [row.section for row in result.sections] == [2]


def test_a_section_with_no_take_is_refused_rather_than_answered_with_nothing(tmp_path: Path) -> None:
    """A caller that read an empty answer for a finished one would write its cue phrase against nothing."""
    with pytest.raises(InputError) as refused:
        words(an_inputs(tmp_path, numbers=(1,)), a_run(tmp_path), only=[2])
    assert refused.value.code is ErrorCode.INPUT
    assert "[1]" in (refused.value.hint or "")


def test_a_project_that_has_not_narrated_is_told_which_command_writes_the_index(tmp_path: Path) -> None:
    write_project(tmp_path, TOML)
    inputs = Inputs.load(tmp_path, environ={})
    with pytest.raises(NotBuiltError) as refused:
        words(inputs, a_run(tmp_path))
    assert refused.value.code is ErrorCode.NOT_BUILT
    assert "decktalk narrate" in (refused.value.hint or "")


def test_the_command_writes_nothing(tmp_path: Path) -> None:
    run = a_run(tmp_path)
    words(an_inputs(tmp_path), run)
    assert run.written == []


def test_one_section_is_read_the_same_way_the_whole_command_reads_it(tmp_path: Path) -> None:
    """`clip` asks for one section through this, so the two agree about every start and every end."""
    inputs = an_inputs(tmp_path)
    whole = words(inputs, a_run(tmp_path))
    assert section_words(inputs, 2) == whole.sections[1]


def test_one_section_of_a_project_that_has_not_narrated_is_nothing_rather_than_a_refusal(tmp_path: Path) -> None:
    write_project(tmp_path, TOML)
    assert section_words(Inputs.load(tmp_path, environ={}), 1) is None


def test_a_section_the_take_index_does_not_hold_is_nothing(tmp_path: Path) -> None:
    assert section_words(an_inputs(tmp_path, numbers=(1,)), 2) is None
