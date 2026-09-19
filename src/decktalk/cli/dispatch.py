"""The table that maps a command name to the handler that runs it.

A handler takes its own options object and gives back an `Outcome`: the payload a program reads,
the summary that leads both outputs, the tally the exit code comes from, the files the run wrote,
and the text a person reads. It never prints the envelope, never chooses an exit code and never
formats a table of its own, so every command ends the same way whatever it did. `build` is the one
exception, because a run of several minutes prints each stage's table as that stage finishes, and it
is the only handler that reads `--json`.

A handler is named for the command it runs, and that function name is its row in this table, so a
command and its handler cannot drift apart. One module holds each group of the command table, in the
order the help screen prints them: `machine.py` holds the commands that act on a machine,
`authoring.py` holds the commands an author runs around a build, and `video.py` holds the stages and
the whole run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import authoring, machine, video
from .envelope import Outcome
from .options import Options

HANDLERS: dict[str, Callable[[Any], Outcome]] = {
    handler.__name__: handler
    for handler in (
        machine.init, machine.install, machine.doctor,
        authoring.status, authoring.preflight, authoring.words, authoring.screenshots,
        authoring.soundscape, authoring.clip, authoring.serve,
        video.narrate, video.align, video.record,
        video.assemble, video.verify, video.build,
    )
}  # fmt: skip


def run(command: str, opts: Options) -> Outcome:
    """The handler of one command, called with the options that command declared."""
    return HANDLERS[command](opts)
