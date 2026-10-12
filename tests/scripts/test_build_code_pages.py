"""The code pages say where each finding comes from, read off the code's own row."""

from __future__ import annotations

import pytest
import yaml

import build_code_pages
from decktalk.errors import ErrorCode
from decktalk.findings import CHECK, REPORTED_WHEN, Code


@pytest.mark.parametrize("code", list(Code), ids=str)
def test_a_finding_page_names_every_command_that_reports_it_and_when(code: Code) -> None:
    page = build_code_pages.finding_page(code)
    for command in code.raised_in:
        assert f"`decktalk {command}`" in page, command
        assert REPORTED_WHEN[command] in page, command


def test_a_code_check_judges_from_files_is_not_said_to_come_from_a_run() -> None:
    page = build_code_pages.finding_page(Code.PROGRAM_NAMED)
    assert "what the run produced" not in page
    assert f"`decktalk {CHECK}` reports it {REPORTED_WHEN[CHECK]}." in page


def test_every_page_front_matter_reads_back_as_its_title_and_sentence() -> None:
    """A sentence may hold a colon, so the front matter carries it in a form YAML reads back unchanged."""
    for path, text in build_code_pages.documents().items():
        if path == build_code_pages.RUNTIME:
            continue
        front = yaml.safe_load(text.split("---\n")[1])
        assert front == {"title": path.stem, "description": text.split("\n\n")[2]}, path.name


@pytest.mark.parametrize("code", [*Code, *ErrorCode], ids=str)
def test_every_code_page_ends_with_what_to_do(code: Code | ErrorCode) -> None:
    """A printed `docs:` link lands here first, so the page says what to change and not only what went wrong."""
    page = build_code_pages.finding_page(code) if isinstance(code, Code) else build_code_pages.error_page(code)
    head, _, what_to_do = page.partition("\n## What to do\n")
    assert what_to_do, code.name
    assert " ".join(code.remedy.split()) in " ".join(what_to_do.split()), code.name
    for title, anchor in build_code_pages.troubleshooting(code):
        assert f"[{title}](/help/troubleshooting#{anchor})" in what_to_do, code.name


def test_the_troubleshooting_rows_name_a_section_for_every_code_they_list() -> None:
    """The map is read off the troubleshooting page's own symptom table, so a code it lists gets its link."""
    listed = {code for code in [*Code, *ErrorCode] if build_code_pages.troubleshooting(code)}
    assert Code.PROGRAM_NAMED in listed
    assert ErrorCode.TOOL in listed
    assert ("A missing toolchain", "a-missing-toolchain") in build_code_pages.troubleshooting(ErrorCode.TOOL)
