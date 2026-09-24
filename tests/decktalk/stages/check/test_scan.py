"""What a frozen share means, and what one pass over the frozen frames draws and judges."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.findings import Code
from decktalk.media.pagereport import SceneCatalog
from decktalk.settings import Settings
from decktalk.stages.check import scan
from decktalk.stages.check.scan import (
    DRAW_STYLE,
    Sheet,
    drawn_cues,
    judged_pages,
    landing_findings,
    opening_panels,
    origin_findings,
    page_findings,
    seam_findings,
    settle_milliseconds,
    share_code,
    share_message,
    static_findings,
)
from decktalk.stages.storyboard import Freeze, slide_cues

from .conftest import BOX, a_project, a_report, a_run, catalog

SLIDES = {"1.1": ("1.1:a", "1.1:b")}
"""One slide with two cues, which is enough to measure a pair and to leave one in front of it."""

TIMES = {"1.1:a": 1.0, "1.1:b": 2.0}
"""Where those two cues resolved, in seconds after the section starts."""

SEAMLESS = """
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
seamless = true
"""
"""A project whose second section carries the picture the first one ended on."""


def settings() -> Settings:
    return Settings()


def entry_of(moments: dict[str, list[str]]) -> SceneCatalog:
    return SceneCatalog.model_validate(catalog("1", moments))


def wrote(out: Path) -> None:
    """Stand in for a screenshot, which leaves a file where a real one would."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"png")


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The screenshot and the comparison faked, with the share every comparison reads in one list."""
    share = [0.0]
    monkeypatch.setattr(scan, "screenshot", lambda _page, _url, out, **_kwargs: wrote(out))
    monkeypatch.setattr(scan.frames, "changed_images_percent", lambda *_a, **_k: share[0])
    return share


def test_a_share_under_the_floor_is_a_reveal_that_never_happened() -> None:
    assert share_code(0.0, settings(), drawn=False) is Code.CUE_NO_CHANGE


def test_a_stroke_under_the_floor_is_a_stroke_too_thin_to_see() -> None:
    """A stroke sweeps a thin area, so the same number means something else about it."""
    assert share_code(0.0, settings(), drawn=True) is Code.PAGE_THIN_DRAW


def test_a_share_that_only_just_passes_is_uncertain() -> None:
    just = settings().verify.changed_share_min_percent * 1.5
    assert share_code(just, settings(), drawn=False) is Code.CUE_THIN_CHANGE


def test_a_clean_reveal_is_no_judgement_at_all() -> None:
    assert share_code(settings().verify.changed_share_min_percent * 10, settings(), drawn=False) is None


@pytest.mark.parametrize("code", [Code.CUE_NO_CHANGE, Code.CUE_THIN_CHANGE, Code.PAGE_THIN_DRAW])
def test_every_sentence_carries_the_number_it_measured(code: Code) -> None:
    said = share_message(code, "1.1:a", 0.05, settings())
    assert "0.05" in said
    assert "percent" in said


def test_a_page_warning_is_judged_by_the_code_the_page_named() -> None:
    """The page carries its own code, so nothing here reads a sentence to work out what happened."""
    report = a_report(warnings=[{"code": "PAGE_KATEX_ERROR", "message": "KaTeX refused it.", "slide": "1.1"}])
    (found,) = page_findings(report, where="deck/index.html", section=1)
    assert found.code is Code.PAGE_KATEX_ERROR
    assert found.location.where == "1.1"
    assert found.location.section == 1


def test_an_origin_the_page_reached_for_is_judged_against_the_page() -> None:
    (found,) = origin_findings(["https://cdn.example"], where="deck/index.html")
    assert found.code is Code.PAGE_CDN_ASSET
    assert "cdn.example" in found.message


def test_an_element_that_describes_nothing_is_judged_from_the_catalog_alone() -> None:
    """The measured rows say what is on the slide, so this judgement needs no picture at all."""
    entry = SceneCatalog.model_validate(
        {
            "scene": "1",
            "elements": {"1.1": [{"attrs": {}, "moments": {"data-in": "1.1:a"}, "text": "", "box": BOX}]},
            "slides": ["1.1"],
            "cues": {"1.1": ["1.1:a"]},
        }
    )
    found = static_findings(entry, TIMES, where="deck/index.html", section=1, settings=settings())
    assert Code.PAGE_NO_DESCRIPTION in {one.code for one in found}


def test_the_strokes_of_a_scene_are_the_elements_that_arrive_drawn() -> None:
    entry = SceneCatalog.model_validate(
        {
            "scene": "1",
            "elements": {
                "1.1": [
                    {"attrs": {"data-in-style": DRAW_STYLE}, "moments": {"data-in": "1.1:a"}, "text": "x", "box": BOX},
                    {"attrs": {"data-in-style": "fade"}, "moments": {"data-in": "1.1:b"}, "text": "y", "box": BOX},
                ]
            },
        }
    )
    assert drawn_cues(entry) == {"1.1:a"}


def test_a_state_is_drawn_once_however_many_pairs_name_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs = a_project(tmp_path)
    shots: list[str] = []
    monkeypatch.setattr(scan, "screenshot", lambda _p, url, out, **_k: (shots.append(url), wrote(out)) and None)
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    section = inputs.document.page_sections[0]
    first = sheet.frozen(section, Freeze("1.1", cue="1.1:a"))
    assert sheet.frozen(section, Freeze("1.1", cue="1.1:a")) == first
    assert len(shots) == 1


def test_a_thin_frozen_share_is_judged_against_the_frame_it_was_read_on(tmp_path: Path, frozen: list[float]) -> None:
    inputs = a_project(tmp_path)
    frozen[0] = 0.0
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    section = inputs.document.page_sections[0]
    found = landing_findings(sheet, section, entry_of({"1.1": list(SLIDES["1.1"])}), SLIDES, TIMES, skipped=set())
    assert {one.code for one in found} == {Code.CUE_NO_CHANGE}
    assert all(one.location.file is not None for one in found)


def test_a_clean_frozen_share_judges_nothing(tmp_path: Path, frozen: list[float]) -> None:
    inputs = a_project(tmp_path)
    frozen[0] = 40.0
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    section = inputs.document.page_sections[0]
    assert landing_findings(sheet, section, entry_of({"1.1": list(SLIDES["1.1"])}), SLIDES, TIMES, skipped=set()) == []


def test_a_cue_the_cue_file_opts_out_of_is_never_measured(tmp_path: Path, frozen: list[float]) -> None:
    inputs = a_project(tmp_path)
    frozen[0] = 0.0
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    section = inputs.document.page_sections[0]
    entry = entry_of({"1.1": list(SLIDES["1.1"])})
    assert landing_findings(sheet, section, entry, SLIDES, TIMES, skipped={(1, "1.1:a"), (1, "1.1:b")}) == []


def test_a_seam_that_would_show_is_a_pop_at_the_cut(tmp_path: Path, frozen: list[float]) -> None:
    inputs = a_project(tmp_path, toml=SEAMLESS)
    frozen[0] = 50.0
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    first, second = inputs.document.page_sections
    slides = {1: {"1.1": ("1.1:a",)}, 2: {"2.1": ("2.1:a",)}}
    times = {1: {"1.1:a": 1.0}, 2: {"2.1:a": 1.0}}
    (found,) = seam_findings(sheet, first, second, slides, times)
    assert found.code is Code.CUT_POP
    assert "50.00" in found.message


def test_a_seam_that_holds_its_picture_is_no_judgement(tmp_path: Path, frozen: list[float]) -> None:
    inputs = a_project(tmp_path, toml=SEAMLESS)
    frozen[0] = 0.0
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    first, second = inputs.document.page_sections
    slides = {1: {"1.1": ("1.1:a",)}, 2: {"2.1": ("2.1:a",)}}
    times = {1: {"1.1:a": 1.0}, 2: {"2.1:a": 1.0}}
    assert seam_findings(sheet, first, second, slides, times) == []


def test_a_seam_whose_side_published_no_catalog_is_a_line_and_no_judgement(tmp_path: Path, frozen: list[float]) -> None:
    inputs = a_project(tmp_path, toml=SEAMLESS)
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    first, second = inputs.document.page_sections
    assert seam_findings(sheet, first, second, {}, {}) == []
    assert frozen[0] == 0.0


def test_every_slide_keeps_the_state_it_opens_on_as_a_panel(tmp_path: Path, frozen: list[float]) -> None:
    inputs = a_project(tmp_path)
    frozen[0] = 0.0
    sheet = Sheet(inputs, a_run(tmp_path), {"deck/index.html": object()}, 0)
    section = inputs.document.page_sections[0]
    landing_findings(sheet, section, entry_of({"1.1": list(SLIDES["1.1"])}), SLIDES, TIMES, skipped=set())
    opening_panels(sheet, section, SLIDES, TIMES)
    assert any(panel.cue is None for panel in sheet.panels)
    assert {panel.cue for panel in sheet.panels if panel.cue} == set(SLIDES["1.1"])


def test_the_pages_judged_are_the_sections_pages_and_the_ones_a_caller_named(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    assert judged_pages(inputs.document.page_sections, ["deck/other.html"]) == ("deck/index.html", "deck/other.html")


def test_the_settle_is_read_from_the_key_that_states_it_in_seconds(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    assert settle_milliseconds(inputs) == int(inputs.settings.record.screenshot_settle_seconds * 1000)


def test_a_scene_declares_its_slides_and_their_cues() -> None:
    assert slide_cues(entry_of({"1.1": ["1.1:a"]})) == {"1.1": ("1.1:a",)}
