"""What a build would spend and show, judged before a single second of it is bought."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import Cancel, Cancelled
from decktalk.events import Log
from decktalk.findings import Code
from decktalk.machine import Machine, Run, Toolchain
from decktalk.results import CheckResult, SpendState
from decktalk.stages.check import NEEDS_A_FRAME, NEEDS_A_PAGE, check

from .conftest import Drawn, a_project, a_run, catalog

CUES = {
    "1": {"cues": [{"cue": "1.1:a", "on": "there"}, {"cue": "1.1:b", "on": "again"}]},
    "2": {"cues": [{"cue": "2.1:a", "on": "speaks"}]},
}
"""A cue file whose phrases every section of the demo script really speaks."""

SCENES = (
    catalog("1", {"1.1": ["1.1:a", "1.1:b"]}),
    catalog("2", {"2.1": ["2.1:a"]}),
)
"""What the demo deck publishes, which is two scenes with one slide each."""


def notes(run: Run) -> list[str]:
    """Every sentence a run said, which is where a reading that is not a judgement goes."""
    said: list[str] = []
    run.machine.events.subscribe(lambda event: said.append(event.message) if isinstance(event, Log) else None)
    return said


def test_a_run_with_no_pages_judges_the_script_and_opens_nothing(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    run = a_run(tmp_path)
    result = check(inputs, run, pages=False)
    assert isinstance(result, CheckResult)
    assert result.pages is False
    assert result.frames is False


def test_a_run_with_no_pages_says_which_judgements_it_could_not_reach(tmp_path: Path) -> None:
    """A new deck gets its first cue rows without a download, so it has to be told what it missed."""
    inputs = a_project(tmp_path, cues=CUES)
    run = a_run(tmp_path)
    said = notes(run)
    check(inputs, run, pages=False)
    missed = " ".join(said)
    assert all(code.name in missed for code in (*NEEDS_A_PAGE, *NEEDS_A_FRAME))


def test_a_run_prices_what_a_voiced_build_would_cost(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    result = check(inputs, a_run(tmp_path), pages=False)
    assert result.spend.state is SpendState.ESTIMATE
    assert result.spend.ceiling_dollars >= result.spend.dollars


def test_a_project_with_no_credential_is_priced_rather_than_refused(tmp_path: Path) -> None:
    """A check is the command a person runs before they have a key, so it never asks for one."""
    inputs = a_project(tmp_path, cues=CUES)
    run = a_run(tmp_path)
    said = notes(run)
    check(inputs, run, pages=False)
    assert any("ELEVENLABS_VOICE_ID" in one for one in said)


def test_a_cue_phrase_nothing_speaks_is_judged_before_anything_is_voiced(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:a", "on": "nowhere"}]}})
    result = check(inputs, a_run(tmp_path), pages=False)
    assert Code.CUE_UNRESOLVED in {one.code for one in result.findings}
    assert result.ok is False


def test_the_files_it_judged_are_the_ones_it_read(tmp_path: Path) -> None:
    """A run without pages opens none of them, so naming one would send a reader looking for a
    judgement nobody made."""
    inputs = a_project(tmp_path, cues=CUES)
    result = check(inputs, a_run(tmp_path), pages=False)
    judged = {one.as_posix() for one in result.judged}
    assert judged == {"script.md", "cues.json"}


def test_a_script_that_cannot_be_read_is_a_judgement_and_not_a_refusal(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    (tmp_path / "script.md").unlink()
    result = check(inputs, a_run(tmp_path), pages=False)
    assert Code.FILE_MISSING in {one.code for one in result.findings}


def test_a_moment_the_cue_file_does_not_list_is_judged_from_the_catalog(tmp_path: Path, drawn: Drawn) -> None:
    """The page's own document says what it declares, so nothing reads a regex over its markup."""
    inputs = a_project(tmp_path)
    drawn.report("deck/index.html", *SCENES)
    result = check(inputs, a_run(tmp_path), frames=False)
    missing = [one for one in result.findings if one.code is Code.CUE_MISSING]
    assert missing
    assert missing[0].fix is not None


def test_a_row_no_page_declares_is_named_rather_than_deleted(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"cue": "1.1:gone", "on": "there"}]}})
    drawn.report("deck/index.html", *SCENES)
    result = check(inputs, a_run(tmp_path), frames=False)
    unknown = [one for one in result.findings if one.code is Code.CUE_UNKNOWN]
    assert [one.location.cue for one in unknown] == ["1.1:gone"]


def test_a_run_without_frames_keeps_the_catalog_and_draws_nothing(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    drawn.report("deck/index.html", *SCENES)
    result = check(inputs, a_run(tmp_path), frames=False)
    assert result.frames is False
    assert drawn.shots == []
    assert result.storyboard is None


def test_a_run_with_frames_freezes_them_and_lays_them_out(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    drawn.report("deck/index.html", *SCENES)
    drawn.share = 40.0
    result = check(inputs, a_run(tmp_path), frames=True)
    assert drawn.shots
    assert result.storyboard == Path("build/storyboard.html")
    assert (tmp_path / "build" / "storyboard.html").is_file()
    assert result.written


def test_a_reveal_that_would_not_be_measured_is_met_here(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    drawn.report("deck/index.html", *SCENES)
    drawn.share = 0.0
    result = check(inputs, a_run(tmp_path), frames=True)
    assert Code.CUE_NO_CHANGE in {one.code for one in result.findings}


def test_what_the_page_said_about_itself_is_judged_by_its_own_code(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    drawn.report(
        "deck/index.html",
        *SCENES,
        warnings=[{"code": "PAGE_SLIDE_DOUBLED", "message": "Two slides carry the id 1.1.", "slide": "1.1"}],
    )
    result = check(inputs, a_run(tmp_path), frames=False)
    assert Code.PAGE_SLIDE_DOUBLED in {one.code for one in result.findings}


def test_it_writes_no_cue_times_because_that_file_belongs_to_cue(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    drawn.report("deck/index.html", *SCENES)
    check(inputs, a_run(tmp_path), frames=True)
    assert not inputs.workspace.cue_times_path.exists()


def test_a_page_a_caller_names_is_judged_although_no_section_plays_it(tmp_path: Path, drawn: Drawn) -> None:
    """A deck page nobody has wired into the project file is still a page worth reading."""
    inputs = a_project(tmp_path, cues=CUES)
    (tmp_path / "deck" / "draft.html").write_text("<div data-scene='9'></div>", encoding="utf-8")
    drawn.report("deck/index.html", *SCENES)
    result = check(inputs, a_run(tmp_path), paths=(tmp_path / "deck" / "draft.html",), frames=False)
    assert Path("deck/draft.html") in result.judged


def test_a_selection_keeps_the_sections_it_names(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    result = check(inputs, a_run(tmp_path), only=[2], pages=False)
    assert {one.location.section for one in result.findings if one.location.section} <= {2}


def test_a_cancelled_run_stops_inside_the_section_it_was_in(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    drawn.report("deck/index.html", *SCENES)
    machine = Machine(environ={}, tables={}, config_path=tmp_path / "m.toml", cwd=tmp_path, toolchain=Toolchain())
    token = Cancel()
    token.cancel()
    run = Run(machine, id="r1", cancel=token, root=tmp_path)
    with pytest.raises(Cancelled):
        check(inputs, run, frames=True)
