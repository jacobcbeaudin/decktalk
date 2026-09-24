"""Every cue resolved to a second on its own section's clock."""

from __future__ import annotations

from pathlib import Path

from decktalk.artifacts.cue_times import CueTimes
from decktalk.results import CueTime, SectionCues

RESOLVED = CueTimes(
    sections=(
        SectionCues(
            section=3,
            key="03",
            estimated=False,
            cues=(
                CueTime(cue="3.1:expand", phrase="On a typical", seconds=1.2, offset=0.0),
                CueTime(cue="3.2:zero", phrase="Zero", seconds=2.5, offset=0.3),
                CueTime(cue="3.3:never", phrase="nowhere", seconds=None, offset=0.0),
            ),
        ),
    )
)


def test_a_section_nothing_resolved_has_no_rows() -> None:
    assert RESOLVED.of(9) is None
    assert RESOLVED.rows(9) == ()
    assert RESOLVED.query(9) is None


def test_the_query_carries_every_resolved_cue_and_leaves_the_rest_out() -> None:
    """A cue with no second behind it is left out rather than passed as a null the page must read."""
    assert RESOLVED.query(3) == "3.1:expand@1.2,3.2:zero@2.5"


def test_the_times_are_keyed_by_wire_id() -> None:
    assert RESOLVED.times(3) == {"3.1:expand": 1.2, "3.2:zero": 2.5}


def test_the_word_behind_a_cue_is_its_second_without_the_author_nudge() -> None:
    assert RESOLVED.word_at(3, "3.2:zero") == 2.2
    assert RESOLVED.word_at(3, "3.3:never") is None


def test_one_row_is_found_by_its_section_and_its_wire_id() -> None:
    assert RESOLVED.row(3, "3.1:expand").phrase == "On a typical"
    assert RESOLVED.row(3, "nothing") is None
    assert RESOLVED.at(3, "3.1:expand") == 1.2


def test_the_file_is_estimated_when_any_section_is() -> None:
    assert not RESOLVED.estimated
    guessed = RESOLVED.sections[0].model_copy(update={"estimated": True})
    assert CueTimes(sections=(guessed,)).estimated


def test_the_cue_times_round_trip_through_their_own_file(tmp_path: Path) -> None:
    path = RESOLVED.write(tmp_path / "cue-times.json")
    assert CueTimes.read(path) == RESOLVED


def test_the_preview_document_carries_each_section_with_its_scene() -> None:
    """A previewed page has no recorder to put its seconds in its URL, so it asks for them."""
    assert RESOLVED.preview({3: "three"}) == {
        "sections": [
            {
                "key": "03",
                "scene": "three",
                "cues": [{"cue": "3.1:expand", "at": 1.2}, {"cue": "3.2:zero", "at": 2.5}],
            }
        ]
    }


def test_a_section_the_project_no_longer_plays_is_left_out_of_the_preview() -> None:
    assert RESOLVED.preview({}) == {"sections": []}
