"""The cue stage: every phrase becomes a second, and the two files are read against each other."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes, Take, Takes, Words, words_file
from decktalk.errors import Cancel, Cancelled, NotBuiltError
from decktalk.events import Event, FindingEvent, Log, SectionStart
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.media.pagereport import PageReport
from decktalk.pipeline import Stage
from decktalk.results import CueResult, Word
from decktalk.stages.cue import cue

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

BOX = {"x": 0, "y": 0, "w": 10, "h": 10}
"""One element's box, which none of these cases measures."""


def a_run(root: Path, **environ: str) -> Run:
    """One run, opened straight on a machine, because nothing here needs an events file."""
    machine = Machine(environ=environ, tables={}, config_path=root / "machine.toml", cwd=root, toolchain=Toolchain())
    return Run(machine, id="r1", cancel=Cancel(), root=root)


def a_project(tmp_path: Path, *, cues: dict | None = None, voiced: bool = True) -> Inputs:
    """A project with one take for section one, and the cue file the case asks for."""
    (tmp_path / "deck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deck" / "index.html").write_text("<div data-scene='1'></div>", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    if cues is not None:
        (tmp_path / "cues.json").write_text(json.dumps({"sections": cues}, indent=2), encoding="utf-8")
    inputs = Inputs.load(tmp_path, environ={})
    take = Take(
        section=1,
        key="01",
        chapter="One",
        hash="h",
        voiced=voiced,
        word_count=len(WORDS),
        characters=17,
        estimated_seconds=2.0,
        duration_seconds=2.0,
        speech_end_seconds=1.6,
        sound_end_seconds=1.7,
        spoken="Hello there again",
    )
    Takes(script="script.md", model="m", output_format="mp3_44100_128", sections=(take,)).write(
        inputs.workspace.takes_path
    )
    Words(words=WORDS).write(inputs.workspace.takes_dir / words_file("h"))
    return inputs


def a_recording(inputs: Inputs, section: int, scene: str, moments: dict[str, list[str]]) -> None:
    """A recording log for one section, carrying the catalog its page published."""
    elements = {
        slide: [{"attrs": {}, "moments": {"data-in": wire}, "text": "", "box": BOX} for wire in wires]
        for slide, wires in moments.items()
    }
    report = PageReport.model_validate({"catalog": [{"scene": scene, "elements": elements}]})
    path = inputs.workspace.recording_log(f"{section:02d}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "section": section,
                "url": "http://project.localhost/deck/index.html",
                "input_hash": "abc",
                "requested_seconds": 2.0,
                "settle_seconds": 0.1,
                "load_seconds": 0.1,
                "clock_start_seconds": 0.2,
                "report": report.model_dump(mode="json"),
            }
        ),
        encoding="utf-8",
    )


def lines(run: Run) -> list[Event]:
    """Every line this run put on the stream, which a test subscribes to before it calls a stage."""
    seen: list[Event] = []
    run.machine.events.subscribe(seen.append)
    return seen


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


def test_the_run_reports_every_judgement_as_it_makes_it(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "nowhere"}]}})
    run = a_run(tmp_path)
    seen = lines(run)
    result = cue(inputs, run)
    reported = [one.finding.code for one in seen if isinstance(one, FindingEvent)]
    assert reported == [Code.CUE_UNRESOLVED]
    assert [one.code for one in result.findings] == [Code.CUE_UNRESOLVED]
    assert result.ok is False, "an unresolved phrase is certain, so the call did not pass"


def test_a_section_opens_and_closes_on_the_stream(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}})
    run = a_run(tmp_path)
    seen = lines(run)
    cue(inputs, run)
    assert [one.section for one in seen if isinstance(one, SectionStart)] == [1]


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
    Words(words=(*WORDS, Word(word="Hello", start=2.0, end=2.4))).write(inputs.workspace.takes_dir / words_file("h"))
    run = a_run(tmp_path)
    seen = lines(run)
    result = cue(inputs, run)
    said = [one.message for one in seen if isinstance(one, Log)]
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
