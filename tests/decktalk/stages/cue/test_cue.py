"""The cue stage: every phrase becomes a second, and the two files are read against each other."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import decktalk.stages.cue as stage
from decktalk.artifacts import CueTimes
from decktalk.errors import Cancelled, NotBuiltError
from decktalk.events import FindingRaised, SectionStart
from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.media.pagereport import PageReport
from decktalk.pipeline import Stage
from decktalk.results import CueResult, Word
from decktalk.stages.cue import cue
from support.pages import SCENE_ONE, a_report, catalog, elements, write_log
from support.projects import load_project
from support.runs import Watched, a_run, notes
from support.takes import a_take, narrated

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


def a_project(
    tmp_path: Path, *, cues: dict | None = None, voiced: bool = True, words: Sequence[Word] = WORDS
) -> Inputs:
    """A project with one take for section one speaking `words`, and the cue file the case asks for."""
    inputs = load_project(tmp_path, TOML, page=SCENE_ONE, cues=cues)
    take = a_take(1, seconds=2.0, voiced=voiced, sound_end_seconds=1.7, lead_seconds=inputs.lead_seconds(1))
    narrated(inputs, take, words={1: words})
    return inputs


def a_recording(inputs: Inputs, section: int, scene: str, moments: dict[str, list[str]]) -> None:
    """A recording log for one section, carrying the catalog its page published when it was recorded."""
    report = PageReport.model_validate({"catalog": [{"scene": scene, "elements": elements(moments, text="")}]})
    write_log(inputs, section, report=report)


@dataclass
class Live:
    """The page as it is now, which the stage reads in a browser, and every page it opened to read it."""

    reports: dict[str, PageReport] = field(default_factory=dict)
    opened: list[str] = field(default_factory=list)

    def publishes(self, scene: str, moments: dict[str, list[str]]) -> None:
        """Declare the catalog `deck/index.html` publishes when it is opened now."""
        self.reports["deck/index.html"] = a_report(catalog=[catalog(scene, moments, text="")])


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Live:
    """The browser seams the stage reads, replaced so it opens nothing and says what it asked for."""
    made = Live()

    @contextmanager
    def chromium(_executable: str = "", **_launch: object) -> Iterator[object]:
        yield object()

    def reports_of(_page: object, _inputs: Inputs, files: Sequence[str]) -> dict[str, PageReport]:
        made.opened += files
        return {name: made.reports[name] for name in files if name in made.reports}

    monkeypatch.setattr(stage, "chromium", chromium)
    monkeypatch.setattr(stage, "open_project_page", lambda *_args, **_kwargs: (object(), object()))
    monkeypatch.setattr(stage, "reports_of", reports_of)
    return made


def standing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every recording on disk still stands, as it does when nothing it was made from has changed since."""
    monkeypatch.setattr(stage, "stale_recording", lambda *_args: None)


# ---- what the stage answers with ---------------------------------------------------------------


def test_every_phrase_becomes_a_second_on_its_own_section_clock(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there", "offset_seconds": 0.25}]}})
    result = cue(inputs, a_run(tmp_path))
    assert isinstance(result, CueResult)
    (block,) = result.sections
    (row,) = block.cues
    lead = inputs.lead_seconds(1)
    assert row.seconds == round(0.5 + lead + 0.25, 3)
    assert (row.id, row.phrase, row.nudge_seconds) == ("1.1:a", "there", 0.25)
    assert block.estimated is False and block.key == "01"


def test_the_artifact_and_the_result_are_one_shape(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}})
    result = cue(inputs, a_run(tmp_path))
    written = CueTimes.read(inputs.workspace.cue_times_path)
    assert written is not None and written.sections == result.sections
    assert result.file == Path("build/cue-times.json")
    assert result.written == (Path("build/cue-times.json"),)


def test_the_run_reports_every_judgement_as_it_makes_it(tmp_path: Path, make_run: Callable[..., Watched]) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "nowhere"}]}})
    watched = make_run(inputs)
    result = cue(inputs, watched.run)
    reported = [one.finding.code for one in watched.lines if isinstance(one, FindingRaised)]
    assert reported == [Code.CUE_UNRESOLVED]
    assert [one.code for one in result.findings] == [Code.CUE_UNRESOLVED]
    assert result.ok is False, "an unresolved phrase is an error, so the call did not pass"


def test_a_section_opens_and_closes_on_the_stream(tmp_path: Path, make_run: Callable[..., Watched]) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}})
    watched = make_run(inputs)
    cue(inputs, watched.run)
    assert [one.section for one in watched.lines if isinstance(one, SectionStart)] == [1]


def test_a_cancelled_run_stops_inside_the_section_it_was_in(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}})
    run = a_run(tmp_path)
    run.cancel.cancel()
    with pytest.raises(Cancelled):
        cue(inputs, run)


def test_a_project_with_no_take_index_is_told_which_stage_writes_one(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}})
    inputs.workspace.takes_path.unlink()
    with pytest.raises(NotBuiltError) as refused:
        cue(inputs, a_run(tmp_path))
    assert "decktalk narrate" in (refused.value.hint or "")


def test_a_section_with_no_take_is_unresolved_while_one_with_no_words_has_a_start(tmp_path: Path) -> None:
    """Cue keys its words on the take index, so a page section with no row has no take to place a cue against."""
    inputs = a_project(tmp_path, cues={"2": {"cues": [{"id": "2.1:a", "phrase": "hello"}]}})
    result = cue(inputs, a_run(tmp_path))
    (finding,) = result.findings
    assert finding.code is Code.CUE_UNRESOLVED
    assert finding.message == (
        "the cue 2.1:a waits for 'hello' and section 2 has no take, so there are no words to place it against."
    )


def test_a_repeated_phrase_is_a_line_on_the_stream_and_never_a_judgement(tmp_path: Path) -> None:
    inputs = a_project(
        tmp_path,
        cues={"1": {"cues": [{"id": "1.1:a", "phrase": "Hello"}]}},
        words=(*WORDS, Word(word="Hello", start=2.0, end=2.4)),
    )
    run = a_run(tmp_path)
    said = notes(run)
    result = cue(inputs, run)
    assert any("occurs 2 times" in one for one in said)
    assert result.findings == ()


def test_a_run_that_names_sections_keeps_the_rows_of_the_others(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}})
    cue(inputs, a_run(tmp_path))
    before = CueTimes.read(inputs.workspace.cue_times_path)
    assert before is not None and [one.section for one in before.sections] == [1]
    result = cue(inputs, a_run(tmp_path), only=[2])
    after = CueTimes.read(inputs.workspace.cue_times_path)
    assert after is not None and [one.section for one in after.sections] == [1]
    assert result.sections == (), "the result reports what this run resolved and not the whole file"


# ---- the two files read against each other ------------------------------------------------------


def test_a_moment_the_cue_file_does_not_list_is_missing_with_a_fix(
    tmp_path: Path, live: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording that still stands is the page as it is, so its catalog is read and no browser is opened."""
    standing(monkeypatch)
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}})
    a_recording(inputs, 1, "1", {"1.1": ["1.1:a", "1.1:b"]})
    result = cue(inputs, a_run(tmp_path))
    (judged,) = [one for one in result.findings if one.code is Code.CUE_UNLISTED]
    assert "1.1:b" in judged.message and judged.fix is not None
    assert judged.stage is Stage.CUE
    assert live.opened == []


def test_a_row_no_page_declares_is_always_reported(tmp_path: Path, live: Live, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whether the finding fails the run is the caller's threshold, so the stage reports it every time."""
    standing(monkeypatch)
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"},
                                                     {"id": "1.9:gone", "phrase": "again"}]}})  # fmt: skip
    a_recording(inputs, 1, "1", {"1.1": ["1.1:a"]})
    codes = {one.code for one in cue(inputs, a_run(tmp_path)).findings}
    assert Code.CUE_UNKNOWN in codes
    assert "allow_unknown" not in inspect.signature(cue).parameters
    assert live.opened == []


def test_a_cue_renamed_in_the_page_and_the_cue_file_is_judged_against_the_page_as_it_is_now(
    tmp_path: Path, live: Live
) -> None:
    """The last recording kept the old name, and record has not run since, so its catalog is not the page's.

    A build stopped here with CUE_UNKNOWN for the new name and CUE_UNLISTED for the old one, although
    the page and the cue file agreed. The recording no longer stands, so the page is read as it is now.
    """
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:b", "phrase": "there"}]}})
    a_recording(inputs, 1, "1", {"1.1": ["1.1:a"]})
    live.publishes("1", {"1.1": ["1.1:b"]})
    codes = {one.code for one in cue(inputs, a_run(tmp_path)).findings}
    assert codes.isdisjoint({Code.CUE_UNKNOWN, Code.CUE_UNLISTED})
    assert live.opened == ["deck/index.html"]


def test_a_page_changed_since_its_recording_is_judged_as_it_is_now(tmp_path: Path, live: Live) -> None:
    """A row the page no longer declares is named from the page as it is, not from what it said when recorded."""
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}})
    a_recording(inputs, 1, "1", {"1.1": ["1.1:a"]})
    live.publishes("1", {"1.1": ["1.1:c"]})
    found = {one.code: one for one in cue(inputs, a_run(tmp_path)).findings}
    assert found[Code.CUE_UNKNOWN].location.cue == "1.1:a"
    assert "1.1:c" in found[Code.CUE_UNLISTED].message


def test_a_page_nothing_has_recorded_is_left_unjudged(tmp_path: Path, live: Live) -> None:
    """A catalog nobody published cannot say a row is unknown, so nothing is guessed about it."""
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "9.9:x", "phrase": "there"}]}})
    codes = {one.code for one in cue(inputs, a_run(tmp_path)).findings}
    assert codes == set()
    assert live.opened == []


# ---- a dialogue ----------------------------------------------------------------------------------

TALK = TOML + '\n[voices.narrator]\nid = "n"\n\n[voices.guest]\nid = "g"\n'
"""The demo project with a guest beside its narrator."""


def a_dialogue(tmp_path: Path, cues: dict) -> Inputs:
    """Section one as two turns: the narrator says "Hello there" and the guest says "General Kenobi, hello"."""
    inputs = load_project(tmp_path, TALK, page=SCENE_ONE, cues=cues)
    narrator = a_take(1, seconds=1.0, lead_seconds=0.5, tail_seconds=0.3, digest="0000000000000001")
    guest = a_take(1, turn=2, voice="guest", seconds=2.0, tail_seconds=0.7, digest="0000000000000002")
    words = {
        (1, 1): (Word(word="Hello", start=0.0, end=0.4), Word(word="there", start=0.5, end=1.0)),
        (1, 2): (
            Word(word="General", start=0.0, end=0.5),
            Word(word="Kenobi", start=0.6, end=1.0),
            Word(word="hello", start=1.2, end=1.6),
        ),
    }
    narrated(inputs, narrator, guest, words=words)
    return inputs


def test_a_cue_phrase_that_spans_two_turns_resolves_to_its_first_word(tmp_path: Path) -> None:
    inputs = a_dialogue(tmp_path, {"1": {"cues": [{"id": "1.1:a", "phrase": "there General"}]}})
    (block,) = cue(inputs, a_run(tmp_path)).sections
    (row,) = block.cues
    assert row.seconds == round(0.5 + 0.5, 3)


def test_an_occurrence_counts_across_the_turns_of_its_section(tmp_path: Path) -> None:
    """The second "hello" is the guest's, after the narrator's first, because one section is one clock."""
    inputs = a_dialogue(tmp_path, {"1": {"cues": [{"id": "1.1:a", "phrase": "hello", "occurrence": 2}]}})
    (block,) = cue(inputs, a_run(tmp_path)).sections
    (row,) = block.cues
    assert row.seconds == round(0.5 + 1.0 + 0.3 + 1.2, 3)
