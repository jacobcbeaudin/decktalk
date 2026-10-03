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
from typer import Context

from decktalk.cli import catalog
from decktalk.cli.app import command
from decktalk.cli.options import Group
from decktalk.results import Scope


@command(
    group=Group.CONTRACTS,
    epilog=(
        "Prints the contract itself and not a result, which is the one command whose output carries no schema, "
        "ok, findings or error."
    ),
)
def schema(
    ctx: Context,  # noqa: ARG001  (the session is made for every command, and this one needs none of it)
    name: Annotated[
        str | None,
        typer.Argument(metavar="NAME", help="A command, or finding, error, event, settings, cues or page."),
    ] = None,
    scope: Annotated[
        Scope | None,
        typer.Option("--scope", metavar="SCOPE", help="With settings, the keys one file may hold: project or machine."),
    ] = None,
) -> dict[str, Any]:
    """Print the JSON Schema of a command, a setting or an event.

    With no name it prints the whole instruction set in one object, which is every command with its
    flags, the globals, the exit codes, the error codes and the finding codes.
    """
    if name is None:
        return catalog.document()
    try:
        return catalog.named(name, scope=scope)
    except KeyError as unknown:
        raise typer.BadParameter(
            f"{name!r} names no contract. The names are {', '.join(catalog.names())}.", param_hint="NAME"
        ) from unknown


__all__ = ["schema"]
