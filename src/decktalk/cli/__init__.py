"""The command line: eighteen commands, each a thin client of one library call.

    decktalk init my-lesson          write a project that already builds
    decktalk build --no-voice        run every stage with placeholder narration
    decktalk check --json            judge the inputs and price a voiced run
    decktalk schema                  read the whole instruction set in one call

The command line is the instruction set. An agent runs `decktalk --help` for the tree, `decktalk
schema` for every command, flag, exit code, error code and finding code, `decktalk schema settings`
for every knob with its range and its sentence, and `decktalk config explain KEY` for one knob whole.
Nothing on that path is a documentation page.

A command opens the project or the machine, calls the library once and returns the result it was
given. The session renders it, so the table on a terminal, the object under `--json` and the exit
code are one decision. Nothing here imports a stage: a browser and an encoder are loaded by the call
that needs them, which is what keeps `schema` and a refused flag as cheap as reading a signature.

The modules are imported for their side effect, which is registering their commands on the one
application, and their order is the order each group prints.
"""

from __future__ import annotations

from decktalk.cli import config as _config  # noqa: F401  (registers the five config verbs)
from decktalk.cli import contracts as _contracts  # noqa: F401  (registers schema)
from decktalk.cli import machine as _machine  # noqa: F401  (registers init, install and doctor)
from decktalk.cli import report as _report  # noqa: F401  (registers status, check, words, storyboard and serve)
from decktalk.cli import run as _run  # noqa: F401  (registers the six stages, build and clip)
from decktalk.cli.app import app, main

__all__ = ["app", "main"]
