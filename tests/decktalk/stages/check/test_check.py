"""What a build would cost and show, judged before a single second of it is bought."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from decktalk.errors import Cancelled, ToolError
from decktalk.findings import Code, Severity
from decktalk.inputs import Inputs
from decktalk.machine.fixes import apply_fix
from decktalk.results import CheckResult, CostState, Scope, up_to_the_cent
from decktalk.settings import BY_ID
from decktalk.speech import canonical_text
from decktalk.stages.check import NEEDS_A_FRAME, NEEDS_A_PAGE, check
from support.pages import SCRIPT, SPIN_CEILING_SECONDS, TOML, a_project, a_spinning_project, catalog
from support.runs import a_run, notes
from support.takes import a_take, hold_take, write_takes

from .conftest import Drawn

CUES = {
    "1": {"cues": [{"id": "1.1:a", "phrase": "there"}, {"id": "1.1:b", "phrase": "again"}]},
    "2": {"cues": [{"id": "2.1:a", "phrase": "speaks"}]},
}
"""A cue file whose phrases every section of the demo script really speaks."""

SCENES = (
    catalog("1", {"1.1": ["1.1:a", "1.1:b"]}),
    catalog("2", {"2.1": ["2.1:a"]}),
)
"""What the demo deck publishes, which is two scenes with one slide each."""


def test_a_run_with_no_pages_judges_the_script_and_opens_nothing(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    run = a_run(tmp_path)
    result = check(inputs, run, pages=False)
    assert isinstance(result, CheckResult)
    assert result.pages_opened is False
    assert result.frames_compared is False


def test_a_run_with_no_pages_says_which_judgements_it_could_not_reach(tmp_path: Path) -> None:
    """A new deck gets its first cue rows without a download, so it has to be told what it missed."""
    inputs = a_project(tmp_path, cues=CUES)
    run = a_run(tmp_path)
    said = notes(run)
    check(inputs, run, pages=False)
    missed = " ".join(said)
    assert all(code.name in missed for code in (*NEEDS_A_PAGE, *NEEDS_A_FRAME))


def test_a_run_with_no_pages_never_says_frames_were_asked_for(tmp_path: Path) -> None:
    """Nobody asks for frames on `check --no-pages`, so the line says what happened and nothing about asking."""
    inputs = a_project(tmp_path, cues=CUES)
    run = a_run(tmp_path)
    said = notes(run)
    check(inputs, run, pages=False)
    assert not any("asked for" in line for line in said)
    assert "No frame was frozen, because no page was opened." in said


def test_a_run_prices_what_a_voiced_build_would_cost(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    result = check(inputs, a_run(tmp_path), pages=False)
    assert result.cost.state is CostState.ESTIMATE
    assert result.cost.ceiling_dollars >= result.cost.dollars


def test_a_project_with_no_credential_is_priced_rather_than_refused(tmp_path: Path) -> None:
    """A check is the command a person runs before they have a key, so it never asks for one."""
    inputs = a_project(tmp_path, cues=CUES)
    run = a_run(tmp_path)
    said = notes(run)
    result = check(inputs, run, pages=False)
    assert result.cost.state is CostState.ESTIMATE
    # The plan says which credential was missing, and says it once.
    assert len([one for one in said if "is not set" in one]) == 1
    assert not any("priced as new" in one for one in said)


PRICED = TOML.replace("[[section]]", "[elevenlabs]\ndollars_per_1000_characters = 0.30\n\n[[section]]", 1)
"""The demo project at a stated rate, so a take it must buy has a price above nothing."""


def voiced_once(tmp_path: Path) -> Inputs:
    """The demo project with a voiced take of each section held and indexed, read with no voice named."""
    inputs = a_project(tmp_path, toml=PRICED, cues=CUES)
    rows = [a_take(section.number, spoken=section.spoken) for section in inputs.spoken()]
    for row in rows:
        hold_take(inputs, row.digest)
    write_takes(inputs, *rows)
    return inputs


def test_a_check_reads_the_take_index_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = voiced_once(tmp_path)
    reads: list[None] = []
    real = Inputs.takes

    def counted(self: Inputs) -> object:
        reads.append(None)
        return real(self)

    monkeypatch.setattr(Inputs, "takes", counted)
    check(inputs, a_run(tmp_path), pages=False)
    assert len(reads) == 1


def test_with_no_voice_named_an_edited_section_is_priced_as_certain(tmp_path: Path) -> None:
    """A take of older words is needed whatever the voice is, so its characters go into the price."""
    voiced_once(tmp_path)
    edited = a_project(
        tmp_path, toml=PRICED, script=SCRIPT.replace("Hello there again.", "Hello once more there again."), cues=CUES
    )
    result = check(edited, a_run(tmp_path), pages=False)
    assert result.cost.sections == (1, 2)
    assert result.cost.dollars > 0


def test_a_check_given_a_turn_prices_voicing_that_turn_again_and_nothing_else(tmp_path: Path) -> None:
    """Only the named turn is in the price, where a check of the whole project prices every section.

    No voice is named here, so no take on disk can be matched and each turn is priced into the ceiling
    alone, at its own characters.
    """
    inputs = voiced_once(tmp_path)
    assert check(inputs, a_run(tmp_path), pages=False).cost.sections == (1, 2)
    result = check(inputs, a_run(tmp_path), pages=False, turns=[(2, 1)])
    [turn] = inputs.spoken()[1].turns
    assert result.cost.sections == (2,)
    dollars_per_character = 0.30 / 1000
    assert result.cost.ceiling_dollars == up_to_the_cent([len(canonical_text(turn.pieces)) * dollars_per_character])


def test_a_cue_phrase_nothing_speaks_is_judged_before_anything_is_voiced(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:a", "phrase": "nowhere"}]}})
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
    # The reason is a whole sentence of its own, so it follows a colon and ends with one full stop.
    [missing] = [one for one in result.findings if one.code is Code.FILE_MISSING]
    assert missing.message.endswith(": script.md is not there."), missing.message
    assert ".)" not in missing.message and ".." not in missing.message


def test_a_moment_the_cue_file_does_not_list_is_judged_from_the_catalog(tmp_path: Path, drawn: Drawn) -> None:
    """The page's own document says what it declares, so nothing reads a regex over its markup."""
    inputs = a_project(tmp_path)
    drawn.report("deck/index.html", *SCENES)
    result = check(inputs, a_run(tmp_path), frames=False)
    missing = [one for one in result.findings if one.code is Code.CUE_UNLISTED]
    assert missing
    assert missing[0].fix is not None


def test_a_row_no_page_declares_is_named_rather_than_deleted(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues={"1": {"cues": [{"id": "1.1:gone", "phrase": "there"}]}})
    drawn.report("deck/index.html", *SCENES)
    result = check(inputs, a_run(tmp_path), frames=False)
    unknown = [one for one in result.findings if one.code is Code.CUE_UNKNOWN]
    assert [one.location.cue for one in unknown] == ["1.1:gone"]


def test_a_run_without_frames_keeps_the_catalog_and_draws_nothing(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    drawn.report("deck/index.html", *SCENES)
    result = check(inputs, a_run(tmp_path), frames=False)
    assert result.frames_compared is False
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
    run = a_run(tmp_path)
    run.cancel.cancel()
    with pytest.raises(Cancelled):
        check(inputs, run, frames=True)


def test_a_phrase_an_edit_moved_is_repaired_by_the_fix_its_finding_carries(tmp_path: Path) -> None:
    """The script was edited from "there again" to "there once again", and the cue kept the old phrase."""
    edited = {"1": {"cues": [{"id": "1.1:a", "phrase": "there agian"}]}}
    inputs = a_project(tmp_path, cues=edited)
    run = a_run(tmp_path)
    result = check(inputs, run, pages=False)
    (found,) = [one for one in result.findings if one.code is Code.CUE_UNRESOLVED]
    assert found.location.file == Path("cues.json") and found.location.line is not None
    assert found.fix is not None
    outcome = apply_fix(run, found.code, found.fix, root=tmp_path, scope=Scope.PROJECT, unsafe=True)
    assert outcome.applied
    again = check(Inputs.load(tmp_path, environ={}), a_run(tmp_path), pages=False)
    assert Code.CUE_UNRESOLVED not in {one.code for one in again.findings}


def test_an_untrusted_project_opens_its_pages_untrusted(tmp_path: Path, drawn: Drawn) -> None:
    """A page `record` would sandbox must not reach the network through `check` instead."""
    a_project(tmp_path)
    inputs = Inputs.load(tmp_path, environ={BY_ID["record.page_policy"].environment: "untrusted"})
    drawn.report("deck/index.html", *SCENES)
    check(inputs, a_run(tmp_path), frames=False)
    assert drawn.policies == ["untrusted"]


PAUSED_SCRIPT = """# Demo

## 1. One

Hello there. [pause 2] Again.

## 2. Two

Second section [beat] speaks as well.
"""
"""A script with one timed pause in section 1 and only a beat in section 2."""


def on_model(model: str) -> str:
    """The demo project read on `model`."""
    return f'{TOML}\n[elevenlabs]\nmodel = "{model}"\n'


def test_a_timed_pause_on_a_model_that_reads_no_break_tag_is_an_error(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, toml=on_model("eleven_v3"), script=PAUSED_SCRIPT)
    result = check(inputs, a_run(tmp_path), pages=False)
    dropped = [one for one in result.findings if one.code is Code.SCRIPT_PAUSE_DROPPED]
    assert [one.location.section for one in dropped] == [1]
    assert dropped[0].severity is Severity.ERROR
    assert "eleven_v3" in dropped[0].message
    assert result.ok is False


def test_the_pause_finding_reads_the_voice_in_force(tmp_path: Path) -> None:
    """The finding asks the voice the project is read in, never the provider's table again."""
    inputs = a_project(tmp_path, toml=on_model("eleven_multilingual_v2"), script=PAUSED_SCRIPT)
    narrator = replace(inputs.voices["narrator"], model="a-model-that-drops-it", renders_pauses=False)
    dropping = replace(inputs, voices={"narrator": narrator})
    result = check(dropping, a_run(tmp_path), pages=False)
    (dropped,) = [one for one in result.findings if one.code is Code.SCRIPT_PAUSE_DROPPED]
    assert "a-model-that-drops-it" in dropped.message


def test_a_model_that_reads_a_break_tag_earns_no_pause_finding(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, toml=on_model("eleven_multilingual_v2"), script=PAUSED_SCRIPT)
    result = check(inputs, a_run(tmp_path), pages=False)
    assert not [one for one in result.findings if one.code is Code.SCRIPT_PAUSE_DROPPED]


def test_check_marks_a_take_with_no_word_times_estimated_and_reports_no_unresolved_cue(tmp_path: Path) -> None:
    """A take its voice sent no word times with has words estimated over its sound until an aligner times it."""
    inputs = voiced_once(tmp_path)
    result = check(inputs, a_run(tmp_path), pages=False)
    assert Code.CUE_UNRESOLVED not in {one.code for one in result.findings}
    assert result.ok is True


UNREADABLE_VOICES = """# Demo

## 1. One

Hello there again.

## 2. Two

[voice gest]

Second section speaks as well.
"""
"""The demo script with a voice direction naming a voice the project never declares."""


def test_a_script_whose_voices_do_not_read_is_judged_and_nothing_is_priced(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, toml=PRICED, script=UNREADABLE_VOICES, cues=CUES)
    run = a_run(tmp_path)
    said = notes(run)
    result = check(inputs, run, pages=False)
    (unknown,) = [one for one in result.findings if one.code is Code.SCRIPT_VOICE_UNKNOWN]
    assert unknown.location.line == 9
    assert result.ok is False
    assert (result.cost.dollars, result.cost.characters) == (0.0, 0)
    assert said.count("The script names voices this project cannot read, so no take was planned or priced.") == 1


def test_a_timed_pause_is_judged_against_the_model_of_the_voice_that_reads_it(tmp_path: Path) -> None:
    """A guest on a model that drops a pause beside a narrator whose model renders one is judged turn by turn."""
    toml = on_model("eleven_multilingual_v2") + '\n[voices.narrator]\nid = "n"\n\n[voices.guest]\nmodel = "eleven_v3"\n'
    script = PAUSED_SCRIPT.replace("## 1. One\n", "## 1. One\n\n[voice guest]\n")
    inputs = a_project(tmp_path, toml=toml, script=script)
    result = check(inputs, a_run(tmp_path), pages=False)
    (dropped,) = [one for one in result.findings if one.code is Code.SCRIPT_PAUSE_DROPPED]
    assert dropped.location.section == 1
    assert dropped.message.startswith(
        "section 1 asks for a timed pause in a turn the voice guest reads, and 'elevenlabs' renders none on model "
        "'eleven_v3', so a voiced run would drop it."
    )


def test_check_says_which_voice_reads_how_many_turns_on_which_bill(tmp_path: Path) -> None:
    toml = PRICED + '\n[voices.narrator]\nid = "n"\n\n[voices.guest]\nprovider = "dtsp"\nid = "af_heart"\n'
    script = SCRIPT.replace("## 2. Two\n", "## 2. Two\n\n[voice guest]\n")
    inputs = a_project(tmp_path, toml=toml, script=script, cues=CUES)
    run = a_run(tmp_path)
    said = notes(run)
    check(inputs, run, pages=False)
    assert "The voice narrator reads 1 turn of 18 characters on elevenlabs, at $0.30 per 1,000 characters." in said
    assert "The voice guest reads 1 turn of 30 characters on dtsp, which bills nothing." in said


def test_every_program_the_project_names_is_listed_before_anything_runs(tmp_path: Path) -> None:
    """A reviewer reads what a project would start before a build starts it, whether or not it was recorded."""
    drawn = 'page = "deck/index.html"\nscene = "2"\n'
    assert TOML.count(drawn) == 1
    toml = TOML.replace(drawn, 'render_command = ["{python}", "render/draw", "{out}"]\n')
    inputs = a_project(tmp_path, toml=toml, cues=CUES)
    result = check(inputs, a_run(tmp_path), pages=False)
    [listed] = [found for found in result.findings if found.code is Code.PROGRAM_NAMED]
    assert listed.severity is Severity.WARNING
    assert 'section 2\'s render_command runs `["{python}", "render/draw", "{out}"]`' in listed.message
    assert listed.location is not None and listed.location.section == 2
    assert ".env" in listed.message
    assert "record.programs is refuse" in listed.message


@pytest.mark.browser
@pytest.mark.timeout(SPIN_CEILING_SECONDS)
def test_a_page_that_spins_its_main_thread_stops_the_check_with_a_tool_error(tmp_path: Path) -> None:
    """A page whose script never yields cannot be raced from inside, so its page is closed from outside."""
    inputs = a_spinning_project(tmp_path)
    with pytest.raises(ToolError, match="stopped answering"):
        check(inputs, a_run(tmp_path))


def test_a_script_whose_headings_do_not_ascend_is_the_error_narrate_would_refuse(tmp_path: Path) -> None:
    """`narrate` refuses headings that count backwards, so a check meets the same rule with the heading's line."""
    backwards = "# Demo\n\n## 2. Two\n\nSecond section speaks as well.\n\n## 1. One\n\nHello there again.\n"
    inputs = a_project(tmp_path, cues=CUES, script=backwards)
    result = check(inputs, a_run(tmp_path), pages=False)
    found = [one for one in result.findings if "do not ascend" in one.message]
    assert len(found) == 1
    assert found[0].code is Code.SCRIPT_UNFINISHED
    assert found[0].severity is Severity.ERROR
    assert found[0].location.line == 7
    assert "## 1. One" in found[0].message
