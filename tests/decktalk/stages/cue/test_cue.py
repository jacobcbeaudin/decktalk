"""The cue stage: every phrase becomes a second, and the two files are read against each other."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes, Words, words_file
from decktalk.errors import Cancelled, NotBuiltError
from decktalk.events import FindingEvent, SectionStart
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.media.pagereport import PageReport
from decktalk.pipeline import Stage
from decktalk.results import CueResult, Word
from decktalk.stages.cue import cue
from support.pages import SCENE_ONE, elements, write_log
from support.projects import load_project
from support.runs import Watched, a_run, notes
from support.takes import a_take, write_takes

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
"""

WORDS = (
    Word(word="Hello", start=0.0, end=0.4),
    Word(word="there", start=0.5, end=1.0),
    Word(word="again", start=1.1, end=1.6),
)
"""The words section one speaks, before its own lead is added to them."""


def a_project(tmp_path: Path, *, cues: dict | None = None, voiced: bool = True) -> Inputs:
    """A project with one take for section one, and the cue file the case asks for."""
    inputs = load_project(tmp_path, TOML, page=SCENE_ONE, cues=cues)
    write_takes(inputs, a_take(1, seconds=2.0, hash="0123456789abcdef", voiced=voiced, sound_end_seconds=1.7))
    Words(words=WORDS).write(inputs.workspace.takes_dir / words_file("0123456789abcdef"))
    return inputs


def a_recording(inputs: Inputs, section: int, scene: str, moments: dict[str, list[str]]) -> None:
    """A recording log for one section, carrying the catalog its page published."""
    report = PageReport.model_validate({"catalog": [{"scene": scene, "elements": elements(moments, text="")}]})
    write_log(inputs, section, report=report)


# ---- what the stage answers with ---------------------------------------------------------------


def test_every_phrase_becomes_a_second_on_its_own_section_clock(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there", "offset": 0.25}]}})
    result = cue(inputs, a_run(tmp_path))
    assert isinstance(result, CueResult)
    (block,) = result.sections
    (row,) = block.cues
    lead = inputs.lead_seconds(1)
    assert row.seconds == round(0.5 + lead + 0.25, 3)
    assert (row.cue, row.phrase, row.offset) == ("1.1:a", "there", 0.25)
    assert block.estimated is False and block.key == "01"


def test_the_artifact_and_the_result_are_one_shape(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}})
    result = cue(inputs, a_run(tmp_path))
    written = CueTimes.read(inputs.workspace.cue_times_path)
    assert written is not None and written.sections == result.sections
    assert result.file == Path("build/cue-times.json")
    assert result.written == (Path("build/cue-times.json"),)


def test_the_run_reports_every_judgement_as_it_makes_it(tmp_path: Path, make_run: Callable[..., Watched]) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "nowhere"}]}})
    watched = make_run(inputs)
    result = cue(inputs, watched.run)
    reported = [one.finding.code for one in watched.lines if isinstance(one, FindingEvent)]
    assert reported == [Code.CUE_UNRESOLVED]
    assert [one.code for one in result.findings] == [Code.CUE_UNRESOLVED]
    assert result.ok is False, "an unresolved phrase is certain, so the call did not pass"


def test_a_section_opens_and_closes_on_the_stream(tmp_path: Path, make_run: Callable[..., Watched]) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}})
    watched = make_run(inputs)
    cue(inputs, watched.run)
    assert [one.section for one in watched.lines if isinstance(one, SectionStart)] == [1]


def test_a_cancelled_run_stops_inside_the_section_it_was_in(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}})
    run = a_run(tmp_path)
    run.cancel.cancel()
    with pytest.raises(Cancelled):
        cue(inputs, run)


def test_a_project_with_no_take_index_is_told_which_stage_writes_one(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}})
    inputs.workspace.takes_path.unlink()
    with pytest.raises(NotBuiltError) as refused:
        cue(inputs, a_run(tmp_path))
    assert "decktalk narrate" in (refused.value.hint or "")


def test_a_repeated_phrase_is_a_line_on_the_stream_and_never_a_judgement(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "Hello"}]}})
    Words(words=(*WORDS, Word(word="Hello", start=2.0, end=2.4))).write(
        inputs.workspace.takes_dir / words_file("0123456789abcdef")
    )
    run = a_run(tmp_path)
    said = notes(run)
    result = cue(inputs, run)
    assert any("occurs 2 times" in one for one in said)
    assert result.findings == ()


def test_a_run_that_names_sections_keeps_the_rows_of_the_others(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}})
    cue(inputs, a_run(tmp_path))
    before = CueTimes.read(inputs.workspace.cue_times_path)
    assert before is not None and [one.section for one in before.sections] == [1]
    result = cue(inputs, a_run(tmp_path), only=[2])
    after = CueTimes.read(inputs.workspace.cue_times_path)
    assert after is not None and [one.section for one in after.sections] == [1]
    assert result.sections == (), "the result reports what this run resolved and not the whole file"


# ---- the two files read against each other ------------------------------------------------------


def test_a_moment_the_cue_file_does_not_list_is_missing_with_a_fix(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}})
    a_recording(inputs, 1, "1", {"1.1": ["1.1:a", "1.1:b"]})
    result = cue(inputs, a_run(tmp_path))
    (judged,) = [one for one in result.findings if one.code is Code.CUE_MISSING]
    assert "1.1:b" in judged.message and judged.fix is not None
    assert judged.stage is Stage.CUE


def test_a_row_no_page_declares_is_unknown_unless_the_caller_allows_it(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"},
                                                     {"cue": "1.9:gone", "on": "again"}]}})  # fmt: skip
    a_recording(inputs, 1, "1", {"1.1": ["1.1:a"]})
    codes = {one.code for one in cue(inputs, a_run(tmp_path)).findings}
    assert Code.CUE_UNKNOWN in codes
    allowed = {one.code for one in cue(inputs, a_run(tmp_path), allow_unknown=True).findings}
    assert Code.CUE_UNKNOWN not in allowed


def test_a_page_nothing_has_recorded_is_left_unjudged(tmp_path: Path) -> None:
    """A catalog nobody published cannot say a row is unknown, so nothing is guessed about it."""
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "9.9:x", "on": "there"}]}})
    codes = {one.code for one in cue(inputs, a_run(tmp_path)).findings}
    assert codes == set()
