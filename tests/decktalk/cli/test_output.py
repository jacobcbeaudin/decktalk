"""What the command line writes: the finding line, the error block, and the tables per result."""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from inline_snapshot import snapshot
from rich.console import Console

from decktalk.cli import output
from decktalk.errors import ErrorCode, ErrorInfo, InputError
from decktalk.events import Event, Fetch, Level, Log, Progress, RunStart, StageDone, StageStart
from decktalk.findings import Certainty, Code
from decktalk.pipeline import Outcome, Stage
from decktalk.results import (
    RESULTS,
    ConfigGetResult,
    ConfigSetResult,
    ConfigUnsetResult,
    CueCheck,
    ErrorResult,
    Layer,
    Scope,
    SettingValue,
    VerifyResult,
)
from support.samples import sample

from .conftest import ANSWERS, finding


def written(render, *args: object) -> str:
    """Whatever one renderer put on its console, as plain text."""
    console = Console(file=io.StringIO(), width=100, no_color=True)
    render(*args, console)
    return console.file.getvalue()  # ty: ignore[unresolved-attribute]


def heard(sink: Callable[[Console], Callable[[Event], None]], *events: Event) -> str:
    """Whatever one stream renderer, opened on a console, put there for these events, as plain text."""
    console = Console(file=io.StringIO(), width=100, no_color=True)
    listen = sink(console)
    for event in events:
        listen(event)
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


@pytest.mark.parametrize("model", sorted(output.RENDERERS, key=lambda model: model.__name__))
def test_every_renderer_is_for_a_published_result_and_writes_what_it_wrote(model) -> None:
    """One of every result, every field filled from its type, held to the whole text a terminal showed."""
    assert model in set(RESULTS.values())
    assert recorded(sample(model, every=True)) == SHOWN[model.__name__]


SHOWN = snapshot(
    {
        "AssembleResult": """\
Built build/film12, 0:13 long.
Loudness 20.2 LUFS against 23.2.
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "BuildResult": """\
     Stopped at cue, $19.25, 1 finding
        Next open build/storyboard24
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "CheckResult": """\
Checking build/judged12.
This run spent $16.25 on 15 characters at $18.25 per 1,000 characters.
Storyboard build/storyboard20
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "ClipResult": """\
Cut build/film13, 17.2 seconds of section 12.
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "ConfigExplainResult": """\
key10 = value13 (override)
  sentence12
  type type11, default default14, range16
  unit unit15
  hazard hazard43
  decides PAGE_STALLED
  docs docs44
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "ConfigGetResult": """\
key10 = value11 (environment)
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "ConfigListResult": """\

 Key     Value     Layer         Default
 ─────────────────────────────────────────
 key10   value11   environment   default12

2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "ConfigSetResult": """\
build/file15 would set key11 = value12
The environment layer still decides it, at effective16.
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "ConfigUnsetResult": """\
build/file14 no longer sets keys11.
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "CueResult": """\

 Section   Cue     Phrase     Seconds
 ────────────────────────────────────
 12        cue14   phrase15   16.25

2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "DoctorResult": """\

 Tool     Version     Where
 ─────────────────────────────────
 tool11   version12   build/path13

Python    python15
Platform  platform16
Voice key yes
Bias      17 ms
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "InitResult": """\
Wrote build/root12 from the example14 example, 1 file.
Next   cd build/root12 && decktalk build --no-voice
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "InstallResult": """\

 Tool     Version     Where
 ─────────────────────────────────
 tool11   version12   build/path13

Cache  build/cache14
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "NarrateResult": """\

 Section   Take     Characters   Seconds
 ───────────────────────────────────────
 13        voiced   16           17.2

Spent $23.25 on placeholder narration.
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "RecordResult": """\

 Section   File           Seconds   Frames   Kept
 ────────────────────────────────────────────────
 12        build/file14   15.2      16       yes

2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "ServeResult": """\
Serving url11
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "SoundscapeResult": """\

 Item     Kind       Status      Seconds
 ───────────────────────────────────────
 name12   ambience   generated   16.2

Would spend $21.25 on the soundscape.
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "StatusResult": """\

 Section   Key     Plays      Voiced   Recorded   Cut   Stale
 ────────────────────────────────────────────────────────────
 14        key15   source17   yes      yes        yes   yes

Film   build/film18, 0:19 long
Live   run20 writing build/events21
Next   next23
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "StoryboardResult": """\
Wrote build/storyboard12, 1 panel.
2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "VerifyResult": """\
Verifying build/film11, 0:12 long.

 Section   Cue     Spoken   Shown   Offset
 ─────────────────────────────────────────
 23        cue24   25.25    26.25   +27.25

2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
        "WordsResult": """\

 Section 11   Start   End
 ──────────────────────────
 word13       14.25   15.25

2.1:formula: CUE_OFF It lands 340 ms late.
Found 1 finding, 1 certain.
""",
    }
)
"""What every renderer writes for the sampled result of its model, keyed by the model's name."""


def test_a_result_with_no_renderer_still_prints_its_findings() -> None:
    assert Code.CUE_UNRESOLVED.value in written(output.render, ErrorResult(ok=False, findings=(finding(),)))


def test_the_plain_lines_renderer_writes_one_line_per_stage_that_ended() -> None:
    said = heard(
        output.Lines,
        _stage_done(),
        _progress(Stage.RECORD, 1, 3),
    )
    assert said.count("\n") == 1
    assert "Record" in said


def test_the_events_renderer_writes_the_library_s_own_line() -> None:
    assert '"event":"stage.done"' in heard(output.Jsonl, _stage_done())


def test_a_debug_line_is_written_under_verbose_alone() -> None:
    assert heard(partial(output.Notes, verbose=False, quiet=False), _log(Level.DEBUG)) == ""
    assert "a debug line" in heard(partial(output.Notes, verbose=True, quiet=False), _log(Level.DEBUG))


def test_verbose_names_the_module_that_wrote_a_line_and_the_default_does_not() -> None:
    line = _log(Level.INFO).model_copy(update={"source": "media.ffmpeg"})
    assert "media.ffmpeg: a debug line" in heard(partial(output.Notes, verbose=True, quiet=False), line)
    assert "media.ffmpeg" not in heard(partial(output.Notes, verbose=False, quiet=False), line)


def test_a_warning_survives_quiet() -> None:
    assert "a debug line" in heard(partial(output.Notes, verbose=False, quiet=True), _log(Level.WARNING))


def test_a_note_one_command_already_printed_is_not_printed_by_its_second_judgement() -> None:
    """`check --fix` judges twice, and the second judgement says what the first already said."""
    console = Console(file=io.StringIO(), width=100, no_color=True)
    heard: set[str] = set()
    for _ in range(2):
        output.Notes(console, verbose=False, quiet=False, heard=heard)(_log(Level.INFO))
    assert console.file.getvalue().count("a debug line") == 1  # ty: ignore[unresolved-attribute]


def test_the_opening_line_names_the_run_and_its_events_file_once() -> None:
    line = RunStart(event="run.start", time=_now(), seq=0, run="abc", events_path=Path("build/events/abc.jsonl"))
    assert heard(output.Opening, line, line).count("run abc") == 1


def _stage_done() -> StageDone:
    """One stage that ended, which is the moment both stage renderers print."""
    return StageDone(
        event="stage.done", time=_now(), seq=0, run="r", stage=Stage.RECORD, outcome=Outcome.OK, seconds=58.0
    )


def _progress(stage: Stage, done: int, total: int) -> Progress:
    """How far one stage has got through its sections."""
    return Progress(
        event="progress", time=_now(), seq=1, run="r", stage=stage, done=done, total=total, unit="section", label="s"
    )


def _log(level: Level) -> Log:
    """One line the library would have printed, at the level a test is about."""
    return Log(event="log", time=_now(), seq=0, run="r", level=level, message="a debug line")


def _now() -> datetime:
    """One instant, which every event carries and no assertion here reads."""
    return datetime.now(UTC)


# What a person reads in a terminal. The snapshot above holds the whole text of every renderer for
# one filled result, and each test below feeds one result or one stream of events that takes a
# branch the filled result does not, and asserts the words that branch writes.


def recorded(result: object, width: int = 120) -> str:
    """One result as a terminal would show it, read back from a recording console.

    The padding a table pads its cells out with is cut from every line, because a reader never sees
    it and a snapshot would otherwise carry it as escapes.
    """
    console = Console(record=True, width=width, no_color=True, file=io.StringIO())
    output.render(result, console)  # ty: ignore[invalid-argument-type]
    return "\n".join(line.rstrip() for line in console.export_text().splitlines()) + "\n"


def test_the_live_region_shows_each_stage_its_progress_and_its_time() -> None:
    console = Console(record=True, width=100, no_color=True, file=io.StringIO(), force_terminal=True)
    region = output.Region(console)
    region.open()
    region(StageStart(event="stage.start", time=_now(), seq=0, run="r", stage=Stage.NARRATE, index=1, count=6))
    region(_stage_done())
    region(_progress(Stage.ASSEMBLE, 2, 5))
    region.close()
    shown = console.export_text()
    assert "Narrate" in shown
    assert "Record" in shown
    assert "0:58" in shown
    assert "2/5" in shown


def test_a_download_shows_its_size_in_the_unit_a_person_reads() -> None:
    console = Console(record=True, width=100, no_color=True, file=io.StringIO(), force_terminal=True)
    region = output.Region(console)
    region.open()
    region(Fetch(event="fetch", time=_now(), seq=0, run="r", tool="ffmpeg", bytes=169_000_000))
    region.close()
    assert "Fetching ffmpeg, 169.0 MB" in console.export_text()


def test_a_finished_build_names_its_film_its_price_and_what_it_found() -> None:
    built = ANSWERS["build"].model_copy(update={"film": Path("build/final/demo.mp4"), "findings": (finding(),)})
    said = recorded(built)
    assert "Built build/final/demo.mp4, $0.12, 1 finding" in said
    assert "1 findings" not in said


def test_a_build_that_stopped_says_where_it_stopped() -> None:
    stopped = ANSWERS["build"].model_copy(
        update={"ok": False, "stopped_at": Stage.CUE, "findings": (finding(), finding())}
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
    # The length is written by the clock a caption or a chapter is written by, so an hour reads as one.
    long_film = recorded(measured.model_copy(update={"film_seconds": 3725.0}))
    assert "Verifying build/final/demo.mp4, 1:02:05 long." in long_film
    assert "2:chart" in said
    assert "+0.06" in said
    assert "2:skipped" not in said


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


@pytest.mark.parametrize(
    ("value", "shown"), [(True, "true"), (None, "null"), (1.5, "1.5"), (["a", "b"], '["a","b"]'), ("plain", "plain")]
)
def test_a_settings_value_prints_in_the_spelling_config_set_accepts(value: object, shown: str) -> None:
    got = ConfigGetResult(
        ok=True, key=SettingValue(key="verify.strict", value=value, default=value, layer=Layer.DEFAULT)
    )
    assert recorded(got) == f"verify.strict = {shown} (default)\n"
