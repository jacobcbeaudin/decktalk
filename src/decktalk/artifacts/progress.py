"""`ProgressRow`, one line of the log a build keeps of itself.

    build/progress.jsonl   one JSON object per event, appended as the run goes

A build truncates the file when it starts and appends one row per event, flushing each as it is
written, so a reader that opens the file mid-run sees every event so far and nothing from an earlier
run. The writer may be half way through a line when a reader opens the file, so `read_rows` skips a
line that is not a whole row rather than refusing the log, and a caller that needs the run's state
reads the rows that are there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..jsonio import ShapeError, as_json, read_as
from ..pipeline import ProgressEvent, Stage


@dataclass(frozen=True)
class ProgressRow:
    """One event of a run: what happened, to which stage and section, when, and in which process."""

    ts: str  # The event time, ISO 8601 in UTC.
    pid: int  # The process that wrote the row, which is how a reader tells a live run from a dead one.
    stage: Stage
    stage_index: int  # The stage's place in this run, counting from 1.
    stage_count: int  # How many stages this run executes.
    section: int | None
    event: ProgressEvent
    detail: str | None  # One sentence, because a reader relays it to a person.


def start_log(path: Path) -> None:
    """Empty the log for a new run, so it only ever describes the run in front of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def append_row(path: Path, row: ProgressRow) -> None:
    """Append one row and flush it, so a reader sees it the moment it happened."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(as_json(row)) + "\n")
        fh.flush()


def read_rows(path: Path) -> list[ProgressRow]:
    """Every whole row of the log in the order written, or none when no build has written one here."""
    if not path.exists():
        return []
    rows: list[ProgressRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(read_as(ProgressRow, json.loads(line)))
        except (json.JSONDecodeError, ShapeError):
            continue
    return rows
