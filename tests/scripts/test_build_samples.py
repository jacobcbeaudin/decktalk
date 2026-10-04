"""The docs-sample generator's own rules: where a block goes, how it is printed, and what `--check` holds.

The runs themselves need ffmpeg, so the committed samples are held by the generator's own `--check`
in the e2e row, and these tests cover what it does around a run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import build_samples
from decktalk.findings import Code
from support.paths import REPO

CUE_OFF = Code.CUE_OFF.value
"""A finding a slower machine can add to a verified run, which a measured row leaves out."""

TAKE_MISSING = Code.TAKE_MISSING.value
"""A finding every run without spend reports, which a measured row keeps."""

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


def test_a_verdict_the_run_decides_is_held_by_its_key_alone() -> None:
    one = '{ "ok": true, "error": null }'
    other = '{ "ok": false, "error": null }'
    assert build_samples.masked(one, ("ok",)) == build_samples.masked(other, ("ok",))


SLOW_BUILD = """\
      Verify    0:06
       Built build/final/my-lesson.mp4, $0.00, 2 findings
script.md: TAKE_MISSING Section 1 plays a placeholder, because it has no voiced take yet.
build/final/my-lesson.mp4: CUE_OFF the reveal at 1.2:script first changed +163 ms from the word it lands on.
Found 2 findings, 1 error."""


def test_a_measured_finding_a_slower_machine_adds_is_left_out_of_the_block() -> None:
    """A recording is made in real time, so a starved machine can land a reveal late where the docs machine did not."""
    shown = build_samples.unmeasured(SLOW_BUILD, (CUE_OFF,))
    assert CUE_OFF not in shown
    assert TAKE_MISSING in shown
    assert build_samples.unmeasured(SLOW_BUILD, ()) == SLOW_BUILD


def test_a_measured_finding_is_left_out_of_a_json_result_and_every_other_finding_stays() -> None:
    result = {"ok": False, "findings": [{"code": CUE_OFF}, {"code": TAKE_MISSING}]}
    assert build_samples.unmeasured_value(result, (CUE_OFF,)) == {"ok": False, "findings": [{"code": TAKE_MISSING}]}


def test_a_findings_exit_is_a_measured_one_only_when_every_error_it_names_is_measured() -> None:
    assert build_samples.only_measured(SLOW_BUILD, (CUE_OFF,))
    assert not build_samples.only_measured(SLOW_BUILD, ())
    assert not build_samples.only_measured(SLOW_BUILD + "\nscript.md: SCRIPT_UNFINISHED a placeholder.", (CUE_OFF,))
    assert not build_samples.only_measured("Found 1 finding, 0 errors.", (CUE_OFF,)), "an exit names its error"


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


def test_a_block_under_an_indented_comment_is_indented_with_it() -> None:
    """A sample inside a Step sits four spaces in, and its block has to sit there with it to render in the Step."""
    page = "    {/* sample: one */}\n    ```text\n    old\n\n    lines\n    ```\nProse.\n"
    written = build_samples.spliced(
        page, {"one": ("{/* sample: one, a run */}", "```text\nnew\n\nlines\n```\n")}, where=PAGE
    )
    assert written == "    {/* sample: one, a run */}\n    ```text\n    new\n\n    lines\n    ```\nProse.\n"


def test_a_list_of_scalars_is_one_line_where_it_fits() -> None:
    assert build_samples.dumped({"sections": [1, 2, 3], "words": []}) == (
        '{\n  "sections": [1, 2, 3],\n  "words": []\n}'
    )


def test_printed_text_keeps_the_lines_a_row_picks_with_no_trailing_space_or_blank_edge() -> None:
    printed = "\n \n Section   Key  \n ──────── \n 1   01  \n   \nFilm   x.mp4\n\n"
    assert (
        build_samples.shown_text(printed, head=None, grep=None) == " Section   Key\n ────────\n 1   01\n\nFilm   x.mp4"
    )
    assert build_samples.shown_text(printed, head=3, grep=None) == " Section   Key"
    assert build_samples.shown_text("a: X one\nb\nc: X two\n", head=None, grep=r": X ") == "a: X one\nc: X two"


def test_a_json_answer_is_cut_to_the_part_a_row_shows() -> None:
    answer = {
        "ok": False,
        "error": None,
        "sections": [{"section": 1, "cues": [1, 2, 3]}, {"section": 2, "cues": []}],
        "findings": [{"code": "A_CODE", "fix": {"kind": "edit"}}],
    }
    assert build_samples.cut(answer, key="findings.0.fix") == {"kind": "edit"}
    assert build_samples.cut(answer, only=("ok", "error")) == {"ok": False, "error": None}
    assert build_samples.cut(answer, keep=(("sections", 1), ("sections.0.cues", 2))) == {
        **answer,
        "sections": [{"section": 1, "cues": [1, 2]}],
    }


def test_a_fragment_is_written_under_its_own_key() -> None:
    assert build_samples.fragment("sections.4", {"cues": []}) == '"4": {\n  "cues": []\n}'


def test_a_steps_edit_and_delete_change_the_project_between_commands(tmp_path: Path) -> None:
    (tmp_path / "cues.json").write_text('{"phrase": "the whole idea"}', encoding="utf-8")
    (tmp_path / "gone.txt").write_text("x", encoding="utf-8")
    build_samples.apply(build_samples.Edit("cues.json", "the whole idea", "Change a word"), tmp_path, row="one")
    build_samples.apply(build_samples.Delete("gone.txt"), tmp_path, row="one")
    assert (tmp_path / "cues.json").read_text(encoding="utf-8") == '{"phrase": "Change a word"}'
    assert not (tmp_path / "gone.txt").exists()
    with pytest.raises(SystemExit, match="no longer says"):
        build_samples.apply(build_samples.Edit("cues.json", "the whole idea", "x"), tmp_path, row="one")


def test_a_row_masks_only_its_own_block() -> None:
    """A mask for one sample's timings leaves every other number on the page held exactly."""
    masks: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "one": ((), (r"\d:\d\d",)),
        "two": (("run",), (r"[0-9a-f]{12}",)),
    }
    page = (
        "Record 0:22 in prose.\n"
        "{/* sample: one, a build */}\n```text\nRecord    0:22\n```\n"
        "{/* sample: two, a status */}\n```jsonl build/events/3ffc9cf27001.jsonl\n"
        '{"run":"3ffc9cf27001", "seq": 0}\n```\n'
    )
    other = page.replace("Record    0:22", "Record    0:31").replace("3ffc9cf27001", "0208b197a5c7")
    held = build_samples.masked_page(page, masks)
    assert build_samples.masked_page(other, masks) == held
    assert build_samples.masked_page(page.replace("Record 0:22 in", "Record 0:23 in"), masks) != held
    assert build_samples.masked_page(page.replace('"seq": 0', '"seq": 1'), masks) != held
