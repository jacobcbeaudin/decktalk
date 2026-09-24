"""The one command that prints a contract rather than a result.

Without it the JSON contract is discoverable only by running commands and reading what comes back,
which fails the thesis outright: an agent must be able to read the whole instruction set before it
runs anything. Two calls are enough. `decktalk schema` prints every command, every flag, every exit
code, every error code and every finding code, and `decktalk schema NAME` prints one document whole.

It is the one exemption from the envelope. A JSON Schema document inside an envelope is not that
document, and a reserved key called `schema` set to 2 inside a document about schemas is unreadable.
It reads no project, opens no socket and writes nothing.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from typer._click import Context

from decktalk.cli import catalog
from decktalk.cli.app import command, docs_for
from decktalk.cli.options import Group

SCHEMA_EPILOG = f"""\
Prints the contract itself and not a result, which is the one command whose
output carries no schema, ok, findings or error. Docs: {docs_for("schema")}"""


@command(group=Group.CONTRACTS, epilog=SCHEMA_EPILOG)
def schema(
    ctx: Context,  # noqa: ARG001  (the session is made for every command, and this one needs none of it)
    name: Annotated[
        str | None,
        typer.Argument(metavar="NAME", help="A command, or finding, error, event, settings, project or page."),
    ] = None,
    machine: Annotated[
        bool, typer.Option("--machine", help="With settings, the keys a per-machine file may hold.")
    ] = False,
) -> dict[str, Any]:
    """Print the JSON Schema of a command, a setting or an event.

    With no name it prints the whole instruction set in one object, which is every command with its
    flags, the globals, the exit codes, the error codes and the finding codes.
    """
    if name is None:
        return catalog.document()
    try:
        return catalog.named(name, machine=machine)
    except KeyError as unknown:
        raise typer.BadParameter(
            f"{name!r} names no contract. The names are {', '.join(catalog.names())}.", param_hint="NAME"
        ) from unknown


__all__ = ["schema"]
