"""Every slide at every cue, frozen onto one page, which is what a person reads before they spend."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from decktalk.errors import Cancel
from decktalk.inputs import Inputs
from decktalk.machine import Machine, Run, Toolchain
from decktalk.media.pagereport import MeasuredScene, PageReport
from decktalk.page import Q
from decktalk.results import Panel, StoryboardResult
from decktalk.settings import BY_ID
from decktalk.stages import storyboard as stage
from decktalk.stages.storyboard import (
    Freeze,
    Selection,
    freeze_url,
    panels_of,
    slide_cues,
    storyboard,
    write_page,
)

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
"""

CUES = {"1": {"cues": [{"cue": "1.1:a", "on": "there"}]}}
"""One cue whose phrase the demo script really speaks, so it resolves to a second."""

SCRIPT = "# Demo\n\n## 1. One\n\nHello there again.\n\n## 2. Two\n\nSecond section speaks as well.\n"

BOX = {"x": 0, "y": 0, "w": 10, "h": 10}


def a_run(root: Path) -> Run:
    machine = Machine(environ={}, tables={}, config_path=root / "m.toml", cwd=root, toolchain=Toolchain())
    return Run(machine, id="r1", cancel=Cancel(), root=root)


def a_project(tmp_path: Path, *, cues: dict | None = None) -> Inputs:
    (tmp_path / "deck").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deck" / "index.html").write_text("<div data-scene='1'></div>", encoding="utf-8")
    (tmp_path / "decktalk.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "script.md").write_text(SCRIPT, encoding="utf-8")
    if cues is not None:
        (tmp_path / "cues.json").write_text(json.dumps({"sections": cues}, indent=2), encoding="utf-8")
    return Inputs.load(tmp_path, environ={})


def catalog(scene: str, moments: dict[str, list[str]]) -> dict:
    elements = {
        slide: [{"attrs": {}, "moments": {"data-in": wire}, "text": "x", "box": BOX} for wire in wires]
        for slide, wires in moments.items()
    }
    return {"scene": scene, "elements": elements, "slides": list(moments), "cues": dict(moments)}


@dataclass
class Served:
    """The project files the router answered the page with, which is what a kept still is checked against."""

    paths: list[str] = field(default_factory=list)


@dataclass
class Opened:
    """Every URL a run pointed a page at, and what each page of the project publishes."""

    served: Served = field(default_factory=Served)
    urls: list[str] = field(default_factory=list)
    shots: list[Path] = field(default_factory=list)
    reports: dict[str, PageReport] = field(default_factory=dict)
    policies: list[str] = field(default_factory=list)

    def publishes(self, page: str, *scenes: dict) -> None:
        self.reports[page] = PageReport.model_validate({"catalog": list(scenes)})


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> Opened:
    """The browser seams `storyboard` reads, replaced so the stage runs whole and opens nothing."""
    made = Opened()

    @contextmanager
    def chromium(_browser_path: str = "", *, policy: str) -> Iterator[object]:
        made.policies.append(policy)
        yield object()

    def open_page(*_args: object, **_kwargs: object) -> tuple[object, Served]:
        return object(), made.served

    def reports_of(_page: object, _inputs: Inputs, files: Sequence[str]) -> dict[str, PageReport]:
        return {name: made.reports[name] for name in dict.fromkeys(files) if name in made.reports}

    def screenshot(_page: object, url: str, out: Path, **_kwargs: object) -> None:
        made.urls.append(url)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")
        made.shots.append(out)

    monkeypatch.setattr(stage, "chromium", chromium)
    monkeypatch.setattr(stage, "open_page", open_page)
    monkeypatch.setattr(stage, "reports_of", reports_of)
    monkeypatch.setattr(stage, "screenshot", screenshot)
    return made


# ---- the vocabulary of a frozen state ----------------------------------------------------------


def test_a_frozen_state_asks_the_page_for_a_slide_and_a_moment() -> None:
    assert Freeze("1.1", cue="1.1:a").query() == {Q.SLIDE: "1.1", Q.AFTER: "1.1:a"}
    assert Freeze("1.1", before="1.1:a").query() == {Q.SLIDE: "1.1", Q.BEFORE: "1.1:a"}
    assert Freeze("1.1").query() == {Q.SLIDE: "1.1"}


def test_a_frozen_state_names_one_file_safely() -> None:
    assert Freeze("4.1", cue="4.1:expand").label == "slide-4.1-after-4.1_expand"


def test_a_scene_declares_its_slides_and_the_cues_it_lists_against_each() -> None:
    entry = MeasuredScene.model_validate(catalog("1", {"1.1": ["1.1:a", "1.1:b"]}))
    assert slide_cues(entry) == {"1.1": ("1.1:a", "1.1:b")}


def test_a_page_that_published_no_such_scene_declares_nothing() -> None:
    assert slide_cues(None) is None


def test_a_scene_that_lists_no_cues_falls_back_to_the_moments_its_elements_name() -> None:
    entry = MeasuredScene.model_validate(
        {"scene": "1", "elements": {"1.1": [{"attrs": {}, "moments": {"data-in": "1.1:a"}, "text": "", "box": BOX}]}}
    )
    assert slide_cues(entry) == {"1.1": ("1.1:a",)}


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


def test_a_frozen_url_asks_for_a_state_and_never_for_the_recorder_clock(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    url = freeze_url(inputs, inputs.document.page_sections[0], Freeze("1.1", cue="1.1:a"))
    assert "slide=1.1" in url
    assert "after=1.1" in url
    assert "t0=" not in url


# ---- the contact sheet -------------------------------------------------------------------------


def test_the_page_names_every_panel_and_points_at_it(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    panels = [Panel(section=1, slide="1.1", cue="1.1:a", at=1.0, image=Path("build/storyboard/01/x.png"))]
    written = write_page(inputs.workspace, panels, title="demo")
    text = written.read_text(encoding="utf-8")
    assert 'src="storyboard/01/x.png"' in text
    assert "slide 1.1" in text
    assert "cue 1.1:a" in text


def test_markup_in_a_cue_or_a_slide_id_is_shown_as_text(tmp_path: Path) -> None:
    # A cue id and a slide id are whatever the project's author typed, and the sheet opens in a browser.
    inputs = a_project(tmp_path)
    hostile = "<script>alert(1)</script>"
    panels = [Panel(section=1, slide=f"1.1{hostile}", cue=f"1.1:{hostile}", at=1.0, image=Path("build/x.png"))]
    text = write_page(inputs.workspace, panels, title=hostile).read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_the_page_sits_beside_the_stills_it_lays_out(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    assert write_page(inputs.workspace, [], title="demo") == inputs.workspace.storyboard_path


# ---- the stage ---------------------------------------------------------------------------------


def test_it_draws_one_still_per_moment_and_writes_the_sheet(tmp_path: Path, opened: Opened) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    result = storyboard(inputs, a_run(tmp_path))
    assert isinstance(result, StoryboardResult)
    assert result.storyboard == Path("build/storyboard.html")
    assert len(result.panels) == len(opened.shots)
    assert (tmp_path / "build" / "storyboard.html").is_file()


def test_it_judges_nothing_at_all(tmp_path: Path, opened: Opened) -> None:
    """A storyboard draws what the film will show, and what it shows is for a person to judge."""
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    result = storyboard(inputs, a_run(tmp_path))
    assert result.findings == ()
    assert result.ok is True


def test_a_selection_draws_the_sections_it_names_and_no_others(tmp_path: Path, opened: Opened) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    result = storyboard(inputs, a_run(tmp_path), only=[2])
    assert {panel.section for panel in result.panels} == {2}


def test_the_four_selectors_reach_the_sheet_through_the_stage(tmp_path: Path, opened: Opened) -> None:
    """The selectors are the stage's own arguments, so a caller narrows the sheet without a second call."""
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    result = storyboard(inputs, a_run(tmp_path), slide=["1.1"], after=["1.1:a"])
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


def test_every_still_it_wrote_is_reported(tmp_path: Path, opened: Opened) -> None:
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    result = storyboard(inputs, a_run(tmp_path))
    assert Path("build/storyboard.html") in result.written
    assert all(str(one).startswith("build/") for one in result.written)


# ---- frames kept by what drew them -------------------------------------------------------------


def test_a_second_storyboard_reads_every_still_back_and_draws_none(tmp_path: Path, opened: Opened) -> None:
    """A repeat storyboard with nothing changed falls to the catalog read, which is all a page must do."""
    inputs = a_project(tmp_path, cues=CUES)
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    first = storyboard(inputs, a_run(tmp_path))
    drawn = len(opened.shots)
    again = storyboard(inputs, a_run(tmp_path))
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
    opened.served.paths.append("deck/hero.png")
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


def test_an_untrusted_project_draws_its_pages_untrusted(tmp_path: Path, opened: Opened) -> None:
    """A page `record` would sandbox must not reach the network through the storyboard instead."""
    a_project(tmp_path, cues=CUES)
    inputs = Inputs.load(tmp_path, environ={BY_ID["record.page_policy"].environment: "untrusted"})
    opened.publishes("deck/index.html", catalog("1", {"1.1": ["1.1:a"]}), catalog("2", {"2.1": ["2.1:a"]}))
    storyboard(inputs, a_run(tmp_path))
    assert opened.policies == ["untrusted"]
