"""Every slide at every cue, frozen onto one page, which is what a person reads before they spend."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.errors import ToolError
from decktalk.inputs import Inputs
from decktalk.media.pagereport import PageReport
from decktalk.pictures.address import Freeze
from decktalk.results import StoryboardResult
from decktalk.settings import BY_ID
from decktalk.stages.storyboard import Selection, panels_of, storyboard
from support.fakes import Browsed
from support.pages import SPIN_CEILING_SECONDS, a_project, a_spinning_project, catalog
from support.runs import a_run

CUES = {"1": {"cues": [{"id": "1.1:a", "phrase": "there"}]}}
"""One cue whose phrase the demo script really speaks, so it resolves to a second."""


class Opened(Browsed):
    """The browser the stage reaches, with what each page of the project publishes."""

    def publishes(self, page: str, *scenes: dict) -> None:
        self.reports[page] = PageReport.model_validate({"catalog": list(scenes)})


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Opened:
    """The browser seams `storyboard` reaches, replaced so the stage runs whole and opens nothing."""
    made = Opened()
    made.install(monkeypatch)
    return made


# ---- the panels a run draws --------------------------------------------------------------------


def test_a_slide_shows_the_state_it_opens_on_before_any_of_its_cues() -> None:
    panels = panels_of({"1.1": ("1.1:a", "1.1:b")}, {"1.1:a": 1.0, "1.1:b": 2.0})
    assert [cue for _freeze, cue, _at in panels] == [None, "1.1:a", "1.1:b"]
    assert panels[0][0] == Freeze("1.1", before="1.1:a")
    assert panels[0][2] == 1.0


def test_a_slide_with_no_resolved_cue_still_gets_its_panels() -> None:
    """A storyboard is read before `cue` has run as well as after, so nothing waits on a second."""
    panels = panels_of({"1.1": ("1.1:a",)}, {})
    assert [cue for _freeze, cue, _at in panels] == [None, "1.1:a"]


def test_a_named_slide_is_the_only_slide_that_contributes() -> None:
    panels = panels_of(
        {"1.1": ("1.1:a",), "1.2": ("1.2:a",)}, {"1.1:a": 1.0, "1.2:a": 2.0}, Selection.of(["1.2"], None, None, None)
    )
    assert {freeze.slide for freeze, _cue, _at in panels} == {"1.2"}


def test_a_named_cue_draws_the_state_after_it_and_drops_the_opening_states() -> None:
    panels = panels_of(
        {"1.1": ("1.1:a", "1.1:b")}, {"1.1:a": 1.0, "1.1:b": 2.0}, Selection.of(None, ["1.1:b"], None, None)
    )
    assert panels == [(Freeze("1.1", cue="1.1:b"), "1.1:b", 2.0)]


def test_the_two_states_around_one_cue_are_the_pair_an_author_compares() -> None:
    chosen = Selection.of(None, ["1.1:a"], ["1.1:a"], None)
    panels = panels_of({"1.1": ("1.1:a",)}, {"1.1:a": 1.0}, chosen)
    assert [freeze for freeze, _cue, _at in panels] == [Freeze("1.1", cue="1.1:a"), Freeze("1.1", before="1.1:a")]


def test_a_second_names_whatever_is_on_screen_then() -> None:
    panels = panels_of(
        {"1.1": ("1.1:a", "1.1:b")}, {"1.1:a": 1.0, "1.1:b": 4.0}, Selection.of(None, None, None, [2.5, 9.0])
    )
    assert [at for _freeze, _cue, at in panels] == [1.0, 4.0]


def test_a_second_before_the_scene_starts_names_nothing() -> None:
    panels = panels_of({"1.1": ("1.1:a",)}, {"1.1:a": 3.0}, Selection.of(None, None, None, [1.0]))
    assert panels == []


def test_a_selector_that_matches_nothing_draws_nothing() -> None:
    assert panels_of({"1.1": ("1.1:a",)}, {"1.1:a": 1.0}, Selection.of(["9.9"], None, None, None)) == []


@pytest.fixture
def published(tmp_path: Path, opened: Opened) -> Inputs:
    """The demo project, whose one page publishes both its scenes with one cue each."""
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    return inputs


# ---- the stage ---------------------------------------------------------------------------------


def test_it_draws_one_still_per_moment_and_writes_the_sheet(tmp_path: Path, opened: Opened, published: Inputs) -> None:
    result = storyboard(published, a_run(tmp_path))
    assert isinstance(result, StoryboardResult)
    assert result.storyboard == Path("build/storyboard.html")
    assert len(result.panels) == len(opened.shots)
    assert (tmp_path / "build" / "storyboard.html").is_file()


def test_it_judges_nothing_at_all(tmp_path: Path, published: Inputs) -> None:
    """A storyboard draws what the film will show, and what it shows is for a person to judge."""
    result = storyboard(published, a_run(tmp_path))
    assert result.findings == ()
    assert result.ok is True


def test_a_selection_draws_the_sections_it_names_and_no_others(tmp_path: Path, published: Inputs) -> None:
    result = storyboard(published, a_run(tmp_path), only=[2])
    assert {panel.section for panel in result.panels} == {2}


def test_the_four_selectors_reach_the_sheet_through_the_stage(tmp_path: Path, published: Inputs) -> None:
    """The selectors are the stage's own arguments, so a caller narrows the sheet without a second call."""
    result = storyboard(published, a_run(tmp_path), slide=["1.1"], after=["1.1:a"])
    assert [(panel.slide, panel.cue) for panel in result.panels] == [("1.1", "1.1:a")]


def test_a_scene_that_published_no_catalog_is_a_line_and_no_panel(tmp_path: Path, opened: Opened) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}))
    result = storyboard(inputs, a_run(tmp_path))
    assert {panel.section for panel in result.panels} == {1}


def test_a_project_with_no_page_section_writes_no_sheet(tmp_path: Path, opened: Opened) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}))
    result = storyboard(inputs, a_run(tmp_path), only=[9])
    assert result.storyboard is None
    assert result.panels == ()


def test_every_still_it_wrote_is_reported(tmp_path: Path, published: Inputs) -> None:
    result = storyboard(published, a_run(tmp_path))
    assert Path("build/storyboard.html") in result.written
    assert all(str(one).startswith("build/") for one in result.written)


# ---- frames kept by what drew them -------------------------------------------------------------


def test_a_second_storyboard_reads_every_still_back_and_draws_none(
    tmp_path: Path, opened: Opened, published: Inputs
) -> None:
    """A repeat storyboard with nothing changed falls to the catalog read, which is all a page must do."""
    first = storyboard(published, a_run(tmp_path))
    drawn = len(opened.shots)
    again = storyboard(published, a_run(tmp_path))
    assert len(opened.shots) == drawn
    assert again.panels == first.panels
    assert all((tmp_path / panel.image).is_file() for panel in again.panels)


def test_an_edited_page_is_drawn_again(tmp_path: Path, opened: Opened) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}))
    storyboard(inputs, a_run(tmp_path), only=[1])
    drawn = len(opened.shots)
    (tmp_path / "deck" / "index.html").write_text("<div data-scene='1'>edited</div>", encoding="utf-8")
    storyboard(inputs, a_run(tmp_path), only=[1])
    assert len(opened.shots) == 2 * drawn


def test_a_changed_file_the_page_loaded_draws_its_stills_again(tmp_path: Path, opened: Opened) -> None:
    """The page's own markup is unchanged, and the picture it loaded is what moved."""
    inputs = a_project(tmp_path, cues=CUES)
    (tmp_path / "deck" / "hero.png").write_bytes(b"one")
    opened.assets.paths.append("deck/hero.png")
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}))
    storyboard(inputs, a_run(tmp_path), only=[1])
    drawn = len(opened.shots)
    (tmp_path / "deck" / "hero.png").write_bytes(b"two")
    storyboard(inputs, a_run(tmp_path), only=[1])
    assert len(opened.shots) == 2 * drawn


def test_a_different_frame_size_is_a_different_still(tmp_path: Path) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    wide = Inputs.load(tmp_path, environ={}, overrides=("video.width=1280", "video.height=720"))
    page = inputs.document.page_sections[0].page
    assert inputs.still_key(page, "url") != wide.still_key(page, "url")


def test_a_frame_drawn_under_one_page_policy_is_never_kept_for_the_other(tmp_path: Path) -> None:
    """An untrusted page has its other origins refused, so it can draw a different frame from the same file."""
    inputs = a_project(tmp_path, cues=CUES)
    sealed = Inputs.load(tmp_path, environ={}, machine={"record": {"page_policy": "untrusted"}})
    page = inputs.document.page_sections[0].page
    assert inputs.still_key(page, "url") != sealed.still_key(page, "url")


def test_an_untrusted_project_draws_its_pages_untrusted(tmp_path: Path, opened: Opened) -> None:
    """A page `record` would sandbox must not reach the network through the storyboard instead."""
    a_project(tmp_path, cues=CUES)
    inputs = Inputs.load(tmp_path, environ={BY_ID["record.page_policy"].environment: "untrusted"})
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    storyboard(inputs, a_run(tmp_path))
    assert opened.policies == ["untrusted"]


@pytest.mark.browser
@pytest.mark.timeout(SPIN_CEILING_SECONDS)
def test_a_page_that_spins_its_main_thread_stops_the_storyboard_with_a_tool_error(tmp_path: Path) -> None:
    """A page whose script never yields cannot be raced from inside, so its page is closed from outside."""
    inputs = a_spinning_project(tmp_path)
    with pytest.raises(ToolError, match="stopped answering"):
        storyboard(inputs, a_run(tmp_path))
