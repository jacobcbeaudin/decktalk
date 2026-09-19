"""The `decktalk` command line: parse, dispatch, and map what happened to one exit code.

The first help screen is generated from the command table in `parser.py`, so no prose here can
promise a command that does not exist. Every command prints one envelope with `--json` and a table
without it, both read from the same result object.

Four things can end a run, and a caller tells them apart without reading a message. Nothing found
exits 0. A finding exits 1, with `error` null, and the project is what to fix. A command line the
parser refused exits 2 with `error.code` of `USAGE`. Anything DeckTalk raised on purpose, and any
bug it did not, exits 3 with the error slot filled: DeckTalk itself could not run, so stop and tell
the user. An interrupt exits 130 and promises no envelope.
"""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import Sequence
from typing import Any

from .. import __version__
from ..errors import DeckTalkError
from . import dispatch, options, parser
from .envelope import (
    Outcome,
    emit,
    envelope_of,
    error_of,
    exit_for,
    finding_rows,
    line_buffer_stdout,
    usage_error,
)
from .output import lead


def configure_logging(verbose: bool, quiet: bool) -> None:
    """Every log line on stderr, so stdout carries the table or the envelope alone."""
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s" if not verbose else "%(name)s: %(message)s"))
    root = logging.getLogger("decktalk")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False


def named(argv: Sequence[str]) -> str:
    """The command a refused command line was trying to run, for the envelope of a usage error."""
    return next((word for word in argv if word in parser.BY_NAME), "decktalk")


def _report(command: str, outcome: Outcome, *, ok: bool, code: int, as_json: bool) -> None:
    """The one place a command's result reaches stdout, as the envelope or as the tables."""
    if as_json:
        # The payload is the result's own JSON-ready data, so each verdict is already the
        # {code, label, certain} object, in the payload and in the row `finding_rows` lifts out of it.
        payload = outcome.payload
        emit(
            envelope_of(
                command,
                __version__,
                ok=ok,
                exit_code=code,
                summary=outcome.summary,
                findings=outcome.findings,
                items=finding_rows(payload, outcome.rows),
                written=outcome.written,
                payload=payload,
            )
        )
        return
    summary = lead(outcome.summary, outcome.findings)
    print("\n".join(part for part in (summary, outcome.text) if part))


def _fail(command: str, error: dict[str, Any], *, code: int, as_json: bool) -> int:
    """Print an error as the envelope or as one stderr line, and give back its exit code."""
    if as_json:
        emit(envelope_of(command, __version__, ok=False, exit_code=code, error=error, payload=None))
    else:
        print(f"error[{error['code']}]: {error['message']}", file=sys.stderr)
        if error.get("hint"):
            print(f"  hint: {error['hint']}", file=sys.stderr)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command line and give back its exit code."""
    line_buffer_stdout()
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse(raw, __version__)
    except parser.UsageError as exc:
        return _fail(named(raw), usage_error(str(exc)), code=2, as_json="--json" in raw)
    verbose = getattr(args, "verbose", False)
    configure_logging(verbose, getattr(args, "quiet", False))
    command = parser.BY_NAME[args.cmd]
    opts = command.options.of(args)
    try:
        outcome = dispatch.run(command.name, opts)
        code = exit_for(outcome.findings, opts.strict, opts.exit_zero)
        # `ok` is true when the command found nothing and raised nothing, so it reads the tally
        # itself. It follows neither `--strict`, which decides an exit code, nor `--exit-zero`.
        ok = not outcome.findings.certain and not outcome.findings.uncertain
        _report(command.name, outcome, ok=ok, code=code, as_json=opts.json)
        if outcome.after is not None:
            # `serve` blocks until the author stops it, and the address it printed is what they
            # needed while it was running, so the envelope goes out before the server holds.
            outcome.after()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # Printing is inside this block too, because a payload a writer refuses is a bug like any
        # other, and a bug that escaped here would leave the process on exit 1 with no envelope.
        if verbose and not isinstance(exc, DeckTalkError):
            # A developer asked for the traceback of a bug, and a caller still gets exit 3, because
            # exit 1 would say the project is what to fix.
            traceback.print_exception(exc, file=sys.stderr)
        return _fail(command.name, error_of(exc, options.project_root(opts)), code=3, as_json=opts.json)
    return code
