"""`ProgressRow`: one line of the log a build keeps, written by the run and read by `status`."""

from __future__ import annotations

import json

from decktalk.artifacts import ProgressRow, append_row, read_rows, start_log
from decktalk.jsonio import as_json
from decktalk.pipeline import ProgressEvent, Stage


def a_row(event: ProgressEvent, section: int | None = None) -> ProgressRow:
    return ProgressRow(
        ts="2026-09-18T20:33:01.470Z", pid=41, stage=Stage.RECORD, stage_index=3, stage_count=5,
        section=section, event=event, detail="Recorded the section: ok.",
    )  # fmt: skip


def test_a_row_is_one_json_line_in_the_order_the_contract_lists_its_keys(tmp_path):
    path = tmp_path / "build" / "progress.jsonl"
    start_log(path)
    append_row(path, a_row(ProgressEvent.DONE, section=3))
    [line] = path.read_text(encoding="utf-8").splitlines()
    written = json.loads(line)
    assert list(written) == ["ts", "pid", "stage", "stage_index", "stage_count", "section", "event", "detail"]
    assert (written["stage"], written["event"]) == (Stage.RECORD.value, ProgressEvent.DONE.value)
    assert read_rows(path) == [a_row(ProgressEvent.DONE, section=3)]


def test_a_new_run_empties_the_log_of_the_run_before(tmp_path):
    path = tmp_path / "progress.jsonl"
    path.write_text(json.dumps(as_json(a_row(ProgressEvent.FAIL))) + "\n", encoding="utf-8")
    start_log(path)
    assert path.read_text(encoding="utf-8") == "" and read_rows(path) == []


def test_a_torn_line_and_a_line_that_is_no_row_are_skipped_and_a_missing_log_is_no_rows(tmp_path):
    """The writer appends while a reader reads, so the last line can arrive half written."""
    path = tmp_path / "progress.jsonl"
    assert read_rows(path) == []
    start_log(path)
    append_row(path, a_row(ProgressEvent.START))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({**as_json(a_row(ProgressEvent.DONE)), "stage": "rehearse"}) + "\n")
        fh.write('{"ts": "2026-09-18T20:33')
    assert read_rows(path) == [a_row(ProgressEvent.START)]
