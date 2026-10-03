"""The CLI reference is the index of the code pages, so every code in its tables links to its own page."""

from __future__ import annotations

import build_cli_reference
from decktalk.errors import ErrorCode
from decktalk.findings import Code


def test_every_error_and_finding_code_links_to_its_page() -> None:
    page = build_cli_reference.page()
    missing = [f"errors/{code.value}" for code in ErrorCode if f"](/reference/errors/{code.value})" not in page]
    missing += [f"findings/{code.value}" for code in Code if f"](/reference/findings/{code.value})" not in page]
    assert missing == []
