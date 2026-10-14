"""Frozen states drawn under one directory, and the contact sheet one writer lays out whichever command froze them."""

from __future__ import annotations

from pathlib import Path

import pytest

from decktalk.pictures import sheet as module
from decktalk.pictures.address import Freeze
from decktalk.pictures.sheet import Sheet, write_page
from decktalk.results import Panel
from support.fakes import Browsed
from support.pages import a_project, a_project_page
from support.runs import a_run


def test_a_sheet_reads_each_sections_words_and_the_documents_once_however_many_states_it_draws(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storyboard draws many states of one section, and each read is a file and a validation."""
    Browsed().install(monkeypatch)
    published = a_project(tmp_path)
    reads: list[str] = []
    monkeypatch.setattr(module, "spoken_query", lambda _inputs, section: reads.append(f"words {section.number}") or {})
    documents = published.documents
    monkeypatch.setattr(type(published), "documents", lambda self: reads.append("documents") or documents())
    pages = {"deck/index.html": a_project_page(published)}
    sheet = Sheet(published, a_run(tmp_path), pages, tmp_path / "out")
    section = published.document.page_sections[0]
    for freeze in (Freeze("1.1"), Freeze("1.1", cue="1.1:a"), Freeze("1.1")):
        sheet.frozen(section, freeze)
        sheet.panel(section, freeze, None, 0.0)
    assert reads == ["words 1", "documents"]


# ---- the contact sheet -------------------------------------------------------------------------


def test_the_page_names_every_panel_and_points_at_it(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    panels = [Panel(section=1, slide="1.1", cue="1.1:a", at_seconds=1.0, image=Path("build/storyboard/01/x.png"))]
    written = write_page(inputs.workspace, panels, title="demo")
    text = written.read_text(encoding="utf-8")
    assert 'src="storyboard/01/x.png"' in text
    assert "slide 1.1" in text
    assert "cue 1.1:a" in text


def test_markup_in_a_cue_or_a_slide_id_is_shown_as_text(tmp_path: Path) -> None:
    # A cue id and a slide id are whatever the project's author typed, and the sheet opens in a browser.
    inputs = a_project(tmp_path)
    hostile = "<script>alert(1)</script>"
    panels = [Panel(section=1, slide=f"1.1{hostile}", cue=f"1.1:{hostile}", at_seconds=1.0, image=Path("build/x.png"))]
    text = write_page(inputs.workspace, panels, title=hostile).read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_the_page_sits_beside_the_stills_it_lays_out(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    assert write_page(inputs.workspace, [], title="demo") == inputs.workspace.storyboard_path


def test_the_storyboard_names_the_voices_of_each_section(tmp_path: Path) -> None:
    inputs = a_project(tmp_path)
    panels = [
        Panel(section=1, slide="1.1", cue=None, at_seconds=0.0, image=Path("build/storyboard/01/a.png")),
        Panel(section=2, slide="2.1", cue=None, at_seconds=0.0, image=Path("build/storyboard/02/b.png")),
    ]
    text = write_page(inputs.workspace, panels, title="demo", voices={1: ("narrator", "guest")}).read_text(
        encoding="utf-8"
    )
    assert "0.00s &middot; read by narrator, guest</figcaption>" in text
    assert "section 2 &middot; slide 2.1 &middot; opening &middot; 0.00s</figcaption>" in text
