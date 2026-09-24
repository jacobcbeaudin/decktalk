"""What the command line writes: the finding line, the error block, and the tables per result."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from decktalk.cli import output
from decktalk.errors import ErrorCode, ErrorInfo, InputError
from decktalk.events import Level, Log, Progress, RunStart, StageDone
from decktalk.findings import Certainty, Code
from decktalk.pipeline import Outcome, Stage
from decktalk.results import RESULTS, ErrorResult, StatusResult

from .conftest import finding


def written(render, *args: object) -> str:
    """Whatever one renderer put on its console, as plain text."""
    console = Console(file=io.StringIO(), width=100, no_color=True)
    render(*args, console)
    return console.file.getvalue()  # ty: ignore[unresolved-attribute]


def test_a_finding_line_carries_its_place_its_code_and_its_sentence() -> None:
    line = written(output.finding_lines, (finding(),))
    assert "cues.json:14:" in line
    assert Code.CUE_UNRESOLVED.value in line
    assert "is not spoken in section 2" in line


def test_a_fix_is_printed_under_its_finding_with_its_applicability() -> None:
    line = written(output.finding_lines, (finding(fix=True),))
    assert "fix (safe):" in line


def test_the_count_line_says_how_many_and_how_many_are_fixable() -> None:
    line = written(output.finding_lines, (finding(fix=True), finding(Code.CUE_THIN_CHANGE)))
    assert "Found 2 findings, 1 certain." in line
    assert "1 fixable with --fix." in line


def test_one_finding_is_counted_in_the_singular() -> None:
    assert "Found 1 finding, 1 certain." in written(output.finding_lines, (finding(),))


def test_the_error_block_carries_the_code_the_sentence_the_hint_and_the_page() -> None:
    info = ErrorInfo.of(InputError("decktalk.toml is not valid TOML.", hint="Fix line 59, then run decktalk status."))
    block = written(output.error_block, info)
    assert block.startswith("error[INPUT]: decktalk.toml is not valid TOML.")
    assert "  hint: Fix line 59, then run decktalk status." in block
    assert f"  docs: {ErrorCode.INPUT.url}" in block


def test_an_uncertain_finding_is_not_counted_as_certain() -> None:
    soft = finding(Code.CUE_THIN_CHANGE)
    assert soft.certainty is Certainty.UNCERTAIN
    assert "0 certain" in written(output.finding_lines, (soft,))


@pytest.mark.parametrize("name", sorted(output.RENDERERS, key=lambda model: model.__name__))
def test_every_renderer_is_for_a_published_result(name) -> None:
    assert name in set(RESULTS.values())


def test_a_result_with_no_renderer_still_prints_its_findings() -> None:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    output.render(ErrorResult(ok=False, findings=(finding(),)), console)
    assert Code.CUE_UNRESOLVED.value in console.file.getvalue()  # ty: ignore[unresolved-attribute]


def test_the_plain_lines_renderer_writes_one_line_per_stage_that_ended() -> None:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    lines = output.Lines(console)
    lines(_stage_done())
    lines(
        Progress(
            event="progress",
            time=_now(),
            seq=1,
            run="r",
            stage=Stage.RECORD,
            done=1,
            total=3,
            unit="section",
            label="a section",
        )
    )  # ty: ignore[invalid-argument-type]
    assert console.file.getvalue().count("\n") == 1  # ty: ignore[unresolved-attribute]
    assert "Record" in console.file.getvalue()  # ty: ignore[unresolved-attribute]


def test_the_events_renderer_writes_the_library_s_own_line() -> None:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    output.Jsonl(console)(_stage_done())
    assert '"event":"stage.done"' in console.file.getvalue()  # ty: ignore[unresolved-attribute]


def test_a_debug_line_is_written_under_verbose_alone() -> None:
    quiet = Console(file=io.StringIO(), width=100, no_color=True)
    output.Notes(quiet, verbose=False, quiet=False)(_log(Level.DEBUG))
    assert quiet.file.getvalue() == ""  # ty: ignore[unresolved-attribute]
    loud = Console(file=io.StringIO(), width=100, no_color=True)
    output.Notes(loud, verbose=True, quiet=False)(_log(Level.DEBUG))
    assert "a debug line" in loud.file.getvalue()  # ty: ignore[unresolved-attribute]


def test_a_warning_survives_quiet() -> None:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    output.Notes(console, verbose=False, quiet=True)(_log(Level.WARNING))
    assert "a debug line" in console.file.getvalue()  # ty: ignore[unresolved-attribute]


def test_the_opening_line_names_the_run_and_its_events_file_once() -> None:
    console = Console(file=io.StringIO(), width=100, no_color=True)
    opening = output.Opening(console)
    line = RunStart(event="run.start", time=_now(), seq=0, run="abc", events_path=Path("build/events/abc.jsonl"))
    opening(line)
    opening(line)
    assert console.file.getvalue().count("run abc") == 1  # ty: ignore[unresolved-attribute]


def test_a_status_table_names_every_column_a_reader_scans() -> None:
    console = Console(file=io.StringIO(), width=120, no_color=True)
    output.render(
        StatusResult(ok=True, run="r", name="demo", script=Path("script.md"), cues=Path("cues.json"), sections=()),
        console,
    )
    assert "Section" in console.file.getvalue()  # ty: ignore[unresolved-attribute]


def _stage_done() -> StageDone:
    """One stage that ended, which is the moment both stage renderers print."""
    return StageDone(
        event="stage.done", time=_now(), seq=0, run="r", stage=Stage.RECORD, outcome=Outcome.OK, seconds=58.0
    )


def _log(level: Level) -> Log:
    """One line the library would have printed, at the level a test is about."""
    return Log(event="log", time=_now(), seq=0, run="r", level=level, message="a debug line")


def _now() -> datetime:
    """One instant, which every event carries and no assertion here reads."""
    return datetime.now(UTC)
