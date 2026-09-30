"""The docs link check reads every link a page holds, including one whose text wraps onto a second line."""

from __future__ import annotations

import check_docs_links


def test_a_link_whose_text_wraps_onto_a_second_line_is_still_read() -> None:
    """Five wrapped links went unchecked while the pattern refused a newline inside a link's text."""
    body = "See [the reference for\nthe card](/reference/card) and [one line](/quickstart)."
    assert [m["target"] for m in check_docs_links.MD_LINK.finditer(body)] == ["/reference/card", "/quickstart"]
