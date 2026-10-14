"""The programs a project names, and the machine's word on whether `record` starts them.

A project names a program in one place: a render section's `render_command`, which draws the
section's picture. It is a program a stranger's `decktalk.toml` can name, and it runs as the user who
runs DeckTalk, so it can read every file that user can, the project's `.env` included. The scrubbed
environment it is given keeps a credential in the shell away from it and keeps nothing on disk away
from it.

So the machine decides, through `[record] programs`: `refuse` starts none, `run` starts every one,
and `ask` starts them when a person at a terminal said yes for this run, which the command line
turns into `run` for that run alone. The `untrusted` page policy refuses every one whatever the key
says. The refusal lists every program the run would have started, before the first one starts.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from decktalk.errors import InputError
from decktalk.inputs.paths import contained
from decktalk.media.browser import UNTRUSTED
from decktalk.results import Program
from decktalk.settings import BY_ID, PROGRAM_POLICIES, PROJECT_FILE, RecordConfig

REFUSE, ASK, RUN = PROGRAM_POLICIES
"""The three answers `[record] programs` takes, by name."""

KEY = "record.programs"
"""The key every refusal names, which is the one place a machine allows a project's programs."""


def section_program(number: int, command: Sequence[str]) -> Program:
    """The program that draws section `number`."""
    return Program(named_by=f"section {number}'s render_command", command=tuple(command), section=number)


def project_files(root: Path, command: Sequence[str]) -> dict[str, Path]:
    """The words of `command` that are relative paths naming a file in the project, each joined to the root.

    A placeholder, an absolute path, a flag or a word of inline code names no project file and is
    left alone. A word that names a file through a link out of the project is refused, as every file
    a project names is.
    """
    found: dict[str, Path] = {}
    for word in command:
        if "{" in word or Path(word).is_absolute() or not (root / word).is_file():
            continue
        found[word] = contained(root, word)
    return found


def admitted(record: RecordConfig, programs: Sequence[Program]) -> None:
    """Refuse every program in `programs` unless this machine runs a project's programs, naming each one."""
    if not programs or (record.programs == RUN and record.page_policy != UNTRUSTED):
        return
    listed = "; ".join(f"{program.named_by} runs `{program.shown}`" for program in programs)
    if record.page_policy == UNTRUSTED:
        raise InputError(
            f"{PROJECT_FILE} names a program ({listed}), and this machine treats every page as untrusted, so it "
            "runs no program a project names.",
            hint="Build on a machine that trusts the project, or give it the files the programs would have made.",
        )
    why = ", and nobody at a terminal allowed it for this run" if record.programs == ASK else ""
    raise InputError(
        f"{PROJECT_FILE} names a program ({listed}), and {KEY} is {record.programs} on this machine{why}, so it "
        "does not run.",
        hint=(
            "A project's program runs with your user's rights and can read every file you can, .env included, "
            f"so read it first. Allow it for one run with --set {KEY}={RUN} or {variable()}={RUN}, ask each run "
            f"with `decktalk config set {KEY} {ASK} --scope machine`, or allow every project's programs with "
            f"`decktalk config set {KEY} {RUN} --scope machine`."
        ),
    )


def variable() -> str:
    """The environment variable that sets `record.programs` on this machine."""
    return BY_ID[KEY].environment


__all__ = [
    "ASK",
    "KEY",
    "REFUSE",
    "RUN",
    "admitted",
    "project_files",
    "section_program",
    "variable",
]
