"""The docs link check reads every link a page holds, including one whose text wraps onto a second line."""

from __future__ import annotations

from pathlib import Path

import pytest

import check_docs_links


def test_a_link_whose_text_wraps_onto_a_second_line_is_still_read() -> None:
    """A page wraps a link's text wherever its line runs out, so the pattern takes a newline inside it."""
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


def _code_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, index: str) -> dict[str, check_docs_links.Page]:
    """A site of an index page and one error page, with the index page's body as given."""
    (tmp_path / "reference" / "errors").mkdir(parents=True)
    (tmp_path / "reference" / "errors" / "INPUT.mdx").write_text("---\ntitle: INPUT\ndescription: Bad.\n---\n")
    (tmp_path / "reference" / "cli.mdx").write_text(f"---\ntitle: CLI\ndescription: All.\n---\n\n{index}\n")
    monkeypatch.setattr(check_docs_links, "DOCS", tmp_path)
    return check_docs_links.read_pages()


def test_a_code_page_the_cli_reference_links_needs_no_navigation_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code tables in the CLI reference are the index of the code pages, so the sidebar stays short."""
    pages = _code_pages(tmp_path, monkeypatch, "| [`INPUT`](/reference/errors/INPUT) | Bad. |")
    assert check_docs_links.navigation_problems(pages, ["reference/cli"]) == []


def test_a_code_page_the_cli_reference_does_not_link_is_reached_by_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = _code_pages(tmp_path, monkeypatch, "| `INPUT` | Bad. |")
    assert check_docs_links.navigation_problems(pages, ["reference/cli"]) == [
        "docs/docs.json: reference/errors/INPUT is in no navigation group, and reference/cli does not link it"
    ]
