"""What the command line writes: the finding line, the error block, and the tables per result."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from decktalk.cli import output
from decktalk.errors import ErrorCode, ErrorInfo, InputError
from decktalk.events import Level, Log, Progress, RunStart, StageDone, StageStart
from decktalk.findings import Certainty, Code
from decktalk.pipeline import Outcome, Stage
from decktalk.results import (
    RESULTS,
    BuildResult,
    CheckResult,
    ClipResult,
    ConfigGetResult,
    ConfigListResult,
    ConfigSetResult,
    ConfigUnsetResult,
    CueCheck,
    ErrorResult,
    Layer,
    Scope,
    SettingValue,
    StatusResult,
    VerifyResult,
    Voicing,
)

from .conftest import finding, spend


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


def test_a_note_one_command_already_printed_is_not_printed_by_its_second_judgement() -> None:
    """`check --fix` judges twice, and the second judgement says what the first already said."""
    console = Console(file=io.StringIO(), width=100, no_color=True)
    heard: set[str] = set()
    for _ in range(2):
        output.Notes(console, verbose=False, quiet=False, heard=heard)(_log(Level.INFO))
    assert console.file.getvalue().count("a debug line") == 1  # ty: ignore[unresolved-attribute]


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


# What a person reads in a terminal. Each test feeds one result or one stream of events into a
# recording console and asserts the rows and the words a reader scans, never the whole text, so a
# change of spacing is not a failure and a lost column or a wrong count is.


def recorded(result: object, width: int = 120) -> str:
    """One result as a terminal would show it, read back from a recording console."""
    console = Console(record=True, width=width, no_color=True, file=io.StringIO())
    output.render(result, console)  # ty: ignore[invalid-argument-type]
    return console.export_text()


def test_the_live_region_shows_each_stage_its_progress_and_its_time() -> None:
    console = Console(record=True, width=100, no_color=True, file=io.StringIO(), force_terminal=True)
    region = output.Region(console)
    region.open()
    region(StageStart(event="stage.start", time=_now(), seq=0, run="r", stage=Stage.NARRATE, index=1, count=6))
    region(_stage_done())
    region(
        Progress(
            event="progress",
            time=_now(),
            seq=2,
            run="r",
            stage=Stage.ASSEMBLE,
            done=2,
            total=5,
            unit="section",
            label="section 2",
        )
    )  # ty: ignore[invalid-argument-type]
    region.close()
    shown = console.export_text()
    assert "Narrate" in shown
    assert "Record" in shown
    assert "0:58" in shown
    assert "2/5" in shown


def test_a_finished_build_names_its_film_its_price_and_what_it_found() -> None:
    built = BuildResult(
        ok=True,
        run="r",
        stages=(),
        voice=Voicing.PLACEHOLDER,
        spend=spend(),
        film=Path("build/final/demo.mp4"),
        findings=(finding(),),
        seconds=1.0,
    )
    said = recorded(built)
    assert "Built build/final/demo.mp4, $0.12, 1 finding" in said
    assert "1 findings" not in said


def test_a_build_that_stopped_says_where_it_stopped() -> None:
    stopped = BuildResult(
        ok=False,
        run="r",
        stages=(),
        voice=Voicing.PLACEHOLDER,
        spend=spend(),
        stopped_at=Stage.CUE,
        findings=(finding(), finding()),
        seconds=1.0,
    )
    said = recorded(stopped)
    assert "Stopped at cue, $0.12, 2 findings" in said
    assert "Built" not in said


def test_verify_prints_a_row_per_measured_cue_with_its_signed_offset() -> None:
    measured = VerifyResult(
        ok=True,
        run="r",
        film=Path("build/final/demo.mp4"),
        film_seconds=64.0,
        cues=(
            CueCheck(section=2, cue="2:chart", spoken=12.4, shown=12.46, offset=0.06),
            CueCheck(section=2, cue="2:skipped", spoken=13.0),
        ),
        seconds=1.0,
    )
    said = recorded(measured)
    assert "Verifying build/final/demo.mp4, 1:04 long." in said
    assert "2:chart" in said
    assert "+0.06" in said
    assert "2:skipped" not in said


def test_a_clip_says_its_file_its_length_and_its_section() -> None:
    cut = ClipResult(
        ok=True,
        run="r",
        section=3,
        film=Path("clip-3.mp4"),
        words=Path("clip-3.words.json"),
        start=0.5,
        end=2.0,
        seconds=2.0,
        hold_seconds=0.5,
        gain_db=0.0,
        estimated=True,
    )
    assert "Cut clip-3.mp4, 2.0 seconds of section 3." in recorded(cut)


def test_the_config_readings_name_the_key_its_value_and_its_layer() -> None:
    listed = ConfigListResult(
        ok=True,
        keys=(SettingValue(key="video.crf", value=20, default=18, layer=Layer.PROJECT, file=Path("decktalk.toml")),),
    )
    said = recorded(listed)
    assert "video.crf" in said
    assert "project" in said
    got = ConfigGetResult(ok=True, key=SettingValue(key="video.crf", value=20, default=18, layer=Layer.PROJECT))
    assert "video.crf = 20 (project)" in recorded(got)


def test_a_write_that_a_higher_layer_shadows_says_so() -> None:
    shadowed = ConfigSetResult(
        ok=True,
        written=(Path("decktalk.toml"),),
        key="video.crf",
        value=20,
        previous=None,
        scope=Scope.PROJECT,
        file=Path("decktalk.toml"),
        effective=24,
        layer=Layer.ENVIRONMENT,
        dry_run=False,
    )
    said = recorded(shadowed)
    assert "decktalk.toml set video.crf = 20" in said
    assert "still decides it, at 24" in said


def test_an_unset_names_every_key_the_file_no_longer_sets() -> None:
    gone = ConfigUnsetResult(
        ok=True,
        written=(Path("decktalk.toml"),),
        keys=("video.crf", "video.preset"),
        previous=20,
        effective=18,
        layer=Layer.DEFAULT,
        scope=Scope.PROJECT,
        file=Path("decktalk.toml"),
    )
    assert "decktalk.toml no longer sets video.crf, video.preset." in recorded(gone)


def test_check_states_the_price_in_the_one_sentence_the_price_writes() -> None:
    """The price owns its sentence, so the check summary prints it rather than a second wording."""
    judged = CheckResult(ok=True, run="r", judged=(Path("script.md"),), pages=True, frames=True, spend=spend())
    said = " ".join(recorded(judged).split())
    assert spend().sentence in said
