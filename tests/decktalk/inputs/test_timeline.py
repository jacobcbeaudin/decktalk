"""Where the joined narration plays in the finished film, run by run."""

from __future__ import annotations

from decktalk.artifacts.takes import Take, Takes
from decktalk.inputs.document import ClipSection, PageSection
from decktalk.inputs.timeline import narration_offsets, narration_runs


def page(number: int, *, hold: float = 0.0) -> PageSection:
    return PageSection(number=number, page="deck/index.html", scene=str(number), hold_seconds=hold)


def clip(number: int) -> ClipSection:
    return ClipSection(number=number, clip="media/broll.mp4")


def take(number: int, seconds: float) -> Take:
    return Take(
        section=number,
        key=f"{number:02d}",
        chapter="",
        hash=f"h{number}",
        voiced=True,
        word_count=1,
        characters=1,
        estimated_seconds=seconds,
        duration_seconds=seconds,
        speech_end_seconds=seconds,
        sound_end_seconds=seconds,
        spoken="x",
    )


def index(*rows: Take) -> Takes:
    return Takes(script="script.md", model="m", output_format="f", sections=rows)


def test_a_film_with_nothing_between_its_pages_plays_the_whole_track_as_one_run() -> None:
    sections = [page(1), page(2)]
    takes = index(take(1, 2.0), take(2, 3.0))
    (run,) = narration_runs(sections, takes, {1: 0.0, 2: 2.0})
    assert (run.sections, run.at, run.start, run.end) == ((1, 2), 0.0, 0.0, None)
    assert run.offset == 0.0


def test_a_clip_between_two_pages_breaks_the_track_into_two_runs() -> None:
    """The narration pauses for the clip, and the next page resumes it on its own first frame."""
    sections = [page(1), clip(2), page(3)]
    takes = index(take(1, 2.0), take(3, 3.0))
    first, second = narration_runs(sections, takes, {1: 0.0, 2: 2.0, 3: 7.0})
    assert first.sections == (1,) and first.start == 0.0 and first.end == 2.0
    assert second.sections == (3,) and second.at == 7.0 and second.start == 2.0 and second.end is None
    assert second.offset == 5.0


def test_a_held_page_ends_its_run_so_the_next_section_starts_the_track_again() -> None:
    sections = [page(1, hold=1.5), page(2)]
    takes = index(take(1, 2.0), take(2, 3.0))
    first, second = narration_runs(sections, takes, {1: 0.0, 2: 3.5})
    assert first.sections == (1,) and second.sections == (2,)
    assert second.offset == 1.5


def test_a_section_the_film_does_not_play_takes_the_offset_of_the_first_run() -> None:
    sections = [page(1)]
    takes = index(take(1, 2.0), take(9, 1.0))
    assert narration_offsets(sections, takes, {1: 4.0}) == {1: 4.0, 9: 4.0}


def test_a_film_with_no_spoken_section_offsets_nothing() -> None:
    assert narration_runs([clip(1)], index(), {1: 0.0}) == ()
    assert narration_offsets([clip(1)], index(), {1: 0.0}) == {}
