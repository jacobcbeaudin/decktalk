"""Everything the command line writes, so that no command anywhere prints a character itself.

A command returns its result and this module renders it, which is what keeps the table, the JSON
object and the exit code one decision rather than three. Three renderings share one stream of
events: a transient live region on a terminal, plain stage lines in a pipe, and the JSON lines
`--events` writes on stderr. The file under `build/events/` is the library's, so it is written
whichever of these is on.

A download is the one wait a run makes on the network, so both readers keep the line that starts it,
with its size, and the line that ends it, and a pipe also gets a progress line now and then, because a
pipe has no bar.

Colour and the live region are the only differences between a terminal and a pipe. The tables are the same tables, the
error block is the same block, and nothing prints a second vocabulary for a reader who piped it.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from rich import box
from rich.console import Console, RenderableType
from rich.console import Group as Stack
from rich.filesize import decimal
from rich.live import Live
from rich.measure import Measurement
from rich.table import Table
from rich.text import Text

from decktalk.captions import clock
from decktalk.errors import ErrorInfo
from decktalk.events import Event, Level, RunLog, RunStart, StageDone, StageProgress, StageStart, ToolFetch
from decktalk.files import json_text
from decktalk.findings import Applicability, Finding, Location, Severity
from decktalk.inputs import programs
from decktalk.pipeline import Outcome, Stage
from decktalk.results import (
    AssembleResult,
    BuildResult,
    CheckResult,
    ClipResult,
    ConfigExplainResult,
    ConfigGetResult,
    ConfigListResult,
    ConfigSetResult,
    ConfigUnsetResult,
    CueResult,
    DoctorResult,
    InitResult,
    InstallResult,
    NarrateResult,
    RecordResult,
    Result,
    ScoreResult,
    ServeResult,
    StatusResult,
    StoryboardResult,
    TakeState,
    VerifyResult,
    Word,
    WordsResult,
    counted,
    money,
)

STAGE_COLUMN = 12
"""Calibration: how wide the stage name sits in a progress line, right-aligned, which is wider than every
stage name and every word that shares the column."""

REFRESH_PER_SECOND = 8
"""Calibration: how often the live region redraws, which is fast enough to read and slow enough not to flicker."""

FETCH_LINE_SECONDS = 10
"""Calibration: the most often a pipe hears how far a download has got, which is often enough that a reader
with a command timeout of a minute or two sees it moving, and rare enough that a slow link writes a page
rather than a book."""

ERROR_STYLE = "bold red"
WARNING_STYLE = "yellow"
CODE_STYLE = "bold"
QUIET_STYLE = "dim"


@dataclass
class Report:
    """One stage line as the live region and the plain renderer both hold it."""

    stage: Stage
    label: str = ""
    seconds: float | None = None
    outcome: Outcome | None = None
    done: int = 0
    total: int = 0

    def line(self) -> Text:
        """The stage's own row: its name, what it is working on, how long it took, and how it ended when not ok."""
        name = self.stage.value.title().rjust(STAGE_COLUMN)
        text = Text(f"{name} {self.label}")
        if self.seconds is not None:
            text.append(f"   {clock(self.seconds)}", style=QUIET_STYLE)
        elif self.total:
            text.append(f"   {self.done}/{self.total}", style=QUIET_STYLE)
        if self.outcome not in (None, Outcome.RAN):
            text.append(f"   {self.outcome.value}")
        return text


def _fetching(event: ToolFetch, *, opened: bool) -> Text:
    """A download's start, or how far it has got: its size when the fetcher knows it, and never one made up.

    A start whose size is not known yet names the download alone, and the size it learns later rides
    on its progress lines, so no download is started twice.
    """
    head = f"{'Fetching'.rjust(STAGE_COLUMN)} {event.tool}"
    if event.total_bytes is None:
        return Text(head if not opened else f"{head}, {decimal(event.bytes)}")
    if not opened:
        return Text(f"{head}, about {decimal(event.total_bytes)}")
    return Text(f"{head}, {decimal(event.bytes)} of {decimal(event.total_bytes)}")


def _fetched(event: ToolFetch) -> Text:
    """A download's last line, which says how much arrived."""
    return Text(f"{'Fetched'.rjust(STAGE_COLUMN)} {event.tool}, {decimal(event.bytes)}")


def _ended(event: ToolFetch) -> bool:
    """Whether this line closes its download, which a fetcher says by counting every byte it stated."""
    return event.total_bytes is not None and event.bytes == event.total_bytes


class Downloads:
    """Which downloads have been announced, so each is started once however its fetcher reports it.

    A fetcher may learn its size after it starts, as Playwright's installer states each part's size
    only at its first tenth, and that size is then said on the progress lines rather than as a start.
    """

    def __init__(self) -> None:
        self._started: set[str] = set()

    def opens(self, event: ToolFetch) -> bool:
        """Whether this line is news to a reader, which is a download not yet announced."""
        news = event.tool not in self._started
        self._started.add(event.tool)
        return news

    def close(self, event: ToolFetch) -> None:
        """Forget a download that ended, so a second fetch of the same tool is announced again."""
        self._started.discard(event.tool)


class Renderer:
    """One reader of the event stream, on the console it writes to, holding nothing open unless it says so."""

    def __init__(self, console: Console) -> None:
        self._console = console

    def open(self) -> None:
        """Start whatever this reader draws, which for most of them is nothing."""

    def close(self) -> None:
        """Stop whatever this reader draws, which for most of them is nothing."""

    def __call__(self, event: Event) -> None:
        """Take one line of the stream."""
        raise NotImplementedError


class Region(Renderer):
    """The transient live region a terminal shows, which is one row per stage of the run.

    It is transient because a run's own summary is what a reader keeps, and because a region that
    stayed behind would double every line of a watch loop.
    """

    def __init__(self, console: Console) -> None:
        super().__init__(console)
        self._rows: dict[Stage, Report] = {}
        self._downloads = Downloads()
        self._live = Live(console=console, transient=True, refresh_per_second=REFRESH_PER_SECOND)

    def open(self) -> None:
        """Start drawing, which a command does once it knows the run has begun."""
        self._live.start()

    def close(self) -> None:
        """Stop drawing and leave nothing behind."""
        self._live.stop()

    def __call__(self, event: Event) -> None:
        """Take one line of the stream into the region."""
        if isinstance(event, StageStart):
            self._rows[event.stage] = Report(stage=event.stage)
        elif isinstance(event, StageProgress):
            row = self._rows.setdefault(event.stage, Report(stage=event.stage))
            row.label, row.done, row.total = event.label, event.done, event.total
        elif isinstance(event, StageDone):
            row = self._rows.setdefault(event.stage, Report(stage=event.stage))
            row.seconds, row.outcome = event.elapsed_seconds, event.outcome
        elif isinstance(event, ToolFetch):
            # The start and the end are printed above the region, so they stay once it is gone.
            if self._downloads.opens(event):
                self._console.print(_fetching(event, opened=False))
            if _ended(event):
                self._downloads.close(event)
                self._console.print(_fetched(event))
            self._live.update(_fetching(event, opened=True))
            return
        else:
            return
        self._live.update(Stack(*(row.line() for row in self._rows.values())))


class Lines(Renderer):
    """The plain stage lines a pipe gets, which are the live region without the cursor movement.

    `clock` is where the time between two progress lines of a download is read, which a test replaces.
    """

    def __init__(self, console: Console, *, clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(console)
        self._clock = clock
        self._downloads = Downloads()
        self._said: dict[str, float] = {}

    def __call__(self, event: Event) -> None:
        """Write one line for every stage that ended and for a download's start and end, and a download's
        progress, when it counts something, no oftener than `FETCH_LINE_SECONDS`."""
        if isinstance(event, StageDone):
            row = Report(stage=event.stage, seconds=event.elapsed_seconds, outcome=event.outcome)
            self._console.print(row.line())
        elif isinstance(event, ToolFetch):
            self._fetch(event)

    def _fetch(self, event: ToolFetch) -> None:
        now = self._clock()
        if self._downloads.opens(event):
            self._console.print(_fetching(event, opened=False))
            self._said[event.tool] = now
        if _ended(event):
            self._downloads.close(event)
            self._said.pop(event.tool, None)
            self._console.print(_fetched(event))
        elif event.bytes and now - self._said.get(event.tool, now) >= FETCH_LINE_SECONDS:
            self._said[event.tool] = now
            self._console.print(_fetching(event, opened=True))


class Jsonl(Renderer):
    """The JSON lines `--events` writes on stderr, which are the library's own lines untouched."""

    def __call__(self, event: Event) -> None:
        """Write one line, exactly as the library minted it."""
        self._console.file.write(event.model_dump_json() + "\n")
        self._console.file.flush()


class Notes(Renderer):
    """The log lines the library would have printed, written at the level `-v` and `-q` choose.

    `heard` is shared by the renderers of one command that runs the same judgement twice, as
    `check --fix` does, so a sentence the first run said is not printed again by the second.
    """

    def __init__(self, console: Console, *, verbose: bool, quiet: bool, heard: set[str] | None = None) -> None:
        super().__init__(console)
        self._verbose = verbose
        self._quiet = quiet
        self._heard = heard

    def __call__(self, event: Event) -> None:
        """Write one log line when its level passes the two flags that choose between them."""
        if not isinstance(event, RunLog):
            return
        quiet = event.level in (Level.DEBUG, Level.INFO)
        if event.level is Level.DEBUG and not self._verbose:
            return
        if self._quiet and quiet:
            return
        if self._heard is not None:
            if event.message in self._heard:
                return
            self._heard.add(event.message)
        style = QUIET_STYLE if quiet else WARNING_STYLE
        # Under -v a line says which module wrote it, which is what tells a tool call from a stage's sentence.
        said = f"{event.source}: {event.message}" if self._verbose and event.source else event.message
        self._console.print(Text(said, style=style))


class Opening(Renderer):
    """The first line `build` writes, which names the run and the file its events are appended to."""

    def __init__(self, console: Console) -> None:
        super().__init__(console)
        self.said = False

    def __call__(self, event: Event) -> None:
        """Name the run and its events file once, before the first stage of the run."""
        if not isinstance(event, RunStart) or self.said:
            return
        self.said = True
        where = event.events_file.as_posix() if event.events_file else "no file"
        self._console.print(Text(f"run {event.run}, events {where}", style=QUIET_STYLE))


def error_block(info: ErrorInfo, console: Console) -> None:
    """The one layout an error takes, on a terminal and in a pipe, with colour the only difference."""
    block = Text()
    block.append(f"error[{info.code.value}]", style=ERROR_STYLE)
    block.append(f": {info.message}\n")
    if info.hint:
        block.append(f"  hint: {info.hint}\n")
    block.append(f"  docs: {info.docs}", style=QUIET_STYLE)
    console.print(block)


def finding_lines(findings: Sequence[Finding], console: Console) -> None:
    """Every judgement, then the one count line that says how many and how many are fixable."""
    for found in findings:
        console.print(_finding(found))
    if findings:
        console.print(_counted(findings))


def _finding(found: Finding) -> Text:
    """One judgement: where it is, its code, its sentence, and the fix under it."""
    line = Text(f"{_where(found.location)}: ")
    line.append(found.code.value, style=CODE_STYLE if found.severity is Severity.ERROR else WARNING_STYLE)
    line.append(f" {found.message}")
    if found.fix is not None:
        line.append(f"\n  fix ({found.fix.applicability.value}): {found.fix.title}", style=QUIET_STYLE)
    return line


def _counted(findings: Sequence[Finding]) -> Text:
    """How many judgements there are, how many are errors, and how many `--fix` would apply."""
    errors = sum(1 for found in findings if found.severity is Severity.ERROR)
    fixable = sum(1 for found in findings if found.fix is not None and found.fix.applicability is Applicability.SAFE)
    text = Text(f"Found {counted(len(findings), 'finding')}, {counted(errors, 'error')}.")
    if fixable:
        text.append(f" {fixable} fixable with --fix.")
    return text


def _where(location: Location) -> str:
    """The object a judgement names, with its file and line when the judgement knows them."""
    if location.file is not None and location.line is not None:
        return f"{location.file.as_posix()}:{location.line}"
    if location.file is not None:
        return location.file.as_posix()
    return location.where


def render(result: Result, console: Console) -> None:
    """The human reading of one result, which is the same reading piped as on a terminal.

    The one difference is a table too wide for the line. A terminal fits it to the person's window,
    and a pipe prints it at the width its cells need, because a reader of plain output, an agent
    among them, has no wider window to look at and a cut path or scene is lost to it. A caller that
    sets `COLUMNS` chose the line width, as the docs samples do, so its table fits that width.
    """
    whole = not console.is_terminal and COLUMNS not in os.environ
    write = RENDERERS.get(type(result))
    if write is not None:
        for piece in write(result):
            if isinstance(piece, Table) and whole:
                _whole(piece, console)
                console.print(piece, crop=False)
            else:
                console.print(piece)
    finding_lines(result.findings, console)


COLUMNS = "COLUMNS"
"""The variable rich reads the line width from, which a caller sets to choose the width of a pipe."""


def _whole(table: Table, console: Console) -> None:
    """Widen `table` to the width its widest cells need when that is wider than the console's line."""
    needed = Measurement.get(console, console.options.update_width(sys.maxsize), table).maximum
    if needed > console.width:
        table.width = needed


def _table(*columns: str) -> Table:
    """One table in the one shape every table here takes, which is a simple box and a bold header."""
    table = Table(box=box.SIMPLE, header_style="bold", pad_edge=False)
    for column in columns:
        table.add_column(column)
    return table


def _yes(state: bool) -> str:
    """A boolean column, written as the two words a reader scans rather than as true and false."""
    return "yes" if state else "no"


def _init(result: InitResult) -> Iterable[RenderableType]:
    yield Text(
        f"Wrote {result.root.as_posix()} from the {result.example} example, {counted(len(result.written), 'file')}."
    )
    if result.voices:
        default, *others = result.voices
        yield Text(f"Voices {', '.join((f'{default} (default)', *others))}")
    # A machine refuses a project's programs until it is told otherwise, so the first build of a
    # project that names one says so on its own command line rather than failing on it.
    allowed = f" --set {programs.KEY}={programs.RUN}" if result.programs else ""
    yield Text(f"Next   cd {result.root.as_posix()} && decktalk build --no-spend{allowed}", style=QUIET_STYLE)


def _tools(tools: Iterable[Any]) -> Table:
    table = _table("Tool", "Version", "Where")
    for tool in tools:
        table.add_row(tool.tool, tool.version or "missing", tool.path.as_posix() if tool.path else "")
    return table


def _install(result: InstallResult) -> Iterable[RenderableType]:
    yield _tools(result.tools)
    yield Text(f"Cache  {result.cache.as_posix()}", style=QUIET_STYLE)


def _doctor(result: DoctorResult) -> Iterable[RenderableType]:
    yield _tools(result.tools)
    yield Text(f"Python    {result.python}")
    yield Text(f"Platform  {result.platform}")
    for key in result.api_keys:
        yield Text(f"API key   {key.provider} {key.variable} {key.state.value}")


def _status(result: StatusResult) -> Iterable[RenderableType]:
    table = _table("Section", "Key", "Plays", "Voices", "Take", "Recorded", "Assembled", "Stale")
    for section in result.sections:
        table.add_row(
            str(section.section),
            section.key,
            section.source,
            ", ".join(section.voices),
            section.take_state.value if section.take_state else "",
            _yes(section.recorded),
            _yes(section.assembled),
            _yes(section.recording_stale or section.take_state is TakeState.STALE),
        )
    yield table
    if result.film is not None:
        yield Text(f"Film   {result.film.as_posix()}, {clock(result.film_seconds or 0)} long")
    for run in result.runs:
        yield Text(f"Live   {run.run} writing {run.events_file.as_posix()}", style=QUIET_STYLE)
    if result.unplayed is not None and result.unplayed.sentence is not None:
        yield Text(f"Takes  {result.unplayed.sentence}")
    if result.next is not None:
        yield Text(f"Next   {result.next}", style=QUIET_STYLE)


def _check(result: CheckResult) -> Iterable[RenderableType]:
    yield Text(f"Checking {', '.join(path.as_posix() for path in result.judged)}.")
    yield Text(result.cost.sentence)
    if result.storyboard is not None:
        yield Text(f"Storyboard {result.storyboard.as_posix()}", style=QUIET_STYLE)


def _words(result: WordsResult) -> Iterable[RenderableType]:
    for section in result.sections:
        if not section.turns[1:]:
            yield _word_table(f"Section {section.section}", section.words)
            continue
        at = 0
        for turn in section.turns:
            heading = f"Section {section.section}, turn {turn.turn}, {turn.voice}"
            yield _word_table(heading, section.words[at : at + turn.word_count])
            at += turn.word_count


def _word_table(heading: str, words: Iterable[Word]) -> Table:
    """One table of words under its heading, each with its start and its end."""
    table = _table(heading, "Start", "End")
    for word in words:
        table.add_row(word.word, f"{word.start:.2f}", f"{word.end:.2f}")
    return table


def _storyboard(result: StoryboardResult) -> Iterable[RenderableType]:
    where = result.storyboard.as_posix() if result.storyboard else "nothing"
    yield Text(f"Wrote {where}, {counted(len(result.panels), 'panel')}.")


def _serve(result: ServeResult) -> Iterable[RenderableType]:
    yield Text(f"Serving {result.url}")


def _narrate(result: NarrateResult) -> Iterable[RenderableType]:
    table = _table("Section", "Take", "Characters", "Seconds")
    for take in result.sections:
        table.add_row(str(take.section), take.outcome.value, str(take.characters), _length(take.seconds))
    yield table
    yield Text(result.cost.sentence)


def _length(seconds: float | None) -> str:
    """How long a take or a sound runs, or that it has no length yet, which a JSON null says."""
    return "not yet" if seconds is None else f"{seconds:.1f}"


def _cue(result: CueResult) -> Iterable[RenderableType]:
    table = _table("Section", "Cue", "Phrase", "Seconds")
    for section in result.sections:
        for cue in section.cues:
            seconds = "unresolved" if cue.seconds is None else f"{cue.seconds:.2f}"
            table.add_row(str(section.section), cue.id, cue.phrase, seconds)
    yield table


def _record(result: RecordResult) -> Iterable[RenderableType]:
    table = _table("Section", "File", "Seconds", "Frames", "Kept")
    for section in result.sections:
        table.add_row(
            str(section.section),
            section.file.as_posix() if section.file else "",
            f"{section.seconds:.1f}",
            str(section.frames),
            _yes(section.kept),
        )
    yield table


def _score(result: ScoreResult) -> Iterable[RenderableType]:
    table = _table("Item", "Kind", "Outcome", "Seconds")
    for item in result.items:
        table.add_row(item.name, item.kind.value, item.outcome.value, _length(item.seconds))
    yield table
    yield Text(result.cost.sentence)


def _assemble(result: AssembleResult) -> Iterable[RenderableType]:
    yield Text(f"Built {result.film.as_posix()}, {clock(result.film_seconds)} long.")
    if result.loudness is not None:
        yield Text(
            f"Loudness {result.loudness.integrated_lufs:.1f} LUFS against {result.loudness.target_lufs:.1f}.",
            style=QUIET_STYLE,
        )


def _verify(result: VerifyResult) -> Iterable[RenderableType]:
    yield Text(f"Verifying {result.film.as_posix()}, {clock(result.film_seconds)} long.")
    measured = [cue for cue in result.cues if cue.offset_seconds is not None]
    if measured:
        # A cue measured with declared motion masked out says how much of the frame that took.
        table = _table("Section", "Cue", "Spoken", "Shown", "Offset", "Masked")
        for cue in measured:
            table.add_row(
                str(cue.section),
                cue.cue,
                f"{cue.spoken:.2f}",
                "" if cue.shown is None else f"{cue.shown:.2f}",
                f"{cue.offset_seconds:+.2f}" if cue.offset_seconds is not None else "",
                "" if cue.masked_percent is None else f"{cue.masked_percent:.1f}%",
            )
        yield table
    if result.motions:
        table = _table("Section", "Motion", "Landing", "Spoken", "Shown", "Offset")
        for row in result.motions:
            table.add_row(
                str(row.section),
                row.cue,
                row.kind,
                f"{row.spoken:.2f}",
                "" if row.shown is None else f"{row.shown:.2f}",
                "" if row.offset_seconds is None else f"{row.offset_seconds:+.2f}",
            )
        yield table


def _build(result: BuildResult) -> Iterable[RenderableType]:
    # The stages are not printed again here. Each one was reported as it ran, by the live region on
    # a terminal and by one plain line in a pipe, so this is the run's own last sentence.
    count = len(result.findings)
    found = "nothing found" if not count else counted(count, "finding")
    if result.stopped_at is not None:
        stopped = f"at {result.stopped_at.value}"
        yield Text(f"{'Stopped'.rjust(STAGE_COLUMN)} {stopped}, {money(result.cost.dollars)}, {found}")
    else:
        where = result.film.as_posix() if result.film else "nothing"
        yield Text(f"{'Built'.rjust(STAGE_COLUMN)} {where}, {money(result.cost.dollars)}, {found}")


def _clip(result: ClipResult) -> Iterable[RenderableType]:
    yield Text(f"Cut {result.file.as_posix()}, {result.seconds:.1f} seconds of section {result.section}.")


def _config_list(result: ConfigListResult) -> Iterable[RenderableType]:
    table = _table("Key", "Value", "Layer", "Default")
    for key in result.keys:
        table.add_row(key.key, _scalar(key.value), key.layer.value, _scalar(key.default))
    yield table


def _config_get(result: ConfigGetResult) -> Iterable[RenderableType]:
    yield Text(f"{result.setting.key} = {_scalar(result.setting.value)} ({result.setting.layer.value})")


def _config_set(result: ConfigSetResult) -> Iterable[RenderableType]:
    verb = "would set" if result.dry_run else "set"
    yield Text(f"{result.file.as_posix()} {verb} {result.key} = {_scalar(result.value)}")
    if result.layer.value != result.scope.value:
        still = f"The {result.layer.value} layer still decides it, at {_scalar(result.effective)}."
        yield Text(still, style=WARNING_STYLE)


def _config_unset(result: ConfigUnsetResult) -> Iterable[RenderableType]:
    yield Text(f"{result.file.as_posix()} no longer sets {', '.join(result.keys)}.")


def _config_explain(result: ConfigExplainResult) -> Iterable[RenderableType]:
    yield Text(f"{result.key} = {_scalar(result.value)} ({result.layer.value})", style=CODE_STYLE)
    yield Text(f"  {result.sentence}")
    yield Text(f"  type {result.type}, default {_scalar(result.default)}, {result.range}")
    if result.unit:
        yield Text(f"  unit {result.unit}")
    if result.hazard:
        yield Text(f"  hazard {result.hazard}", style=WARNING_STYLE)
    if result.decides:
        yield Text(f"  decides {', '.join(code.value for code in result.decides)}", style=QUIET_STYLE)
    yield Text(f"  docs {result.docs}", style=QUIET_STYLE)


def _scalar(value: object) -> str:
    """One settings value as a row prints it, which is JSON's own spelling for everything but a string."""
    return value if isinstance(value, str) else json_text(value)


RENDERERS: dict[type[Result], Callable[[Any], Iterable[RenderableType]]] = {
    AssembleResult: _assemble,
    BuildResult: _build,
    CheckResult: _check,
    ClipResult: _clip,
    ConfigExplainResult: _config_explain,
    ConfigGetResult: _config_get,
    ConfigListResult: _config_list,
    ConfigSetResult: _config_set,
    ConfigUnsetResult: _config_unset,
    CueResult: _cue,
    DoctorResult: _doctor,
    InitResult: _init,
    InstallResult: _install,
    NarrateResult: _narrate,
    RecordResult: _record,
    ServeResult: _serve,
    ScoreResult: _score,
    StatusResult: _status,
    StoryboardResult: _storyboard,
    VerifyResult: _verify,
    WordsResult: _words,
}
"""One reading per result, so the command returns its result and the reading lives in one place."""


__all__ = [
    "Jsonl",
    "Lines",
    "Notes",
    "Opening",
    "Region",
    "Renderer",
    "error_block",
    "finding_lines",
    "render",
]
