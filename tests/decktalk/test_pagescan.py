"""The static scan: what a slide's measured catalog says, before anybody looks at a picture."""

from __future__ import annotations

from pathlib import Path

from decktalk import page, pagescan
from decktalk.findings import Code
from decktalk.media.pagereport import ElementRow, MeasuredScene
from decktalk.pagescan import measured_rows, page_findings, scene_cues, scene_entry, slide_cues
from decktalk.pipeline import Stage
from decktalk.settings import KEYS
from support.pages import a_report

PAGE = "deck/index.html"
NONE = 1.0
"""The scale a project that has not turned `motion.scale` renders at, which is no change at all."""


BOX = {"x": 0, "y": 0, "w": 10, "h": 10}
"""One element's box, which every catalog row here shares because none of these cases measures a box."""


def row(cue: str | None = "1.1:open", **attrs: str) -> ElementRow:
    moments = {page.Attr.IN.value: cue} if cue else {}
    return ElementRow.model_validate({"attrs": attrs, "moments": moments, "box": BOX})


def codes(found: list) -> list[Code]:
    return [item.code for item in found]


def entry(scene: str, moments: dict[str, list[str]], **extra: object) -> MeasuredScene:
    """One scene of a catalog, with one element per moment the slide declares."""
    elements = {
        slide: [{"attrs": {}, "moments": {"data-in": cue_id}, "text": "", "box": BOX} for cue_id in cue_ids]
        for slide, cue_ids in moments.items()
    }
    return MeasuredScene.model_validate({"scene": scene, "elements": elements, **extra})


# ---- reading the catalog ----------------------------------------------------------------------


def test_a_scene_entry_is_found_by_its_scene_whatever_type_the_page_wrote_it_in() -> None:
    one, two = entry("1", {}), entry("2", {})
    assert scene_entry((one, two), "2") is two
    assert scene_entry((one,), "9") is None
    assert scene_entry(None, "1") is None


def test_a_scene_declares_its_slides_and_the_cues_it_lists_against_each() -> None:
    listed = entry("1", {"1.1": []}, slides=["1.1"], cues={"1.1": ["1.1:a", "1.1:b"]})
    assert slide_cues(listed) == {"1.1": ("1.1:a", "1.1:b")}
    assert slide_cues(None) is None


def test_a_scene_names_its_slides_in_the_order_the_page_declares_them() -> None:
    assert list(slide_cues(entry("1", {"1.2": []}, slides=["1.1", "1.2"])) or ()) == ["1.1", "1.2"]
    assert list(slide_cues(entry("2", {"2.1": []})) or ()) == ["2.1"]


def test_a_scene_that_lists_no_cues_falls_back_to_the_moments_its_elements_name() -> None:
    assert slide_cues(entry("1", {"1.1": ["1.1:a"]})) == {"1.1": ("1.1:a",)}


def test_a_scene_declares_every_moment_its_elements_carry() -> None:
    assert scene_cues(entry("1", {"1.1": ["1.1:a", "1.1:b"], "1.2": ["1.2:c"]})) == ("1.1:a", "1.1:b", "1.2:c")


def test_a_scene_also_declares_the_cues_its_own_map_names() -> None:
    """A cue a handler alone serves is in the scene's map and on no element, so both are read."""
    one = entry("1", {"1.1": ["1.1:a"]}, cues={"1.1": ["1.1:a", "1.1:handled"]})
    assert scene_cues(one) == ("1.1:a", "1.1:handled")


def test_a_catalog_row_is_the_row_this_module_judges() -> None:
    (row,) = measured_rows(entry("1", {"1.1": ["1.1:a"]}))
    assert row.cue == "1.1:a"


def test_a_staggered_row_carries_the_count_of_children_the_probe_measured() -> None:
    """A stagger's span is judged from its children, so the count must reach the row that is judged."""
    staggered = {"attrs": {"data-stagger": "0.08"}, "moments": {"data-in": "1.1:a"}, "text": "", "box": BOX}
    scene = MeasuredScene.model_validate({"scene": "1", "elements": {"1.1": [{**staggered, "children": 4}]}})
    (row,) = measured_rows(scene)
    assert row.children == 4


def test_a_page_that_reported_nothing_is_judged_on_nothing() -> None:
    assert page_findings(a_report(), page=PAGE) == []


def test_every_page_warning_becomes_the_finding_of_the_code_the_page_carried() -> None:
    report = a_report(
        warnings=[
            {
                "code": Code.PAGE_KATEX_ERROR.value,
                "message": "KaTeX refused $x$.",
                "slide": "1.1",
                "cue": None,
                "attr": None,
            },
            {
                "code": Code.PAGE_UNKNOWN_ATTR.value,
                "message": "data-nope is not an attribute.",
                "slide": None,
                "cue": "1.1:open",
                "attr": "data-nope",
            },
        ]
    )
    found = page_findings(report, page=PAGE, section=1, stage=Stage.RECORD)
    assert [row.code for row in found] == [Code.PAGE_KATEX_ERROR, Code.PAGE_UNKNOWN_ATTR]
    assert [row.location.where for row in found] == ["1.1", "1.1:open"]
    assert found[0].location.file == Path(PAGE)
    assert {row.stage for row in found} == {Stage.RECORD}


# ---- judging the measured rows ----------------------------------------------------------------


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
    container = row(**{"data-stagger": "0.12", "data-spotlight": "", "data-in-seconds": "0.24"})
    container = container.model_copy(update={"children": 5})
    (found,) = pagescan.motion_findings([container], where=PAGE, section=1, scale=NONE)
    assert found.code is Code.PAGE_STAGGER_OVERRUN
    assert found.severity is Code.PAGE_STAGGER_OVERRUN.severity


def test_the_steps_flag_is_never_read_as_a_count_of_children() -> None:
    """`data-spotlight` is a flag, so a page that writes it as `true` is a spotlit stagger and not a crash."""
    container = row(**{"data-stagger": "0.12", "data-spotlight": "true", "data-in-seconds": "0.24"})
    assert pagescan.motion_findings([container], where=PAGE, section=1, scale=NONE) == []
    counted = container.model_copy(update={"children": 5})
    assert [one.code for one in pagescan.motion_findings([counted], where=PAGE, section=1, scale=NONE)] == [
        Code.PAGE_STAGGER_OVERRUN
    ]


def test_a_reduced_render_that_slows_a_motion_past_the_ceiling_is_judged_for_it() -> None:
    """`motion.scale` is the setting the code names, so setting it too far has to say so."""
    slowed = row(**{"data-in-seconds": "0.24"})
    assert pagescan.motion_findings([slowed], where=PAGE, section=1, scale=NONE) == []
    (found,) = pagescan.motion_findings([slowed], where=PAGE, section=1, scale=3.0)
    assert found.code is Code.PAGE_MOTION_OVERRUN
    assert Code.PAGE_MOTION_OVERRUN in next(key for key in KEYS if key.id == "motion.scale").decides


def test_an_element_that_names_no_moment_is_never_judged_for_motion() -> None:
    assert (
        pagescan.motion_findings([row(cue=None, **{"data-in-seconds": "0.9"})], where=PAGE, section=1, scale=NONE) == []
    )


def test_an_element_that_changes_the_picture_and_says_nothing_loses_it_from_the_transcript() -> None:
    (found,) = pagescan.description_findings([row()], where=PAGE, section=1)
    assert found.code is Code.PAGE_NO_DESCRIPTION


def test_an_element_that_describes_itself_or_draws_words_is_not_judged() -> None:
    described = row(**{"data-describe": "the curve appears"})
    worded = row().model_copy(update={"text": "Ninety percent"})
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
    default = page.ATTRS[page.Attr.IN_STYLE].default
    assert default is not None
    assert row().entrance == page.ENTRANCES[default].seconds
