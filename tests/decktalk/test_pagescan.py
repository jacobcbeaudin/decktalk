"""The static scan: what a slide's measured catalog says, before anybody looks at a picture."""

from __future__ import annotations

from decktalk import page, pagescan
from decktalk.findings import Code
from decktalk.pagescan import Measured

PAGE = "deck/index.html"
NONE = 1.0
"""The scale a project that has not turned `motion.scale` renders at, which is no change at all."""


def row(cue: str | None = "1.1:open", **attrs: str) -> Measured:
    moments = {page.Attr.IN.value: cue} if cue else {}
    return Measured(attrs=attrs, moments=moments, text="", box=(0, 0, 100, 40))


def codes(found: list) -> list[Code]:
    return [item.code for item in found]


def test_a_motion_inside_the_ceiling_is_judged_on_nothing() -> None:
    assert pagescan.motion_findings([row(**{"data-in-seconds": "0.24"})], where=PAGE, section=1, scale=NONE) == []


def test_a_motion_past_the_ceiling_makes_its_own_cue_unmeasurable() -> None:
    long = row(**{"data-in-seconds": "0.6"})
    (found,) = pagescan.motion_findings([long], where=PAGE, section=1, scale=NONE)
    assert found.code is Code.PAGE_MOTION_OVERRUN
    assert found.location.cue == "1.1:open" and found.location.section == 1
    assert "0.60s" in found.message and "0.50s" in found.message


def test_a_staggered_container_is_judged_by_its_own_exact_arithmetic() -> None:
    """The last child starts one step per earlier child after the cue and then plays its entrance."""
    container = row(**{"data-stagger": "0.12", "data-steps": "5", "data-in-seconds": "0.24"})
    (found,) = pagescan.motion_findings([container], where=PAGE, section=1, scale=NONE)
    assert found.code is Code.PAGE_STAGGER_OVERRUN
    assert found.certainty is Code.PAGE_STAGGER_OVERRUN.certainty


def test_a_reduced_render_that_slows_a_motion_past_the_ceiling_is_judged_for_it() -> None:
    """`motion.scale` is the knob the code names, so turning it too far has to say so."""
    slowed = row(**{"data-in-seconds": "0.24"})
    assert pagescan.motion_findings([slowed], where=PAGE, section=1, scale=NONE) == []
    (found,) = pagescan.motion_findings([slowed], where=PAGE, section=1, scale=3.0)
    assert found.code is Code.PAGE_MOTION_OVERRUN
    assert "motion.scale" in Code.PAGE_MOTION_OVERRUN.decides


def test_an_element_that_names_no_moment_is_never_judged_for_motion() -> None:
    assert (
        pagescan.motion_findings([row(cue=None, **{"data-in-seconds": "0.9"})], where=PAGE, section=1, scale=NONE) == []
    )


def test_an_element_that_changes_the_picture_and_says_nothing_loses_it_from_the_transcript() -> None:
    (found,) = pagescan.description_findings([row()], where=PAGE, section=1)
    assert found.code is Code.PAGE_NO_DESCRIPTION


def test_an_element_that_describes_itself_or_draws_words_is_not_judged() -> None:
    described = row(**{"data-describe": "the curve appears"})
    worded = Measured(attrs={}, moments={page.Attr.IN.value: "1.1:open"}, text="Ninety percent", box=None)
    assert pagescan.description_findings([described, worded], where=PAGE, section=1) == []


def test_a_swap_whose_halves_land_together_is_one_change() -> None:
    swap = row(**{"data-swaps": "1.1:other"})
    times = {"1.1:open": 2.0, "1.1:other": 2.2}
    assert pagescan.swap_findings([swap], times, where=PAGE, section=1) == []


def test_a_swap_whose_halves_land_apart_is_seen_as_two() -> None:
    swap = row(**{"data-swaps": "1.1:other"})
    times = {"1.1:open": 2.0, "1.1:other": 4.0}
    (found,) = pagescan.swap_findings([swap], times, where=PAGE, section=1)
    assert found.code is Code.PAGE_SWAP_APART and "2.00s" in found.message


def test_a_swap_whose_partner_never_resolved_is_not_judged() -> None:
    swap = row(**{"data-swaps": "1.1:other"})
    assert pagescan.swap_findings([swap], {"1.1:open": 2.0}, where=PAGE, section=1) == []


def test_two_cues_closer_than_the_first_one_is_still_playing_run_into_each_other() -> None:
    first = row(cue="1.1:one", **{"data-in-seconds": "0.32"})
    second = row(cue="1.1:two")
    times = {"1.1:one": 2.0, "1.1:two": 2.1}
    (found,) = pagescan.overlap_findings([first, second], times, where=PAGE, section=1, scale=NONE)
    assert found.code is Code.CUE_OVERLAP and found.location.cue == "1.1:two"


def test_two_cues_further_apart_than_the_motion_between_them_are_not_judged() -> None:
    first = row(cue="1.1:one", **{"data-in-seconds": "0.12"})
    times = {"1.1:one": 2.0, "1.1:two": 2.5}
    assert pagescan.overlap_findings([first], times, where=PAGE, section=1, scale=NONE) == []


def test_an_asset_from_another_origin_is_a_file_the_film_does_not_own() -> None:
    (found,) = pagescan.asset_findings(["https://cdn.example", "https://cdn.example"], where=PAGE)
    assert found.code is Code.PAGE_CDN_ASSET and "cdn.example" in found.message


def test_one_call_reaches_every_static_judgement_a_slide_supports() -> None:
    rows = [
        row(cue="1.1:one", **{"data-in-seconds": "0.6"}),
        row(cue="1.1:two", **{"data-describe": "a note"}),
    ]
    found = pagescan.slide_findings(rows, {"1.1:one": 2.0, "1.1:two": 2.2}, where=PAGE, section=1, scale=NONE)
    assert Code.PAGE_MOTION_OVERRUN in codes(found)
    assert Code.PAGE_NO_DESCRIPTION in codes(found)
    assert Code.CUE_OVERLAP in codes(found)


def test_an_entrance_with_no_seconds_of_its_own_takes_the_span_of_the_style_it_names() -> None:
    assert row(**{"data-in-style": "draw"}).entrance == page.ENTRANCES["draw"].seconds
    assert row().entrance == page.ENTRANCES[page.ATTRS[page.Attr.IN_STYLE].default].seconds
