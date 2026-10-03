"""Everything the command line writes, so that no command anywhere prints a character itself.

A command returns its result and this module renders it, which is what keeps the table, the JSON
object and the exit code one decision rather than three. Three renderings share one stream of
events: a transient live region on a terminal, plain stage lines in a pipe, and the JSON lines
`--events` writes on stderr. The file under `build/events/` is the library's, so it is written
whichever of these is on.

Colour is the only difference between a terminal and a pipe. The tables are the same tables, the
error block is the same block, and nothing prints a second vocabulary for a reader who piped it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from rich import box
from rich.console import Console, RenderableType
from rich.console import Group as Stack
from rich.filesize import decimal
from rich.live import Live
from rich.table import Table
from rich.text import Text

from decktalk.captions import clock
from decktalk.errors import ErrorInfo
from decktalk.events import Event, Level, RunLog, RunStart, StageDone, StageProgress, StageStart, ToolFetch
from decktalk.files import json_text
from decktalk.findings import Applicability, Finding, Location, Severity
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
    VerifyResult,
    WordsResult,
    counted,
    money,
)

STAGE_COLUMN = 12
"""How wide the stage name sits in a progress line, which is the longest of the six plus a space."""

REFRESH_PER_SECOND = 8
"""How often the live region redraws, which is fast enough to read and slow enough not to flicker."""

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
            self._live.update(Text(f"{'Fetching'.rjust(STAGE_COLUMN)} {event.tool}, {decimal(event.bytes)}"))
            return
        else:
            return
        self._live.update(Stack(*(row.line() for row in self._rows.values())))


class Lines(Renderer):
    """The plain stage lines a pipe gets, which are the live region without the cursor movement."""

    def __call__(self, event: Event) -> None:
        """Write one line for every stage that ended, and nothing for the moments in between."""
        if isinstance(event, StageDone):
            row = Report(stage=event.stage, seconds=event.elapsed_seconds, outcome=event.outcome)
            self._console.print(row.line())


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
    """The human reading of one result, which is the same reading piped as on a terminal."""
    write = RENDERERS.get(type(result))
    if write is not None:
        for piece in write(result):
            console.print(piece)
    finding_lines(result.findings, console)


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
    yield Text(f"Next   cd {result.root.as_posix()} && decktalk build --no-spend", style=QUIET_STYLE)


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
    yield Text(f"API key   {result.api_key_state.value.replace('_', ' ')}")
    if result.bias_ms is not None:
        yield Text(f"Bias      {result.bias_ms:.0f} ms")


def _status(result: StatusResult) -> Iterable[RenderableType]:
    table = _table("Section", "Key", "Plays", "Voiced", "Recorded", "Assembled", "Stale")
    for section in result.sections:
        table.add_row(
            str(section.section),
            section.key,
            section.source,
            _yes(section.voiced),
            _yes(section.recorded),
            _yes(section.assembled),
            _yes(section.stale or section.voiced_stale),
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
        table = _table(f"Section {section.section}", "Start", "End")
        for word in section.words:
            table.add_row(word.word, f"{word.start:.2f}", f"{word.end:.2f}")
        yield table


def _storyboard(result: StoryboardResult) -> Iterable[RenderableType]:
    where = result.storyboard.as_posix() if result.storyboard else "nothing"
    yield Text(f"Wrote {where}, {counted(len(result.panels), 'panel')}.")


def _serve(result: ServeResult) -> Iterable[RenderableType]:
    yield Text(f"Serving {result.url}")


def _narrate(result: NarrateResult) -> Iterable[RenderableType]:
    table = _table("Section", "Take", "Characters", "Seconds")
    for take in result.sections:
        table.add_row(str(take.section), take.status.value, str(take.characters), _length(take.seconds))
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
    table = _table("Item", "Kind", "Status", "Seconds")
    for item in result.items:
        table.add_row(item.name, item.kind.value, item.status.value, _length(item.seconds))
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
        table = _table("Section", "Cue", "Spoken", "Shown", "Offset")
        for cue in measured:
            table.add_row(
                str(cue.section),
                cue.cue,
                f"{cue.spoken:.2f}",
                "" if cue.shown is None else f"{cue.shown:.2f}",
                f"{cue.offset_seconds:+.2f}" if cue.offset_seconds is not None else "",
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
