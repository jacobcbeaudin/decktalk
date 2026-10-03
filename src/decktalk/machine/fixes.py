"""The fix applier: every fix a finding offers, carried out or refused in one sentence.

A safe fix is applied, an unsafe one only on request, and one only a person can make is reported.
Every edit is staged in memory and every file replaced together, so a fix that is refused part way
leaves every file as it was, and a path a fix names is held to the project it was made for.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from decktalk.errors import DeckTalkError, InputError, ToolError
from decktalk.events import (
    Level,
)
from decktalk.files import current_text, replace_all
from decktalk.findings import (
    FIX_COMMANDS,
    Applicability,
    Code,
    CommandFix,
    Edit,
    Finding,
    Location,
)
from decktalk.inputs.paths import at, contained, relative
from decktalk.machine.run import Run
from decktalk.results import (
    ApplyResult,
    FixOutcome,
    Scope,
)
from decktalk.settings import PROJECT_FILE
from decktalk.settings.edit import edit, validate
from decktalk.toolchain import command_line, tail, traced

if TYPE_CHECKING:  # pragma: no cover
    from decktalk.findings import Fix

log = logging.getLogger(__name__)

FIX_TIMEOUT_SECONDS = 1800.0
"""The longest a command fix may run, which fetches a browser and an encoder in minutes and never in an hour."""


def apply_fixes(
    run: Run, findings: Finding | Iterable[Finding], *, root: Path, scope: Scope, unsafe: bool
) -> ApplyResult:
    """Carry out every fix a caller handed over, in order, and publish what each one did.

    A fix left alone is also a warning on the run, so a reader of the stream or the events file
    learns why without holding the result.
    """
    outcomes = tuple(
        apply_fix(run, code, found, root=root, scope=scope, unsafe=unsafe) for code, found in fixes_of(findings)
    )
    for outcome in outcomes:
        if not outcome.applied:
            run.note(f"{outcome.title} was not applied: {outcome.why}", level=Level.WARNING)
    return run.result(ApplyResult, fixes=outcomes)


def apply_fix(run: Run, code: Code, fix: Fix, *, root: Path, scope: Scope, unsafe: bool) -> FixOutcome:
    """Carry out one fix, or say in one sentence why it was left alone.

    A display fix is a change only a person can make, so it is reported and never applied, and an
    unsafe fix can lose the author's work, so it is applied only when the caller asked for that. A
    file the system will not let DeckTalk read or write is a refusal like any other, because the fix
    changed nothing and the caller is owed a sentence rather than an exception.
    """
    if fix.applicability is Applicability.DISPLAY:
        return FixOutcome(code=code, title=fix.title, applied=False, why="only a person can make this change.")
    if fix.applicability is Applicability.UNSAFE and not unsafe:
        why = "this fix can lose work, so it was applied only on request."
        return FixOutcome(code=code, title=fix.title, applied=False, why=why)
    try:
        files = _carry_out(run, fix, root=root, scope=scope)
    except DeckTalkError as refused:
        return FixOutcome(code=code, title=fix.title, applied=False, why=str(refused))
    except (OSError, UnicodeDecodeError) as failed:
        return FixOutcome(code=code, title=fix.title, applied=False, why=_unchanged(failed, root))
    for path in files:
        run.wrote(path)
    changed = tuple(relative(path, root) for path in files)
    return FixOutcome(code=code, title=fix.title, applied=True, files=changed)


def _unchanged(failed: OSError | UnicodeDecodeError, root: Path) -> str:
    """The sentence for a fix the system stopped, naming the file when the failure names one."""
    named = failed.filename if isinstance(failed, OSError) else None
    what = relative(Path(named), root).as_posix() if isinstance(named, str) else "a file this fix names"
    reason = failed.strerror if isinstance(failed, OSError) and failed.strerror else str(failed)
    return f"{what} could not be read or written ({reason}), so every file was left as it was."


def _carry_out(run: Run, fix: Fix, *, root: Path, scope: Scope) -> tuple[Path, ...]:
    """Make the change one fix describes, and give back every file it changed.

    Every edit is made in memory before any file is touched. A key edit is staged as text in the
    same place as a line edit, so a fix that edits the lines of `decktalk.toml` and sets a key in it
    changes one text, and a settings file the fix touched is loaded whole before anything is written.
    Every file is then replaced together by `replace_all`, so a fix whose last edit is refused, or
    whose last file cannot be written, leaves every file as it was.
    """
    if isinstance(fix, CommandFix):
        _run_command(run, fix, root=root)
        return ()
    settings_file = _settings_file(run, root, scope)
    staged: dict[Path, str] = {}
    for one in fix.edits:
        path = _inside(root, one.file)
        if one.key is not None:
            text = staged[settings_file] if settings_file in staged else current_text(settings_file)
            staged[settings_file] = edit(text, one.key, one.new, scope=scope, file=settings_file).text
            continue
        lines = staged[path].splitlines(keepends=True) if path in staged else _lines_under(one, path, root)
        staged[path] = "".join(_edited(one, lines, path, root))
    for path, holds in _settings_files(run, root).items():
        if path in staged:
            validate(staged[path], path, holds)
    replace_all(staged)
    return tuple(staged)


def _run_command(run: Run, fix: CommandFix, *, root: Path) -> None:
    """Run one of DeckTalk's own commands as this interpreter's DeckTalk, bounded in time.

    The set is checked again here, because a model can be built without validation and a fix is the
    one value that decides what this process launches. The command runs as `python -m decktalk`
    under the interpreter making the call rather than as whatever `decktalk` is first on `PATH`,
    which may be another install, and it sees the machine's own environment rather than the
    process's, so a host that built its machine by hand is the one that decides what it reads.
    """
    if fix.command not in FIX_COMMANDS:
        raise InputError(
            f"`{' '.join(fix.command)}` is not one of DeckTalk's own commands, so a fix may not run it.",
            hint="Run the command by hand if you mean it.",
        )
    argv = [sys.executable, "-m", *fix.command]
    typed = command_line(fix.command)
    started = time.monotonic()
    try:
        finished = subprocess.run(
            argv,
            cwd=root,
            env=run.machine.child_environ(),
            check=False,
            capture_output=True,
            timeout=FIX_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as late:
        log.warning(
            "`%s` was stopped after %.0f seconds (timeout).",
            typed,
            FIX_TIMEOUT_SECONDS,
            extra={"data": {"argv": command_line(argv), "reason": "timeout", "limit": FIX_TIMEOUT_SECONDS}},
        )
        raise ToolError(
            f"`{typed}` ran for {FIX_TIMEOUT_SECONDS:.0f} seconds and was stopped.",
            hint=f"Run `{typed}` by hand to see where it waits.",
        ) from late
    said = finished.stderr or finished.stdout or b""
    traced(log, typed, argv, code=finished.returncode, seconds=time.monotonic() - started, said=said)
    if finished.returncode != 0:
        # What the command said is the reason it failed, and a fix outcome keeps the message alone,
        # so the message quotes its last lines.
        raise ToolError(
            f"{fix.command[0]} exited {finished.returncode}: {tail(said)}",
            hint=f"Run `{typed}` by hand to see the rest of what it says.",
        )


def _inside(root: Path, named: Path) -> Path:
    """The file an edit names, held to the project by `contained`, the one rule every project path meets.

    A fix can arrive as JSON from anywhere, so its path is the one part of it an attacker chooses, and
    the refusal says what a fix may change rather than what a project may read. The path handed back
    is the resolved file spelled under the root the caller gave, so two edits that name one file by
    two spellings change it once.
    """
    try:
        path = contained(root, named)
    except InputError as outside:
        raise InputError(
            f"{Path(named).as_posix()} is outside the project, so a fix may not change it.",
            hint="A fix only ever changes files inside the project it was made for.",
            location=Location(where=Path(named).as_posix()),
        ) from outside
    return root / path.resolve().relative_to(root.resolve())


def _edited(edit: Edit, lines: list[str], path: Path, root: Path) -> list[str]:
    """The lines of one file after one edit, addressed by the one locator the edit names."""
    if edit.pointer is not None:
        raise InputError(
            f"a pointer edit into {edit.file} has no applier yet.",
            hint="Make the change by hand, or run the command the finding names.",
            location=at(path, root),
        )
    if edit.old is not None:
        _still_reads(edit, lines, (edit.line or 1) - 1, path, root)
    return edit.applied(lines)


def _still_reads(edit: Edit, lines: list[str], index: int, path: Path, root: Path) -> None:
    """Refuse a replacement whose line no longer reads what the fix was made against.

    A fix is computed from the file as it was when the finding was raised. A file edited since then
    has moved its lines, and replacing line n by number would overwrite whatever line n holds now.
    """
    found = lines[index].rstrip("\r\n") if index < len(lines) else None
    if found != edit.old:
        raise InputError(
            f"line {edit.line} of {edit.file} no longer reads what this fix was made against, so it was left alone.",
            hint="Run the command that raised the finding again for a fix made against the file as it is now.",
            location=at(path, root),
        )


def _lines_under(edit: Edit, path: Path, root: Path) -> list[str]:
    """The lines one edit works on, which is an empty file when the edit is the one that writes it.

    A fix that writes a whole file states itself as an insert of the whole text at line one, as the
    cue file's does, so a file that is not there yet is that edit's starting point. Every other line
    edit needs the lines it names, and a caller who cannot be given them is told so in a sentence it
    can print rather than in an operating system error nothing above here would catch.
    """
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    if edit.old is None and (edit.line or 1) == 1:
        return []
    raise InputError(
        f"{edit.file} is not there, so it has no line {edit.line or 1} to change.",
        hint="Write the file first, or run the command the finding names.",
        location=at(path, root),
    )


def fixes_of(given: Finding | Iterable[Finding]) -> tuple[tuple[Code, Fix], ...]:
    """Every fix a caller handed over, each beside the code of the finding it resolves.

    A fix is taken from its finding rather than on its own, because what a fix resolves is the
    finding's own code and an outcome that could not name one would tell a caller nothing.
    """
    findings = (given,) if isinstance(given, Finding) else tuple(given)
    return tuple((found.code, found.fix) for found in findings if found.fix is not None)


def _settings_file(run: Run, root: Path, scope: Scope) -> Path:
    """The file a settings change lands in, which is the project's own or the one this machine was built from.

    The machine's file is the one it holds, and never the one the process environment would name,
    so a fix applied through a machine a host built by hand lands in that host's file. The project's
    file is held to the project like every file a fix names, so a link out of it is refused.
    """
    return _inside(root, Path(PROJECT_FILE)) if scope is Scope.PROJECT else _machine_file(run)


def _settings_files(run: Run, root: Path) -> dict[Path, Scope]:
    """Both settings files a fix may reach, each with the scope its keys belong to."""
    return {_settings_file(run, root, held): held for held in Scope}


def _machine_file(run: Run) -> Path:
    """This machine's settings file with its links followed, which is where a change to it lands."""
    return run.machine.machine_file.resolve()
