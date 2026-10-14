"""The take state: what the disk holds for each spoken section, and what one narrate run does about it.

Every test writes a project, real take pairs under the digests the take's inputs give, and a take
index, and asks `take_states` alone. Nothing here runs narrate, builds a voice or opens a run.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from decktalk.artifacts import (
    AlignedWords,
    AlignmentInputs,
    EstimatedWords,
    ProviderWords,
    Take,
    TimedBy,
    Words,
    is_placeholder,
    take_file,
)
from decktalk.errors import InputError
from decktalk.inputs import Inputs
from decktalk.inputs.script import ScriptSection, estimated_words, spoken_words
from decktalk.results import TakeOutcome, TakeState, Word
from decktalk.speech import canonical_text
from decktalk.stages.cost import cost_of
from decktalk.stages.narrate.plan import placeholder_inputs, take_inputs
from decktalk.stages.narrate.state import (
    CHANGED,
    FILES_GONE,
    HELD,
    NO_VOICED_TAKE,
    REPLACED,
    SHARED,
    UNINDEXED,
    NarratePlan,
    TakeStates,
    take_states,
    without_a_voice,
)
from support.fakes import NARRATOR_ID
from support.projects import load_project
from support.takes import (
    TAIL_TAKE_SOUND_END,
    TAKE_SUFFIX,
    a_take,
    damage_take,
    hold_take,
    tail_take_words,
    write_takes,
)

from .conftest import DIALOGUE, ENVIRON, FREE_TOML, SCRIPT, SOUND_END, TOML, VOICE_ID, WITH_A_GUEST

MOVED = re.compile(r"the takes directory \S+ holds none of the (\d+) takes? this project played before")
"""The moved clause, which names the folder and a count, so it is matched rather than compared."""

NAMELESS = {key: value for key, value in ENVIRON.items() if key != NARRATOR_ID}
"""The machine with the credential and no voice named."""

SHARED_SCRIPT = SCRIPT.replace("Every picture waited for its word.", "A bowl. [beat] A ball.")
"""Sections 1 and 3 say the same words, which come to one take."""

INSERTED = SCRIPT.replace("## 2. Middle", "## 2. New\n\nA new thought arrives.\n\n## 3. Middle").replace(
    "## 3. Close", "## 4. Close"
)
"""Section 2 inserted, so the old 2 and 3 are 3 and 4 now."""

UNDECLARED = "## 1. Open\n\nA bowl.\n\n## 9. Nowhere\n\nNo section plays this.\n"
"""A script naming a section `decktalk.toml` does not declare, which does not parse."""

FOUR = TOML + '\n[[section]]\nnumber = 4\npage = "deck/index.html"\nscene = "4"\n'
"""The project with a fourth section, which the inserted script needs."""


def section_of(inputs: Inputs, number: int) -> ScriptSection:
    return next(section for section in inputs.spoken() if section.number == number)


def digest_of(inputs: Inputs, number: int, *, voice: str = VOICE_ID) -> str:
    """The voiced take this section's current text names under `voice`."""
    section = section_of(inputs, number)
    return take_inputs(section, replace(inputs.voices["narrator"], id=voice)).digest


def stand_in_of(inputs: Inputs, number: int) -> str:
    return placeholder_inputs(inputs, section_of(inputs, number)).digest


def row(inputs: Inputs, number: int, digest: str) -> Take:
    """The index row of this section playing this take, as narrate writes it."""
    return a_take(number, digest=digest, voiced=not is_placeholder(digest), spoken=section_of(inputs, number).spoken)


def bought(inputs: Inputs, *numbers: int) -> Inputs:
    """Hold and index a voiced take of each of these sections, or of every one when none is named."""
    rows = []
    for number in numbers or tuple(section.number for section in inputs.spoken()):
        digest = digest_of(inputs, number)
        hold_take(inputs, digest)
        rows.append(row(inputs, number, digest))
    write_takes(inputs, *rows)
    return inputs


def stood_in(inputs: Inputs, *numbers: int) -> Inputs:
    """Hold and index a placeholder of each of these sections, as a run that buys nothing leaves it."""
    rows = []
    for number in numbers or tuple(section.number for section in inputs.spoken()):
        digest = stand_in_of(inputs, number)
        hold_take(inputs, digest)
        rows.append(row(inputs, number, digest))
    write_takes(inputs, *rows)
    return inputs


def reload(inputs: Inputs, environ: dict[str, str] = ENVIRON, **machine: object) -> Inputs:
    return Inputs.load(inputs.root, environ=environ, machine=machine or None)


def rewritten(inputs: Inputs, script: str, *, toml: str = TOML, environ: dict[str, str] = ENVIRON) -> Inputs:
    return load_project(inputs.root, toml, script=script, environ=environ)


def outcomes(states: TakeStates, *, spend: bool, force: bool = False) -> list[TakeOutcome]:
    return [plan.outcome for plan in states.plan(spend=spend, force=force).takes]


# ---- the states ---------------------------------------------------------------------------------


def test_a_voiced_take_of_the_current_inputs_the_index_plays_is_voiced_and_settled(inputs: Inputs) -> None:
    states = take_states(bought(inputs))
    assert [state.state for state in states.values()] == [TakeState.VOICED] * 3
    assert states[1].reason == HELD
    assert states[1].turns[0].digest == digest_of(inputs, 1)
    assert states.settled is True


def test_the_take_state_names_takes_by_the_voice_in_force(inputs: Inputs) -> None:
    """The state reads the voice the project is read in, so another voice in force names other takes."""
    voices = {"narrator": replace(inputs.voices["narrator"], id="someone-else")}
    other = replace(inputs, voices=voices)
    named = [state.turns[0].digest for state in take_states(other).values()]
    assert named == [digest_of(inputs, number, voice="someone-else") for number in (1, 2, 3)]
    assert named != [state.turns[0].digest for state in take_states(inputs).values()]


def test_a_held_voiced_take_the_index_does_not_play_is_voiced_and_not_settled(inputs: Inputs) -> None:
    """The voice was switched back after a run that bought nothing indexed another voice's placeholders."""
    bought(inputs)
    stood_in(inputs)
    states = take_states(inputs)
    assert states[1].state is TakeState.VOICED
    assert states[1].reason == UNINDEXED
    assert states.settled is False
    assert outcomes(states, spend=False) == [TakeOutcome.KEPT] * 3


def test_a_voice_change_with_the_old_take_held_is_stale(inputs: Inputs) -> None:
    bought(inputs)
    revoiced = reload(inputs, {**ENVIRON, NARRATOR_ID: "another-voice"})
    states = take_states(revoiced)
    assert states[1].state is TakeState.STALE
    assert states[1].reason == CHANGED
    assert states.settled is False
    stood_in(revoiced)
    after = take_states(revoiced)
    assert after[1].state is TakeState.PLACEHOLDER, "once a run that buys nothing indexes its placeholder"
    assert after.settled is True


def test_a_voice_change_with_the_old_take_gone_is_missing_its_files(inputs: Inputs) -> None:
    bought(inputs)
    gone = digest_of(inputs, 1)
    (inputs.workspace.takes / take_file(gone, TAKE_SUFFIX)).unlink()
    states = take_states(reload(inputs, {**ENVIRON, NARRATOR_ID: "another-voice"}))
    assert states[1].state is TakeState.MISSING
    assert states[1].reason == FILES_GONE
    assert states[1].takes_dir_gone is False, "the takes directory still holds the other sections' takes"
    assert states.settled is False


def test_an_edited_section_with_its_old_take_held_is_stale(inputs: Inputs) -> None:
    bought(inputs)
    states = take_states(rewritten(inputs, SCRIPT.replace("It steps down the bowl.", "It rolls down the bowl.")))
    assert states[2].state is TakeState.STALE
    assert states[2].reason == CHANGED
    assert states[1].state is TakeState.VOICED
    assert states.settled is False


@pytest.mark.parametrize("selected", [None, [2]], ids=["every-section", "section-2"])
def test_an_inserted_section_has_no_voiced_take_whatever_the_selection(
    inputs: Inputs, selected: list[int] | None
) -> None:
    bought(inputs)
    moved = rewritten(inputs, INSERTED, toml=FOUR)
    sections = None if selected is None else [section_of(moved, number) for number in selected]
    states = take_states(moved, sections)
    assert states[2].state is TakeState.MISSING
    assert states[2].reason == NO_VOICED_TAKE
    if selected is None:
        assert states[3].state is TakeState.VOICED
        assert states[3].reason == UNINDEXED, "its take is held under the row of the number it had"


def test_two_sections_with_the_same_held_words_are_both_voiced(make_inputs: Callable[..., Inputs]) -> None:
    doubled = bought(make_inputs(script=SHARED_SCRIPT))
    states = take_states(doubled)
    assert states[1].state is states[3].state is TakeState.VOICED
    assert states[1].reason == states[3].reason == HELD
    assert states[1].turns[0].digest == states[3].turns[0].digest
    for spend in (False, True):
        plan = states.plan(spend=spend)
        assert [p.outcome for p in plan.takes] == [TakeOutcome.KEPT] * 3
        assert SHARED not in {p.reason for p in plan.takes}
        assert plan.cost.dollars == 0
    assert states.settled is True


def test_a_placeholder_under_a_voice_that_bills_is_settled(inputs: Inputs) -> None:
    states = take_states(stood_in(inputs))
    assert states[1].state is TakeState.PLACEHOLDER
    assert states[1].reason == NO_VOICED_TAKE
    assert states.settled is True


def test_a_placeholder_under_a_named_free_voice_is_not_settled(make_inputs: Callable[..., Inputs]) -> None:
    inputs = make_inputs(toml=FREE_TOML)
    states = take_states(stood_in(inputs))
    assert states[1].state is TakeState.PLACEHOLDER
    assert states.settled is False, "a run that buys nothing voices it for nothing"


def test_a_placeholder_under_a_free_voice_nobody_named_is_settled(make_inputs: Callable[..., Inputs]) -> None:
    inputs = make_inputs(toml=FREE_TOML)
    states = take_states(stood_in(reload(inputs, NAMELESS)))
    assert states[1].state is TakeState.PLACEHOLDER
    assert states[1].turns[0].digest is None
    assert states.settled is True


def test_a_section_with_nothing_held_is_missing(inputs: Inputs) -> None:
    states = take_states(inputs)
    assert {state.state for state in states.values()} == {TakeState.MISSING}
    assert {state.reason for state in states.values()} == {NO_VOICED_TAKE}
    assert states.settled is False


def test_with_no_voice_named_a_held_take_of_this_text_is_unchecked_and_kept(inputs: Inputs) -> None:
    bought(inputs)
    states = take_states(reload(inputs, NAMELESS))
    assert states[1].state is TakeState.UNCHECKED
    assert states[1].reason == without_a_voice(inputs.voices["narrator"])
    assert NARRATOR_ID in states[1].reason
    assert states[1].turns[0].digest is None
    plan = states.plan(spend=False)
    assert [p.outcome for p in plan.takes] == [TakeOutcome.KEPT] * 3
    assert [p.digest for p in plan.takes] == [digest_of(inputs, number) for number in (1, 2, 3)]
    assert states.settled is True


def test_with_no_voice_named_an_edited_section_with_its_old_take_held_is_stale(inputs: Inputs) -> None:
    bought(inputs)
    edited = SCRIPT.replace("It steps down the bowl.", "It rolls down the bowl.")
    states = take_states(rewritten(inputs, edited, environ=NAMELESS))
    assert states[2].state is TakeState.STALE
    assert states[2].reason == CHANGED
    assert states.settled is False


def test_with_no_voice_named_a_take_whose_files_are_gone_is_missing(inputs: Inputs) -> None:
    bought(inputs)
    (inputs.workspace.takes / take_file(digest_of(inputs, 1), TAKE_SUFFIX)).unlink()
    states = take_states(reload(inputs, NAMELESS))
    assert states[1].state is TakeState.MISSING
    assert states[1].reason == FILES_GONE
    assert states.settled is False


def test_with_no_voice_named_an_inserted_section_has_no_voiced_take(inputs: Inputs) -> None:
    bought(inputs)
    states = take_states(rewritten(inputs, INSERTED, toml=FOUR, environ=NAMELESS))
    assert states[2].state is TakeState.MISSING
    assert states[2].reason == NO_VOICED_TAKE
    assert states[3].state is TakeState.UNCHECKED


def test_with_no_voice_named_a_renumbered_take_is_kept_under_the_row_it_matched(inputs: Inputs) -> None:
    """The old sections 2 and 3 are unindexed at 3 and 4, and a run that buys nothing keeps their takes there."""
    old = {number: digest_of(bought(inputs), number) for number in (1, 2, 3)}
    moved = rewritten(inputs, INSERTED, toml=FOUR, environ=NAMELESS)
    states = take_states(moved)
    assert states[4].state is TakeState.UNCHECKED
    assert states.settled is False
    plan = states.plan(spend=False)
    assert [p.outcome for p in plan.takes] == [
        TakeOutcome.KEPT,
        TakeOutcome.PLACEHOLDER,
        TakeOutcome.KEPT,
        TakeOutcome.KEPT,
    ]
    assert [p.digest for p in plan.takes] == [old[1], stand_in_of(moved, 2), old[2], old[3]]
    hold_take(moved, stand_in_of(moved, 2))
    write_takes(moved, *(row(moved, p.section.number, p.digest) for p in plan.takes if p.digest is not None))
    assert take_states(reload(moved, NAMELESS)).settled is True, "the run's own index reads settled"


def test_a_takes_directory_holding_none_of_the_index_s_takes_says_it_moved(inputs: Inputs) -> None:
    bought(inputs)
    shutil.move(inputs.workspace.takes, inputs.root / "takes-old")
    states = take_states(reload(inputs))
    for state in states.values():
        assert state.state is TakeState.MISSING
        assert state.takes_dir_gone is True
        found = MOVED.search(state.reason)
        assert found is not None, state.reason
        assert "3 takes" in state.reason
    assert states.settled is False


def test_a_placeholder_section_in_a_moved_takes_directory_keeps_its_own_reason(inputs: Inputs) -> None:
    bought(inputs, 1, 2)
    hold_take(inputs, stand_in_of(inputs, 3))
    write_takes(
        inputs,
        row(inputs, 1, digest_of(inputs, 1)),
        row(inputs, 2, digest_of(inputs, 2)),
        row(inputs, 3, stand_in_of(inputs, 3)),
    )
    shutil.move(inputs.workspace.takes, inputs.root / "takes-old")
    states = take_states(reload(inputs))
    assert states[1].takes_dir_gone is True
    assert states[3].state is TakeState.PLACEHOLDER
    assert states[3].reason == NO_VOICED_TAKE
    assert states[3].takes_dir_gone is False


def test_a_store_holding_every_take_is_not_a_moved_takes_directory(tmp_path: Path, inputs: Inputs) -> None:
    store = tmp_path / "store"
    bought(inputs)
    shutil.copytree(inputs.workspace.takes, store)
    shutil.move(inputs.workspace.takes, inputs.root / "takes-old")
    states = take_states(reload(inputs, narration={"store_dir": str(store)}))
    assert {state.state for state in states.values()} == {TakeState.VOICED}
    assert not any(state.takes_dir_gone for state in states.values())
    assert states.settled is True


def test_a_store_holding_some_takes_counts_only_the_takes_no_place_holds(tmp_path: Path, inputs: Inputs) -> None:
    store = tmp_path / "store"
    bought(inputs)
    shutil.copytree(inputs.workspace.takes, store)
    for path in store.glob(f"{digest_of(inputs, 3)}*"):
        path.unlink()
    shutil.move(inputs.workspace.takes, inputs.root / "takes-old")
    states = take_states(reload(inputs, narration={"store_dir": str(store)}))
    assert [states[number].state for number in (1, 2, 3)] == [TakeState.VOICED, TakeState.VOICED, TakeState.MISSING]
    assert states[3].takes_dir_gone is True
    found = MOVED.search(states[3].reason)
    assert found is not None and found.group(1) == "1"


def test_a_selected_take_damaged_everywhere_is_refused(inputs: Inputs) -> None:
    bought(inputs)
    damage_take(inputs, digest_of(inputs, 1))
    with pytest.raises(InputError) as refused:
        take_states(inputs)
    assert refused.value.location is not None and refused.value.location.section == 1
    assert refused.value.hint is not None and "--replace-voiced --spend" in refused.value.hint


def test_a_take_damaged_outside_the_selection_is_not_refused(inputs: Inputs) -> None:
    bought(inputs)
    damage_take(inputs, digest_of(inputs, 3))
    states = take_states(inputs, [section_of(inputs, 1)])
    assert list(states) == [1]
    assert states[1].state is TakeState.VOICED
    assert outcomes(states, spend=False) == [TakeOutcome.KEPT]


def test_a_damaged_take_under_replace_voiced_is_missing_and_planned_as_replaced(inputs: Inputs) -> None:
    bought(inputs)
    damage_take(inputs, digest_of(inputs, 1))
    states = take_states(inputs, replace_voiced=True)
    assert states[1].state is TakeState.MISSING
    assert states[1].reason == REPLACED
    voiced = states.plan(spend=True).takes[0]
    assert (voiced.outcome, voiced.reason) == (TakeOutcome.VOICED, REPLACED)
    stand_in = states.plan(spend=False).takes[0]
    assert (stand_in.outcome, stand_in.reason) == (TakeOutcome.PLACEHOLDER, REPLACED)


def test_with_no_voice_named_a_same_text_take_damaged_everywhere_is_refused(inputs: Inputs) -> None:
    """The refusal names the section whose read it stops, and asks for the voice before a purchase."""
    old = digest_of(bought(inputs), 2)
    moved = rewritten(inputs, INSERTED, toml=FOUR, environ=NAMELESS)
    damage_take(moved, old)
    with pytest.raises(InputError) as refused:
        take_states(moved, [section_of(moved, 3)])
    assert refused.value.location is not None and refused.value.location.section == 3
    assert refused.value.hint is not None and refused.value.hint.startswith("Name the voice")


def test_a_clip_section_has_no_take_state(make_inputs: Callable[..., Inputs]) -> None:
    toml = FOUR.replace('number = 3\npage = "deck/index.html"\nscene = "3"', 'number = 3\nclip = "media/c.mp4"')
    script = SCRIPT + "\n## 4. After\n\nThe end comes softly.\n"
    project = make_inputs(toml=toml, script=script)
    states = take_states(project)
    assert list(states) == [1, 2, 4]
    request = states.plan(spend=True).takes[2].request
    assert request is not None and request.previous_text == section_of(project, 2).spoken


def test_a_script_that_does_not_parse_raises_from_take_states(make_inputs: Callable[..., Inputs]) -> None:
    project = make_inputs(script=UNDECLARED)
    with pytest.raises(InputError):
        take_states(project)


# ---- the plan -----------------------------------------------------------------------------------


def test_force_never_touches_a_voiced_take(inputs: Inputs) -> None:
    bought(inputs, 1)
    hold_take(inputs, stand_in_of(inputs, 2))
    states = take_states(inputs)
    assert outcomes(states, spend=False, force=True) == [
        TakeOutcome.KEPT,
        TakeOutcome.PLACEHOLDER,
        TakeOutcome.PLACEHOLDER,
    ]
    assert outcomes(states, spend=False) == [TakeOutcome.KEPT, TakeOutcome.KEPT, TakeOutcome.PLACEHOLDER]


def test_force_is_ignored_by_a_run_that_voices(inputs: Inputs) -> None:
    states = take_states(bought(inputs, 1))
    assert states.plan(spend=True, force=True) == states.plan(spend=True)


def test_a_forced_placeholder_says_its_state_s_reason(inputs: Inputs) -> None:
    states = take_states(stood_in(inputs))
    forced = states.plan(spend=False, force=True).takes
    assert [p.outcome for p in forced] == [TakeOutcome.PLACEHOLDER] * 3
    assert {p.reason for p in forced} == {NO_VOICED_TAKE}


def test_shared_words_plan_one_voiced_take_and_price_it_once(make_inputs: Callable[..., Inputs]) -> None:
    doubled = make_inputs(script=SHARED_SCRIPT)
    states = take_states(doubled)
    plan = states.plan(spend=True)
    assert [p.outcome for p in plan.takes] == [TakeOutcome.VOICED, TakeOutcome.VOICED, TakeOutcome.KEPT]
    assert plan.takes[2].reason == SHARED
    assert plan.takes[2].digest == plan.takes[0].digest
    assert plan.cost.sections == (1, 2)
    assert states.settled is False


def test_a_section_sharing_a_placeholder_says_its_own_reason(make_inputs: Callable[..., Inputs]) -> None:
    """Nothing voices either section in a run that buys nothing, so neither says another section voices it."""
    states = take_states(make_inputs(script=SHARED_SCRIPT))
    plan = states.plan(spend=False)
    assert [p.outcome for p in plan.takes] == [TakeOutcome.PLACEHOLDER, TakeOutcome.PLACEHOLDER, TakeOutcome.KEPT]
    assert plan.takes[2].digest == plan.takes[0].digest
    assert [p.reason for p in plan.takes] == [NO_VOICED_TAKE] * 3


def test_with_no_voice_named_shared_words_are_priced_once(make_inputs: Callable[..., Inputs]) -> None:
    project = make_inputs(script=SHARED_SCRIPT)
    named = take_states(project).plan(spend=True)
    plan = take_states(reload(project, NAMELESS)).plan(spend=True)
    assert [p.outcome for p in plan.takes] == [TakeOutcome.VOICED, TakeOutcome.VOICED, TakeOutcome.KEPT]
    assert plan.takes[2].reason == SHARED
    assert plan.cost.sections == (1, 2)
    assert plan.cost.dollars == named.cost.dollars > 0


def test_with_no_voice_named_held_shared_words_count_into_the_ceiling_once(make_inputs: Callable[..., Inputs]) -> None:
    doubled = bought(make_inputs(script=SHARED_SCRIPT))
    plan = take_states(reload(doubled, NAMELESS)).plan(spend=True)
    named = take_states(make_inputs(script=SHARED_SCRIPT, name="fresh")).plan(spend=True)
    assert plan.cost.dollars == 0
    assert plan.cost.ceiling_dollars == named.cost.dollars


def test_a_named_free_voice_voices_without_spend_and_an_unnamed_one_does_not(
    make_inputs: Callable[..., Inputs],
) -> None:
    inputs = make_inputs(toml=FREE_TOML)
    assert take_states(inputs).plan(spend=False).voiced is True
    assert take_states(reload(inputs, NAMELESS)).plan(spend=False).voiced is False


def test_a_spend_run_that_keeps_every_take_is_still_voiced(inputs: Inputs) -> None:
    plan = take_states(bought(inputs)).plan(spend=True)
    assert plan.voiced is True
    assert [p.outcome for p in plan.takes] == [TakeOutcome.KEPT] * 3


def test_with_no_voice_named_a_voiced_plan_has_no_digest(inputs: Inputs) -> None:
    plan = take_states(reload(inputs, NAMELESS)).plan(spend=True)
    assert [p.outcome for p in plan.takes] == [TakeOutcome.VOICED] * 3
    assert {p.digest for p in plan.takes} == {None}


def test_replace_voiced_without_spend_plays_a_placeholder_that_says_it_was_replaced(inputs: Inputs) -> None:
    states = take_states(bought(inputs), [section_of(inputs, 2)], replace_voiced=True)
    assert states[2].state is TakeState.VOICED, "the take stays on disk, so its state is still voiced"
    (plan,) = states.plan(spend=False).takes
    assert (plan.outcome, plan.reason) == (TakeOutcome.PLACEHOLDER, REPLACED)
    assert plan.digest == stand_in_of(inputs, 2)


def test_replace_voiced_with_spend_buys_a_held_take_again(inputs: Inputs) -> None:
    states = take_states(bought(inputs), [section_of(inputs, 2)], replace_voiced=True)
    plan = states.plan(spend=True)
    assert [(p.outcome, p.reason) for p in plan.takes] == [(TakeOutcome.VOICED, REPLACED)]
    assert plan.takes[0].digest == digest_of(inputs, 2)
    assert plan.cost.sections == (2,)


def test_a_take_bought_again_re_places_the_unselected_sections_that_play_it(make_inputs: Callable[..., Inputs]) -> None:
    """Section 1 plays the take section 3 replaces, so its row is placed again on the new bytes."""
    doubled = bought(make_inputs(script=SHARED_SCRIPT))
    states = take_states(doubled, [section_of(doubled, 3)], replace_voiced=True)
    plan = states.plan(spend=True)
    assert [(p.section.number, p.outcome, p.reason) for p in plan.takes] == [
        (3, TakeOutcome.VOICED, REPLACED),
        (1, TakeOutcome.KEPT, SHARED),
    ]
    assert plan.takes[1].digest == plan.takes[0].digest
    assert plan.cost.sections == (3,)


# ---- the price ----------------------------------------------------------------------------------


def test_with_no_voice_named_a_held_take_of_this_text_is_priced_into_the_ceiling_alone(inputs: Inputs) -> None:
    bought(inputs, 1)
    spend = take_states(reload(inputs, NAMELESS)).plan(spend=True).cost
    sections = {section.number: section for section in inputs.spoken()}
    assert spend.characters == sum(len(canonical_text(sections[number].pieces)) for number in (2, 3))
    assert spend.sections == (2, 3, 1)
    assert spend.ceiling_dollars >= spend.dollars > 0


def test_with_no_voice_named_an_edited_section_is_priced_as_certain(inputs: Inputs) -> None:
    bought(inputs)
    edited = rewritten(inputs, SCRIPT.replace("It steps down the bowl.", "It rolls down the bowl."), environ=NAMELESS)
    spend = take_states(edited).plan(spend=True).cost
    assert spend.sections == (2, 1, 3)
    assert spend.characters == len(canonical_text(section_of(edited, 2).pieces))
    assert spend.dollars > 0


def test_certain_sections_are_listed_before_uncertain_ones(inputs: Inputs) -> None:
    bought(inputs, 1, 3)
    spend = take_states(reload(inputs, NAMELESS)).plan(spend=True).cost
    assert spend.sections == (2, 1, 3)


def test_the_price_builds_no_provider(make_inputs: Callable[..., Inputs]) -> None:
    """A price is over names and text, none of them secret, so a machine with no key prices exactly."""
    project = Inputs.load(make_inputs().root, environ={NARRATOR_ID: VOICE_ID})
    plan = take_states(project).plan(spend=True)
    assert all(p.digest is not None for p in plan.takes)
    assert plan.cost.sections == (1, 2, 3)


def test_an_empty_selection_reads_nothing_and_prices_nothing(make_inputs: Callable[..., Inputs]) -> None:
    project = make_inputs(script=UNDECLARED)
    states = take_states(project, ())
    assert len(states) == 0
    assert states.settled is True
    assert states.plan(spend=True).cost == cost_of(project)


# ---- the words ----------------------------------------------------------------------------------

SAID = (Word(word="A", start=0.1, end=0.3), Word(word="bowl", start=0.35, end=0.7))
"""The words a provider sent back with section 1's take, on the take's own clock."""


def test_planned_words_of_a_voiced_take_are_its_provider_words_from_the_section_start(inputs: Inputs) -> None:
    hold_take(inputs, digest_of(inputs, 1), words=SAID)
    write_takes(inputs, row(inputs, 1, digest_of(inputs, 1)))
    words = take_states(inputs).planned_words(1)
    assert type(words) is Words
    lead = inputs.lead_seconds(1)
    assert words.words == tuple(replace_start(word, lead) for word in SAID)


def test_planned_words_of_a_held_take_end_where_its_sound_ends(inputs: Inputs) -> None:
    """The real take's provider ran its last word into the trailing silence, which the join never plays."""
    digest = digest_of(inputs, 1)
    hold_take(inputs, digest, words=tail_take_words().words)
    write_takes(inputs, row(inputs, 1, digest).model_copy(update={"sound_end_seconds": TAIL_TAKE_SOUND_END}))
    words = take_states(inputs).planned_words(1)
    assert words.words[-1].end == round(inputs.lead_seconds(1) + TAIL_TAKE_SOUND_END, 3)


def replace_start(word: Word, lead: float) -> Word:
    return Word(word=word.word, start=round(word.start + lead, 3), end=round(word.end + lead, 3))


def test_planned_words_of_an_unchecked_take_are_the_matched_take_s_words(inputs: Inputs) -> None:
    hold_take(inputs, digest_of(inputs, 1), words=SAID)
    write_takes(inputs, row(inputs, 1, digest_of(inputs, 1)))
    states = take_states(reload(inputs, NAMELESS))
    assert states[1].state is TakeState.UNCHECKED
    words = states.planned_words(1)
    assert type(words) is Words
    assert [word.word for word in words.words] == ["A", "bowl"]


def test_planned_words_of_a_section_with_no_voiced_take_are_estimated(inputs: Inputs) -> None:
    words = take_states(stood_in(inputs)).planned_words(2)
    assert type(words) is EstimatedWords
    assert [word.word for word in words.words] == ["It", "steps", "down", "the", "bowl"]
    assert words.words[0].start == inputs.lead_seconds(2)


# ---- what it reads ------------------------------------------------------------------------------


def test_the_take_index_is_read_once_per_reading(inputs: Inputs, monkeypatch: pytest.MonkeyPatch) -> None:
    bought(inputs, 1)
    reads: list[None] = []
    real = Inputs.takes

    def counted(self: Inputs) -> object:
        reads.append(None)
        return real(self)

    monkeypatch.setattr(Inputs, "takes", counted)
    states = take_states(inputs)
    states.plan(spend=True)
    states.plan(spend=False)
    assert states.settled is False
    states.planned_words(1)
    states.planned_words(2)
    assert len(reads) == 1


def held_without_times(inputs: Inputs, number: int) -> ProviderWords:
    """Hold a voiced take of this section whose voice sent no word times, and give back its words file."""
    digest = digest_of(inputs, number)
    hold_take(inputs, digest)
    said = inputs.take_words(digest)
    assert isinstance(said, ProviderWords) and said.timed is False
    return said


@pytest.mark.usefixtures("quiet_sound_end")
def test_planned_words_of_a_take_with_no_words_are_its_kept_alignment_with_no_take_index(inputs: Inputs) -> None:
    """A fresh clone has no take index, and still plays the alignment committed beside the take."""
    said = held_without_times(inputs, 1)
    keyed = AlignmentInputs(
        audio=said.audio.blake3, words=spoken_words(section_of(inputs, 1).spoken), aligner="a", revision="1",
        model_digest="m",
    )  # fmt: skip
    spoken = section_of(inputs, 1).spoken
    aligned = AlignedWords(words=tuple(estimated_words(spoken, SOUND_END)), inputs=keyed, generation=1)
    inputs.workspace.aligned_dir.mkdir(parents=True, exist_ok=True)
    aligned.write(inputs.workspace.aligned_words(keyed.key))
    words = take_states(inputs).planned_words(1)
    assert type(words) is Words
    assert words.words == aligned.shifted(inputs.lead_seconds(1))


@pytest.mark.usefixtures("quiet_sound_end")
def test_planned_words_of_a_take_with_no_words_and_none_kept_are_estimated_over_its_sound(inputs: Inputs) -> None:
    """The row's sound end is read when the row names the take, and the take is measured only when none does."""
    held_without_times(inputs, 1)
    spoken = section_of(inputs, 1).spoken
    lead = inputs.lead_seconds(1)
    measured = take_states(inputs).planned_words(1)
    assert type(measured) is EstimatedWords
    assert measured.words == EstimatedWords(words=tuple(estimated_words(spoken, 0.8))).shifted(lead)
    write_takes(inputs, a_take(1, digest=digest_of(inputs, 1), seconds=2.0, spoken=spoken, timed_by=TimedBy.ESTIMATED))
    indexed = take_states(reload(inputs)).planned_words(1)
    assert indexed.words == EstimatedWords(words=tuple(estimated_words(spoken, 2.0))).shifted(lead)


def test_settled_judges_takes_and_not_their_timing(inputs: Inputs) -> None:
    """A take no aligner timed is still the take the script names, so nothing about it is for narrate to buy."""
    for number in (1, 2, 3):
        held_without_times(inputs, number)
    write_takes(
        inputs,
        *(
            a_take(n, digest=digest_of(inputs, n), spoken=section_of(inputs, n).spoken, timed_by=TimedBy.ESTIMATED)
            for n in (1, 2, 3)
        ),
    )
    assert take_states(reload(inputs)).settled is True


# ---- voices -------------------------------------------------------------------------------------

GUEST = TOML.replace(
    '[voices.narrator]\nprovider = "elevenlabs"\n', '[voices.narrator]\nprovider = "elevenlabs"\n\n[voices.guest]\n'
)
"""The project with a second ElevenLabs voice, the guest, whose id nobody names."""

ECHO = "## 1. Open\n\nA bowl.\n\n## 2. Middle\n\n[voice guest]\n\nA bowl.\n\n## 3. Close\n\nDone.\n"
"""The narrator and the guest each saying the same words, in sections 1 and 2."""


def test_an_unnamed_voices_section_is_matched_by_its_voice_and_its_text_never_by_text_alone(
    make_inputs: Callable[..., Inputs],
) -> None:
    """The narrator's take of the words the guest says is not the guest's, so an unnamed guest never plays it."""
    inputs = make_inputs(toml=GUEST, script=ECHO)
    narrator = digest_of(inputs, 1)
    hold_take(inputs, narrator)
    write_takes(inputs, row(inputs, 1, narrator))
    states = take_states(inputs)
    assert states[1].state is TakeState.VOICED
    assert (states[2].state, states[2].reason) == (TakeState.MISSING, "it has no voiced take yet")
    guest = "00000000000beef0"
    hold_take(inputs, guest)
    write_takes(inputs, row(inputs, 1, narrator), a_take(2, digest=guest, spoken="A bowl.", voice="guest"))
    again = take_states(inputs)
    assert again[2].state is TakeState.UNCHECKED
    assert again[2].reason == without_a_voice(inputs.voices["guest"])
    assert again.plan(spend=False).takes[1].digest == guest


# ---- turns ---------------------------------------------------------------------------------------

BOTH_PAID = WITH_A_GUEST.replace('provider = "dtsp"\nid = "af_heart"', 'id = "guest-voice"')
"""The dialogue project with an ElevenLabs guest of its own id beside the narrator."""


def hold_turns(inputs: Inputs, *, skip: tuple[str, ...] = ()) -> Inputs:
    """Hold and index a voiced take of every turn whose voice is not in `skip`, as a paid run leaves them."""
    rows = []
    for section in inputs.spoken():
        for turn in section.turns:
            voice = inputs.voice_of(turn)
            if voice.name in skip:
                continue
            digest = take_inputs(turn, voice).digest
            hold_take(inputs, digest, words=(Word(word="said", start=0.0, end=0.4),))
            rows.append(a_take(section.number, turn=turn.number, voice=voice.name, digest=digest, spoken=turn.spoken))
    # The index counts each section's turns from one, so a turn left out ends its section's rows there.
    indexed = [
        row
        for row in rows
        if all((row.section, earlier) in {(r.section, r.turn) for r in rows} for earlier in range(1, row.turn))
    ]
    write_takes(inputs, *indexed)
    return inputs


def test_the_section_state_is_the_worst_of_its_turns(make_inputs: Callable[..., Inputs]) -> None:
    inputs = hold_turns(make_inputs(toml=BOTH_PAID, script=DIALOGUE), skip=("guest",))
    states = take_states(inputs)
    assert [turn.state for turn in states[2].turns] == [TakeState.VOICED, TakeState.MISSING, TakeState.VOICED]
    assert states[2].state is TakeState.MISSING
    assert states[2].reason == f"{NO_VOICED_TAKE}, in turn 2, which guest reads"
    assert states[1].state is TakeState.VOICED


def test_a_section_of_one_turn_keeps_todays_reason_word_for_word(inputs: Inputs) -> None:
    states = take_states(inputs)
    assert states[1].reason == NO_VOICED_TAKE
    assert [turn.reason for turn in states[1].turns] == [NO_VOICED_TAKE]


@pytest.mark.usefixtures("quiet_sound_end")
def test_a_placeholder_turn_among_voiced_turns_makes_its_section_estimated(make_inputs: Callable[..., Inputs]) -> None:
    inputs = hold_turns(make_inputs(toml=BOTH_PAID, script=DIALOGUE), skip=("guest",))
    states = take_states(inputs)
    assert states.planned_words(2).estimated
    assert not states.planned_words(1).estimated


BEFORE = SCRIPT.replace("It steps down the bowl.\n", "It steps down the bowl.\n\nThen it stops.\n")
"""Section 2 read whole by the narrator, before a guest is put in the middle of it."""

AFTER = SCRIPT.replace(
    "It steps down the bowl.\n",
    "[voice narrator]\n\nIt steps down the bowl.\n\n[voice guest]\n\nAnd it rolls.\n\n"
    "[voice narrator]\n\nThen it stops.\n",
)
"""The same section with a guest's turn put in the middle."""


def test_inserting_a_turn_voices_again_only_the_turns_whose_text_changed(make_inputs: Callable[..., Inputs]) -> None:
    """The narrator's one take of the section now reads as two turns of new text, and nothing else moves."""
    before = hold_turns(make_inputs(toml=BOTH_PAID, script=BEFORE))
    after = make_inputs(toml=BOTH_PAID, script=AFTER)
    assert after.root == before.root
    states = take_states(after)
    assert [state.state for state in states.values()] == [TakeState.VOICED, TakeState.MISSING, TakeState.VOICED]
    assert [(turn.state, turn.reason) for turn in states[2].turns] == [
        (TakeState.STALE, CHANGED),
        (TakeState.MISSING, NO_VOICED_TAKE),
        (TakeState.STALE, CHANGED),
    ]


def test_two_voices_saying_the_same_words_never_share_a_take(make_inputs: Callable[..., Inputs]) -> None:
    echo = SCRIPT.replace("It steps down the bowl.\n", "[voice narrator]\n\nYes.\n\n[voice guest]\n\nYes.\n")
    plan = take_states(make_inputs(toml=BOTH_PAID, script=echo)).plan(spend=True)
    turns = [take for take in plan.takes if take.section.number == 2]
    assert [take.outcome for take in turns] == [TakeOutcome.VOICED, TakeOutcome.VOICED]
    assert turns[0].digest != turns[1].digest


def test_two_turns_with_one_digest_are_bought_and_priced_once(make_inputs: Callable[..., Inputs]) -> None:
    again = SCRIPT.replace(
        "It steps down the bowl.\n", "[voice narrator]\n\nYes.\n\n[voice guest]\n\nNo.\n\n[voice narrator]\n\nYes.\n"
    )
    plan = take_states(make_inputs(toml=BOTH_PAID, script=again)).plan(spend=True)
    turns = [take for take in plan.takes if take.section.number == 2]
    assert [(take.turn.number, take.outcome) for take in turns] == [
        (1, TakeOutcome.VOICED),
        (2, TakeOutcome.VOICED),
        (3, TakeOutcome.KEPT),
    ]
    assert turns[2].digest == turns[0].digest and turns[2].reason == SHARED
    every = sum(len(canonical_text(section.pieces)) for section in plan_sections(plan) if section.number != 2)
    assert plan.cost.characters == every + len("Yes.") + len("No.")


def plan_sections(plan: NarratePlan) -> list[ScriptSection]:
    """Every section a plan holds a take of, once each, in plan order."""
    return list({take.section.number: take.section for take in plan.takes}.values())


def test_a_renamed_voice_buys_nothing(make_inputs: Callable[..., Inputs]) -> None:
    """The table, `[narration] voice` and every direction renamed together name the same takes."""
    bought = hold_turns(make_inputs(toml=BOTH_PAID, script=DIALOGUE))
    toml = (
        BOTH_PAID.replace("[narration]\n", '[narration]\nvoice = "host"\n')
        .replace("[voices.narrator]", "[voices.host]")
        .replace("[voices.guest]", "[voices.visitor]")
    )
    script = DIALOGUE.replace("[voice narrator]", "[voice host]").replace("[voice guest]", "[voice visitor]")
    environ = {"ELEVENLABS_API_KEY": ENVIRON["ELEVENLABS_API_KEY"], "DECKTALK_VOICES_HOST_ID": VOICE_ID}
    renamed = rewritten(bought, script, toml=toml, environ=environ)
    states = take_states(renamed)
    assert {state.state for state in states.values()} == {TakeState.VOICED}
    assert not states.plan(spend=True).cost.buys
