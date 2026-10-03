"""The docs-sample generator's own rules: where a block goes, how it is printed, and what `--check` holds.

The runs themselves need ffmpeg, so the committed samples are held by the generator's own `--check`
in the e2e row, and these tests cover what it does around a run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import build_samples
from support.paths import REPO

PAGE = REPO / "docs" / "page.mdx"


def test_a_sample_comment_and_its_block_are_written_and_every_other_line_is_left_alone() -> None:
    page = "Prose.\n\n{/* sample: one, an old sentence */}\n```json old.json\n{}\n```\n\nMore prose.\n"
    written = build_samples.spliced(
        page, {"one": ("{/* sample: one, a run */}", "```json new.json\n[]\n```\n")}, where=PAGE
    )
    assert written == "Prose.\n\n{/* sample: one, a run */}\n```json new.json\n[]\n```\n\nMore prose.\n"


def test_a_sample_comment_inside_a_step_keeps_its_indent() -> None:
    page = "  {/* sample: one */}\n```json a.json\n{}\n```\n"
    written = build_samples.spliced(
        page, {"one": ("{/* sample: one, a run */}", "```json a.json\n[]\n```\n")}, where=PAGE
    )
    assert written.startswith("  {/* sample: one, a run */}\n")


def test_a_comment_no_row_names_is_left_as_it_is() -> None:
    page = "{/* sample: decktalk status, on a fresh starter */}\n```text\nold\n```\n"
    assert build_samples.spliced(page, {}, where=PAGE) == page


@pytest.mark.parametrize(
    ("page", "said"),
    [
        ("{/* sample: one */}\nProse where the block should be.\n", "has no fenced block under it"),
        ("{/* sample: one */}\n```json a.json\n{}\n", "has a block that never closes"),
    ],
    ids=["no block", "no closing fence"],
)
def test_a_sample_with_no_whole_block_under_it_is_refused(page: str, said: str) -> None:
    with pytest.raises(SystemExit, match=said):
        build_samples.spliced(page, {"one": ("{/* sample: one */}", "```json\n{}\n```\n")}, where=PAGE)


def test_an_object_of_scalars_is_one_line_where_it_fits_and_indented_where_it_does_not() -> None:
    short = {"word": "This", "start": 0.0, "end": 0.333}
    long = {"spoken": "x" * build_samples.LINE_WIDTH}
    assert build_samples.dumped({"words": [short], "row": long}) == (
        "{\n"
        '  "words": [\n'
        '    { "word": "This", "start": 0.0, "end": 0.333 }\n'
        "  ],\n"
        '  "row": {\n'
        f'    "spoken": "{"x" * build_samples.LINE_WIDTH}"\n'
        "  }\n"
        "}"
    )


def test_a_value_the_encoder_decides_is_held_by_its_key_alone() -> None:
    one = '{ "bytes": 101791, "blake3": "fffe804f5695880d", "words": 3 }'
    other = '{ "bytes": 99000, "blake3": "0123456789abcdef", "words": 3 }'
    assert build_samples.masked(one, ("bytes", "blake3")) == build_samples.masked(other, ("bytes", "blake3"))
    assert build_samples.masked(one, ("bytes",)) != build_samples.masked(other, ("bytes",))
    assert build_samples.masked(one, ("bytes", "blake3")) != build_samples.masked(
        one.replace("3 }", "4 }"), ("bytes", "blake3")
    )


def test_the_fake_voice_times_every_word_at_its_pace_with_each_pause_after_its_piece() -> None:
    words, seconds = build_samples.timed([{"text": "A bowl.", "pause": 0.5}, {"text": "It rolls.", "pause": None}])
    step = build_samples.WORD_SECONDS
    assert [row["word"] for row in words] == ["A", "bowl.", "It", "rolls."]
    assert words[2]["start"] == round(2 * step + 0.5, 3)
    assert seconds == pytest.approx(4 * step + 0.5)


def test_every_row_names_a_page_that_marks_it() -> None:
    for row in build_samples.rows():
        assert isinstance(row.page, Path) and row.page.is_file(), row.page
        marked = row.page.read_text(encoding="utf-8")
        assert f"{{/* sample: {row.id}, {row.what} */}}" in marked, f"{row.page.name} has no comment for {row.id}"
