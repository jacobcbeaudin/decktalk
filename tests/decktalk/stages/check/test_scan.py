"""What a frozen share means, and what one pass over the frozen frames draws and judges."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.findings import Code
from decktalk.inputs import Inputs
from decktalk.machine import Run
from decktalk.media.pagereport import MeasuredScene
from decktalk.pagescan import page_findings, slide_cues
from decktalk.settings import Settings
from decktalk.stages.check.scan import (
    DRAW_STYLE,
    drawn_cues,
    judged_pages,
    landing_findings,
    opening_panels,
    seam_findings,
    share_code,
    share_message,
    static_findings,
)
from decktalk.stages.storyboard import Freeze, Sheet
from support.pages import BOX, a_project, catalog
from support.runs import a_run, notes

from .conftest import Drawn, FakeAssets, a_report

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


def entry_of(moments: dict[str, list[str]]) -> MeasuredScene:
    return MeasuredScene.model_validate(catalog("1", moments))


def a_sheet(inputs: Inputs, run: Run | None = None) -> Sheet:
    """The sheet one pass draws on, with the project's one page opened on nothing."""
    run = run or a_run(inputs.root)
    return Sheet(inputs, run, {"deck/index.html": (object(), FakeAssets())}, inputs.workspace.frames_dir)


FLOOR = Settings().verify.changed_share_min_percent
"""The least share a reveal may change, which each row below is measured against."""


@pytest.mark.parametrize(
    ("share", "drawn", "code"),
    [
        pytest.param(0.0, False, Code.CUE_NO_CHANGE, id="under the floor is a reveal that never happened"),
        # A stroke sweeps a thin area, so the same number means something else about it.
        pytest.param(0.0, True, Code.PAGE_THIN_DRAW, id="a stroke under the floor is too thin to see"),
        pytest.param(FLOOR * 1.5, False, Code.CUE_THIN_CHANGE, id="only just passing is uncertain"),
        pytest.param(FLOOR * 10, False, None, id="a clean reveal is no judgement at all"),
    ],
)
def test_a_share_is_judged_against_the_floor(share: float, drawn: bool, code: Code | None) -> None:
    assert share_code(share, settings(), drawn=drawn) is code


@pytest.mark.parametrize("code", [Code.CUE_NO_CHANGE, Code.CUE_THIN_CHANGE, Code.PAGE_THIN_DRAW])
def test_every_sentence_carries_the_number_it_measured(code: Code) -> None:
    said = share_message(code, "1.1:a", 0.05, settings())
    assert "0.05" in said
    assert "percent" in said


def test_a_page_warning_is_judged_by_the_code_the_page_named() -> None:
    """The page carries its own code, so nothing here reads a sentence to work out what happened."""
    report = a_report(warnings=[{"code": "PAGE_KATEX_ERROR", "message": "KaTeX refused it.", "slide": "1.1"}])
    (found,) = page_findings(report, page="deck/index.html", section=1)
    assert found.code is Code.PAGE_KATEX_ERROR
    assert found.location.where == "1.1"
    assert found.location.section == 1


def test_an_element_that_describes_nothing_is_judged_from_the_catalog_alone() -> None:
    """The measured rows say what is on the slide, so this judgement needs no picture at all."""
    entry = MeasuredScene.model_validate(catalog("1", {"1.1": ["1.1:a"]}, text=""))
    found = static_findings(entry, TIMES, where="deck/index.html", section=1, settings=settings())
    assert Code.PAGE_NO_DESCRIPTION in {one.code for one in found}


def test_the_strokes_of_a_scene_are_the_elements_that_arrive_drawn() -> None:
    entry = MeasuredScene.model_validate(
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


def test_a_state_is_drawn_once_however_many_pairs_name_it(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path)
    sheet = a_sheet(inputs)
    section = inputs.document.page_sections[0]
    first = sheet.frozen(section, Freeze("1.1", cue="1.1:a"))
    assert sheet.frozen(section, Freeze("1.1", cue="1.1:a")) == first
    assert len(drawn.shots) == 1


@pytest.mark.parametrize(("share", "codes"), [(0.0, {Code.CUE_NO_CHANGE}), (40.0, set())], ids=["thin", "clean"])
def test_a_frozen_share_is_judged_against_the_frame_it_was_read_on(
    tmp_path: Path, drawn: Drawn, share: float, codes: set[Code]
) -> None:
    inputs = a_project(tmp_path)
    drawn.share = share
    section = inputs.document.page_sections[0]
    entry = entry_of({"1.1": list(SLIDES["1.1"])})
    found = landing_findings(a_sheet(inputs), section, entry, SLIDES, TIMES, skipped=set())
    assert {one.code for one in found} == codes
    assert all(one.location.file is not None for one in found)


def test_a_cue_no_element_declares_is_passed_over_rather_than_judged(tmp_path: Path, drawn: Drawn) -> None:
    """A still fires cues and runs no handler, so a `data-owns` cue draws nothing however well it plays."""
    inputs = a_project(tmp_path)
    drawn.share = 0.0
    run = a_run(tmp_path)
    said = notes(run)
    section = inputs.document.page_sections[0]
    found = landing_findings(a_sheet(inputs, run), section, entry_of({"1.1": ["1.1:a"]}), SLIDES, TIMES, skipped=set())
    assert [one.location.cue for one in found] == ["1.1:a"]
    assert any("1.1:b" in line and "runs no handler" in line for line in said)


def test_a_cue_the_cue_file_opts_out_of_is_never_measured(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path)
    drawn.share = 0.0
    section = inputs.document.page_sections[0]
    entry = entry_of({"1.1": list(SLIDES["1.1"])})
    assert landing_findings(a_sheet(inputs), section, entry, SLIDES, TIMES, skipped={(1, "1.1:a"), (1, "1.1:b")}) == []


@pytest.mark.parametrize(("share", "codes"), [(50.0, [Code.CUT_POP]), (0.0, [])], ids=["shows", "holds"])
def test_a_seam_is_a_pop_at_the_cut_only_when_it_would_show(
    tmp_path: Path, drawn: Drawn, share: float, codes: list[Code]
) -> None:
    inputs = a_project(tmp_path, toml=SEAMLESS)
    drawn.share = share
    first, second = inputs.document.page_sections
    slides = {1: {"1.1": ("1.1:a",)}, 2: {"2.1": ("2.1:a",)}}
    times = {1: {"1.1:a": 1.0}, 2: {"2.1:a": 1.0}}
    found = seam_findings(a_sheet(inputs), first, second, slides, times)
    assert [one.code for one in found] == codes
    assert all("50.00" in one.message for one in found)


def test_a_seam_whose_side_published_no_catalog_is_a_line_and_no_judgement(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path, toml=SEAMLESS)
    first, second = inputs.document.page_sections
    assert seam_findings(a_sheet(inputs), first, second, {}, {}) == []
    assert drawn.shots == []


def test_every_slide_keeps_the_state_it_opens_on_as_a_panel(tmp_path: Path, drawn: Drawn) -> None:
    inputs = a_project(tmp_path)
    drawn.share = 0.0
    sheet = a_sheet(inputs)
    section = inputs.document.page_sections[0]
    landing_findings(sheet, section, entry_of({"1.1": list(SLIDES["1.1"])}), SLIDES, TIMES, skipped=set())
    opening_panels(sheet, section, SLIDES, TIMES)
    assert any(panel.cue is None for panel in sheet.panels)
    assert {panel.cue for panel in sheet.panels if panel.cue} == set(SLIDES["1.1"])


def test_the_pages_judged_are_the_sections_pages_and_the_ones_a_caller_named(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    assert judged_pages(inputs.document.page_sections, ["deck/other.html"]) == ("deck/index.html", "deck/other.html")


def test_a_scene_declares_its_slides_and_their_cues() -> None:
    assert slide_cues(entry_of({"1.1": ["1.1:a"]})) == {"1.1": ("1.1:a",)}
