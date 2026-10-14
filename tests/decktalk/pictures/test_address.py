"""Where a page section is opened, for the recorder and for each frozen state of it.

Every query key is a member of the contract's own vocabulary, so a page URL cannot be built by hand
and a param naming a key no page reads is refused. A frozen state asks the page for a state and
never for the recorder's clock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.artifacts import CueTimes, Words
from decktalk.errors import InputError
from decktalk.inputs import Inputs, PageSection, TurnWords
from decktalk.page import Q
from decktalk.pictures.address import Freeze, as_query, freeze_url, scene_params, scene_url, turns_param, words_param
from decktalk.results import CueTime, SectionCues, Word
from support.pages import TWO_SCENE_PAGE
from support.projects import load_project

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

[[section]]
number = 3
clip = "media/broll.mp4"
words = "media/broll.words.json"

[mix]
music = "media/bed.mp3"
slate = "media/slate.png"

[[mix.effect]]
file = "media/chime.wav"
section = 1
cue = "1.1:open"
"""


def a_project(tmp_path: Path, toml: str = TOML, page: str = TWO_SCENE_PAGE) -> Inputs:
    return load_project(tmp_path, toml, page=page)


def section_of(inputs: Inputs, number: int) -> PageSection:
    found = inputs.document.section(number)
    assert isinstance(found, PageSection)
    return found


def cue_times() -> CueTimes:
    row = CueTime(id="1.1:open", phrase="one", seconds=1.5, nudge_seconds=0.0)
    return CueTimes(sections=(SectionCues(section=1, key="01", estimated=False, cues=(row,)),))


def test_a_param_naming_a_key_no_page_reads_is_refused() -> None:
    with pytest.raises(InputError, match="nope"):
        as_query({"nope": "1"})


def test_a_param_naming_a_contract_key_becomes_that_key() -> None:
    assert as_query({"hud": "1"}) == {Q.HUD: "1"}


def test_the_resolved_cues_join_the_query(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    params = scene_params(section_of(inputs, 1), cue_times())
    assert params[Q.CUES] == "1.1:open@1.5"


def test_a_section_that_sets_its_own_cues_keeps_them(tmp_path: Path) -> None:
    toml = TOML.replace('scene = "1"\n', 'scene = "1"\nparams = { cues = "1.1:open@9" }\n', 1)
    inputs = a_project(tmp_path, toml)
    assert scene_params(section_of(inputs, 1), cue_times())[Q.CUES] == "1.1:open@9"


def test_the_page_url_carries_the_scene_and_the_recorder_signal(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    url = scene_url(inputs, section_of(inputs, 1), scene_params(section_of(inputs, 1), cue_times()))
    assert url.startswith("http://project.localhost/deck/index.html?")
    assert "scene=1" in url
    assert "t0=signal" in url


def test_a_section_whose_page_is_not_there_is_refused(tmp_path: Path) -> None:
    inputs = load_project(tmp_path, TOML)
    with pytest.raises(InputError, match="deck/index.html"):
        scene_url(inputs, section_of(inputs, 1), {})


def test_a_word_never_carries_the_separators_the_query_uses() -> None:
    value = words_param((Word(word="one,two@three", start=0.0, end=0.4),))
    assert value == "onetwothree@0.000"


def test_no_words_is_no_query_value() -> None:
    assert words_param(()) is None


def test_each_turn_is_written_as_its_voice_and_the_second_it_begins() -> None:
    """The page reads which voice says each word, and in which turn, from the second every turn begins."""
    said = Words(words=(Word(word="hi", start=0.5, end=0.8),))
    turns = (
        TurnWords(turn=1, voice="narrator", spoken="hi", words=said, start=0.4),
        TurnWords(turn=2, voice="guest", spoken="", words=Words(), start=3.104),
    )
    assert turns_param(turns) == "narrator@0.400,guest@3.104"
    assert turns_param(()) is None


def test_a_last_word_just_before_the_next_turn_is_written_before_that_turn_begins() -> None:
    """With no gap between turns, a last word can start a few milliseconds before the next turn does, and the page
    gives a word to the last turn that began at or before it, so both are written to the same thousandth."""
    last = Word(word="rolls", start=3.996, end=3.999)
    turns = (
        TurnWords(turn=1, voice="narrator", spoken="rolls", words=Words(words=(last,)), start=0.0),
        TurnWords(turn=2, voice="guest", spoken="", words=Words(), start=4.0),
    )
    word = float((words_param((last,)) or "").split("@")[1])
    begins = float((turns_param(turns) or "").split(",")[1].split("@")[1])
    assert word < begins


# ---- a frozen state ---------------------------------------------------------------------------


def test_a_frozen_state_asks_the_page_for_a_slide_and_a_moment() -> None:
    assert Freeze("1.1", cue="1.1:a").query() == {Q.FREEZE: "1.1", Q.AFTER: "1.1:a"}
    assert Freeze("1.1", before="1.1:a").query() == {Q.FREEZE: "1.1", Q.BEFORE: "1.1:a"}
    assert Freeze("1.1").query() == {Q.FREEZE: "1.1"}


def test_a_frozen_state_names_one_file_safely() -> None:
    assert Freeze("4.1", cue="4.1:expand").label == "freeze-4.1-after-4.1_expand"


def test_a_frozen_url_asks_for_a_state_and_never_for_the_recorder_clock(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    url = freeze_url(section_of(inputs, 1), Freeze("1.1", cue="1.1:a"), {})
    assert "freeze=1.1" in url
    assert "after=1.1" in url
    assert "t0=" not in url
