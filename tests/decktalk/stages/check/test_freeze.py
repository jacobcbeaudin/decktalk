"""Which two frozen states each cue is measured between, with no browser and no file."""

from __future__ import annotations

from decktalk.results import SkipReason
from decktalk.stages.check.freeze import (
    fired_by,
    first_state,
    last_state,
    mounts,
    owner_slide,
    plan_frames,
)
from decktalk.stages.storyboard import Freeze

FPS = 25
"""The rate every case here reads a first frame against, which is the rate the recorder captures at."""

SLIDES = {"1.1": ("1.1:a", "1.1:b"), "1.2": ("1.2:c",)}
"""Two slides of one scene, with the cues the catalog lists against each of them."""

TIMES = {"1.1:a": 1.0, "1.1:b": 2.0, "1.2:c": 3.0}
"""Where each of those cues resolved, in seconds after the section starts."""


def test_a_slide_owns_exactly_the_cues_the_catalog_lists_against_it() -> None:
    assert owner_slide("1.1:b", SLIDES) == "1.1"
    assert owner_slide("1.2:c", SLIDES) == "1.2"


def test_a_cue_no_slide_lists_belongs_to_no_slide() -> None:
    """Ownership is declared, so an id that starts with a slide's own name still belongs to nobody."""
    assert owner_slide("1.1:never", SLIDES) is None


def test_the_first_slide_is_already_on_screen_when_its_section_starts() -> None:
    assert mounts(SLIDES, TIMES) == [("1.1", 0.0), ("1.2", 3.0)]


def test_a_slide_mounts_at_its_own_earliest_cue() -> None:
    later = {"1.1:a": 1.0, "1.2:c": 0.5}
    assert mounts(SLIDES, later)[0] == ("1.2", 0.0)


def test_a_slide_with_no_resolved_cue_never_mounts() -> None:
    assert mounts(SLIDES, {"1.1:a": 1.0}) == [("1.1", 0.0)]


def test_the_cues_that_have_fired_are_read_in_the_order_the_slide_declares_them() -> None:
    assert fired_by(SLIDES, TIMES, "1.1", 2.0, inclusive=True) == ["1.1:a", "1.1:b"]
    assert fired_by(SLIDES, TIMES, "1.1", 2.0, inclusive=False) == ["1.1:a"]


def test_the_first_cue_of_a_slide_is_measured_against_the_slide_before_it() -> None:
    pairs = {pair.cue: pair for pair in plan_frames(SLIDES, TIMES, FPS)}
    assert pairs["1.1:a"].before == Freeze("1.1", before="1.1:a")
    assert pairs["1.1:a"].after == Freeze("1.1", cue="1.1:a")


def test_a_later_cue_is_measured_against_the_cue_in_front_of_it() -> None:
    pairs = {pair.cue: pair for pair in plan_frames(SLIDES, TIMES, FPS)}
    assert pairs["1.1:b"].before == Freeze("1.1", cue="1.1:a")


def test_a_cue_that_mounts_its_own_slide_is_measured_against_the_slide_it_replaces() -> None:
    """A viewer was looking at the slide before it, so an empty stage is not what changed."""
    pairs = {pair.cue: pair for pair in plan_frames(SLIDES, TIMES, FPS)}
    assert pairs["1.2:c"].before == Freeze("1.1", cue="1.1:b")


def test_a_cue_inside_the_first_frame_has_no_frame_in_front_of_it() -> None:
    (pair,) = plan_frames({"1.1": ("1.1:a",)}, {"1.1:a": 0.02}, FPS)
    assert pair.reason is SkipReason.AT_SECTION_START
    assert not pair.measured


def test_a_cue_no_slide_declares_is_skipped_with_its_reason() -> None:
    (pair,) = plan_frames(SLIDES, {"1.9:x": 1.0}, FPS)
    assert pair.reason is SkipReason.NO_SLIDE
    assert pair.slide is None


def test_a_slide_whose_cues_fire_out_of_order_carries_a_note() -> None:
    """A freeze fires a slide's cues in the order it declares them, whatever seconds they resolved to."""
    backwards = {"1.1:a": 2.0, "1.1:b": 1.0}
    assert all(pair.note for pair in plan_frames(SLIDES, backwards, FPS))


def test_a_slide_whose_cues_fire_in_order_carries_no_note() -> None:
    assert not any(pair.note for pair in plan_frames(SLIDES, TIMES, FPS))


def test_a_section_ends_on_its_last_slide_with_every_cue_fired() -> None:
    assert last_state(SLIDES, TIMES) == Freeze("1.2", cue="1.2:c")


def test_a_section_with_no_resolved_cue_ends_nowhere() -> None:
    assert last_state(SLIDES, {}) is None


def test_a_section_opens_on_its_first_slide_before_its_first_cue() -> None:
    assert first_state(SLIDES, TIMES, FPS) == Freeze("1.1", before="1.1:a")


def test_a_section_whose_first_cue_is_inside_its_first_frame_opens_with_it_fired() -> None:
    early = {"1.1:a": 0.0, "1.1:b": 2.0}
    assert first_state(SLIDES, early, FPS) == Freeze("1.1", cue="1.1:a")
