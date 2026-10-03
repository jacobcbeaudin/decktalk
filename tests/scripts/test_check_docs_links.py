"""The docs link check reads every link a page holds, including one whose text wraps onto a second line."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_docs_links


def test_a_link_whose_text_wraps_onto_a_second_line_is_still_read() -> None:
    """Five wrapped links went unchecked while the pattern refused a newline inside a link's text."""
    body = "See [the reference for\nthe card](/reference/card) and [one line](/quickstart)."
    assert [m["target"] for m in check_docs_links.MD_LINK.finditer(body)] == ["/reference/card", "/quickstart"]


def test_a_markdown_page_must_be_in_the_navigation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The site serves a .md file under docs/ as a page, so one left out of the navigation fails like an .mdx."""
    (tmp_path / "decisions").mkdir()
    (tmp_path / "decisions" / "a-choice.md").write_text("---\ntitle: A choice\ndescription: Why.\n---\n\nBody.\n")
    (tmp_path / "index.mdx").write_text("---\ntitle: Home\ndescription: Start.\n---\n\nBody.\n")
    monkeypatch.setattr(check_docs_links, "DOCS", tmp_path)
    pages = check_docs_links.read_pages()
    assert set(pages) == {"index", "decisions/a-choice"}
    assert check_docs_links.navigation_problems(pages, ["index"]) == [
        "docs/docs.json: decisions/a-choice is a page and is in no navigation group"
    ]
