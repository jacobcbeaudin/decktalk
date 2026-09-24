"""The one table: a command is its function's signature, and everything else is read off that.

Under argparse the command line was a tuple of rows beside the functions. Under Typer the row is
the function itself. Its parameters are its own flags, its return annotation is the result model it
answers with, and the flags it shares with other commands are derived from that annotation, so there
is no side table to drift from the functions it describes.

One walker over the parser feeds three renderings, which are `--help`, the generated reference page
and `decktalk schema`. A command never prints: it returns its result and the session renders it, so
the table, the JSON object and the exit code are one decision made in one place.

Nothing under `cli/` imports a stage at module level, which is what keeps `decktalk schema` and a
refused `--set` as cheap as reading a signature.
"""

from __future__ import annotations

import functools
import inspect
import itertools
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, cast

import typer
from typer._click import Context, HelpFormatter, Parameter
from typer._click.core import Command
from typer._click.exceptions import ClickException, UsageError
from typer.core import TyperCommand, TyperGroup, TyperOption
from typer.main import get_command

from decktalk import __version__
from decktalk.cli.options import DOCS, GLOBALS, SHARED, FailOn, Group, Panel, When, allowed, shared_for
from decktalk.cli.session import Globals, Session
from decktalk.errors import Cancelled, DeckTalkError, ErrorCode, ErrorInfo
from decktalk.findings import Code
from decktalk.results import Result

PROGRAM = "decktalk"
"""The name every usage line and every hint spells, whatever the file the entry point is in."""

PURPOSE = (
    "Every picture lands on its word. DeckTalk turns a markdown script, HTML slides and your voice "
    "into one narrated mp4."
)

EPILOG = """\
Every command prints one JSON object with --json, carrying its own fields
beside schema, ok, run, findings and error. Run decktalk schema for the whole
contract in one call, and decktalk config explain KEY for a setting's
sentence, range and default. Exit codes: 0 found nothing, 1 found something,
2 refused the command line, 3 could not run, 130 interrupted.
Docs: https://docs.decktalk.ai/reference/cli"""

SHARED_LINE = "-p, --json, --events, --color, --no-input, -v and -q work on every command."
"""The one line a command's help spends on the eight flags every command carries."""

HELP_SENTENCE = "Print this help and exit."
"""What `-h` says, which is a sentence rather than Click's own line about showing a message."""

PROMPT_FLAGS = frozenset(
    {
        "--all",
        "--defaults",
        "--example",
        "--fix",
        "--max-cost",
        "--name",
        "--no-fix",
        "--no-skills",
        "--no-voice",
        "--overwrite",
        "--replace-voiced",
        "--spend",
    }
)
"""Every flag that answers a prompt, which is what a refused `--yes` names back at its caller."""

PANELS: tuple[str, ...] = tuple(panel.value for panel in Panel)
"""The order a command's own option panels are read in, which is the order a run meets them."""

_current: Session | None = None
"""This process's session, held so that a refusal made before a command ran still renders."""

_order = itertools.count()
"""Where each command sits in the source, which is the order its group prints it in."""

CONTEXT = {"help_option_names": ["-h", "--help"], "show_default": False}
"""Settings every command shares. A default is written into its own sentence, never in brackets."""


class Quiet:
    """The help shared by the two formatters below, which is the one place a default is not bracketed.

    Every default that matters is written into the sentence that explains it, in the words a reader
    needs rather than in a bracket, so Click's own bracket is turned off once here rather than on
    every option.
    """

    def get_params(self, ctx: Context) -> list[Parameter]:
        """Every parameter, with the bracketed default turned off before anything renders one."""
        params: list[Parameter] = super().get_params(ctx)  # ty: ignore[unresolved-attribute]
        for param in params:
            if isinstance(param, TyperOption):
                param.show_default = False
        return params


class DeckTalkCommand(Quiet, TyperCommand):
    """One command, whose options are printed under the headings the command itself declared."""

    def format_options(self, ctx: Context, formatter: HelpFormatter) -> None:
        """Write the options in their panels, with the ones in no panel last."""
        rows: dict[str, list[tuple[str, str]]] = {}
        for param in self.get_params(ctx):
            record = param.get_help_record(ctx)
            if record is None:
                continue
            rows.setdefault(getattr(param, "rich_help_panel", None) or "Options", []).append(record)
        for name in (*PANELS, "Options"):
            if rows.get(name):
                with formatter.section(name):
                    formatter.write_dl(rows[name])

    def format_epilog(self, ctx: Context, formatter: HelpFormatter) -> None:  # noqa: ARG002  (Click's signature)
        """Write the epilog as it was written, because its lines are already the width they want."""
        _write_block(formatter, SHARED_LINE, indent=True)
        if self.epilog:
            _write_block(formatter, self.epilog, indent=False)

    def get_help_option(self, ctx: Context) -> TyperOption | None:
        """The help option with our own sentence on it."""
        return _help_option(super().get_help_option(ctx))


class DeckTalkGroup(Quiet, TyperGroup):
    """The root command, whose subcommands are printed under the five headings of the tree."""

    def format_commands(self, ctx: Context, formatter: HelpFormatter) -> None:
        """Write the command tree grouped, which is how a reader learns what the tool is for.

        One name column is used across every group, so a reader's eye keeps one line down the page
        rather than a different one under each heading.
        """
        rows: dict[str, list[tuple[int, str, str]]] = {}
        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            panel = getattr(command, "rich_help_panel", None) or "Commands"
            rows.setdefault(panel, []).append((written(command), name, command.get_short_help_str(formatter.width - 6)))
        column = max((len(name) for group in rows.values() for _, name, _ in group), default=0)
        for group in (*(member.value for member in Group), "Commands"):
            if rows.get(group):
                with formatter.section(group):
                    formatter.write_dl([(name.ljust(column), help) for _, name, help in sorted(rows[group])])

    def format_epilog(self, ctx: Context, formatter: HelpFormatter) -> None:  # noqa: ARG002  (Click's signature)
        """Write the footer as it was written, which is the contract a reader leaves the page with."""
        if self.epilog:
            _write_block(formatter, self.epilog, indent=False)

    def get_help_option(self, ctx: Context) -> TyperOption | None:
        """The help option with our own sentence on it."""
        return _help_option(super().get_help_option(ctx))


def written(command: Command) -> int:
    """Where one command was written, which is its own place or the first place inside a group."""
    inside = getattr(command, "commands", None)
    if inside:
        return min(written(each) for each in inside.values())
    return int(getattr(command.callback, "order", 0))


def _help_option(option: TyperOption | None) -> TyperOption | None:
    """One help option with our sentence written onto it, whichever command asked for it."""
    if option is not None:
        option.help = HELP_SENTENCE
    return option


def _write_block(formatter: HelpFormatter, text: str, *, indent: bool) -> None:
    """Write a block of already-wrapped lines, which a rewrap would turn into a different paragraph."""
    formatter.write_paragraph()
    prefix = " " * 2 if indent else ""
    for line in text.splitlines():
        formatter.write(f"{prefix}{line}\n" if line else "\n")


app = typer.Typer(
    cls=DeckTalkGroup,
    name=PROGRAM,
    help=PURPOSE,
    epilog=EPILOG,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
    invoke_without_command=True,
    context_settings=CONTEXT,
)
"""The parser, the help and the dispatch, all of which are read from the command functions below."""


def command[F: Callable[..., object]](
    name: str | None = None,
    *,
    group: Group,
    to: typer.Typer | None = None,
    epilog: str = "",
    short_help: str = "",
) -> Callable[[F], F]:
    """Register one command, whose parameters are its own and whose shared flags are derived.

    `eval_str` is load bearing: the modules are written under postponed annotations, so the return
    annotation is the string `"BuildResult"` until it is evaluated, and the shared flags are chosen
    from the model it names.
    """

    def register(fn: F) -> F:
        signature = inspect.signature(fn, eval_str=True)
        result = signature.return_annotation
        parameters = [*signature.parameters.values(), *shared_for(result)]
        wrapper = _client(fn, name or str(getattr(fn, "__name__", "")))
        wrapper.__signature__ = signature.replace(  # ty: ignore[unresolved-attribute]
            parameters=parameters, return_annotation=inspect.Signature.empty
        )
        wrapper.__annotations__ = {param.name: param.annotation for param in parameters}
        wrapper.result = result  # ty: ignore[unresolved-attribute]
        wrapper.order = next(_order)  # ty: ignore[unresolved-attribute]
        (to or app).command(
            name or str(getattr(fn, "__name__", "")),
            cls=DeckTalkCommand,
            rich_help_panel=group.value,
            epilog=epilog or None,
            short_help=short_help or None,
        )(wrapper)
        return fn

    return register


def _client(fn: Callable[..., object], name: str) -> Callable[..., int]:
    """Wrap one command so that it takes its own parameters alone and answers with an exit code.

    The shared flags are taken off the call here, which is what lets a command read none of them and
    lets `decktalk --json build` and `decktalk build --json` mean the same thing.
    """

    @functools.wraps(fn)
    def wrapper(**arguments: object) -> int:
        context = cast("Context", arguments.pop("ctx"))
        shared = {key: arguments.pop(key) for key in list(arguments) if key in SHARED}
        session = _begin(context, shared, command=name)
        if shared.get("yes"):
            raise _yes_refused(context)
        session.judging(
            fail_on=cast("FailOn", shared.get("fail_on") or FailOn.CERTAIN),
            allow=allowed(cast("Sequence[Code] | None", shared.get("allow"))),
        )
        session.spending(
            no_voice=bool(shared.get("no_voice")),
            spend=bool(shared.get("spend")),
            max_cost=cast("float | None", shared.get("max_cost")),
        )
        answered = fn(ctx=context, **arguments)
        if isinstance(answered, Result):
            return session.report(answered)
        return session.document(answered)

    return wrapper


def _begin(context: Context, shared: dict[str, object], *, command: str) -> Session:
    """This command's session, which is the root's flags with the ones after the command name on top."""
    global _current  # noqa: PLW0603  (one process runs one command, and a refusal must still render)
    base = context.find_root().obj
    flags = base.flags if isinstance(base, Session) else Globals()
    merged = flags.merged({key: value for key, value in shared.items() if key in _GLOBAL_NAMES})
    session = Session(merged, command=command)
    context.obj = session
    _current = session
    return session


_GLOBAL_NAMES = frozenset(name for name, _, _ in GLOBALS)
"""The shared names that are globals rather than families, which are the ones a command may restate."""


def _yes_refused(context: Context) -> UsageError:
    """The refusal `--yes` earns, which names this command's own prompt flags rather than a topic.

    One token that authorises a spend, an overwrite and a lost take is how an agent burns credits it
    was told to ask about, so the flag is recognised and refused rather than left unknown.
    """
    named = sorted(
        flag
        for param in context.command.get_params(context)
        for flag in (*param.opts, *param.secondary_opts)
        if flag in PROMPT_FLAGS
    )
    answers = ", ".join(named) if named else "no flag, because this command asks nothing"
    return UsageError(f"--yes answers nothing. The prompts of this command are answered by {answers}.", ctx=context)


def _version(value: bool) -> None:
    """Print the version and exit, which is eager so that no project is read to answer it."""
    if not value:
        return
    typer.echo(__version__)
    raise typer.Exit(0)


@app.callback()
def root(
    ctx: Context,
    project: Annotated[
        str | None,
        typer.Option(
            "-p",
            "--project",
            metavar="DIR",
            help="The project directory. Default: DECKTALK_PROJECT, else the current directory.",
        ),
    ] = None,
    json_out: Annotated[
        bool, typer.Option("--json", help="Print one JSON object on stdout and nothing else there.")
    ] = False,
    events: Annotated[bool, typer.Option("--events", help="Print one JSON line per progress event on stderr.")] = False,
    color: Annotated[
        When, typer.Option("--color", metavar="WHEN", help="auto, always or never. Default: auto.")
    ] = When.AUTO,
    no_input: Annotated[
        bool,
        typer.Option(
            "--no-input",
            help="Never prompt. Take the safe default, or refuse and name the flag that would have answered.",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("-v", "--verbose", help="Debug lines on stderr, and the traceback of a bug.")
    ] = False,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Warnings and errors only on stderr.")] = False,
    version: Annotated[  # noqa: ARG001  (the eager callback reads it and exits before the body runs)
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Print the version and exit."),
    ] = False,
) -> int:
    """Every picture lands on its word. DeckTalk turns a markdown script, HTML slides and your voice
    into one narrated mp4."""
    global _current  # noqa: PLW0603  (one process runs one command, and a refusal must still render)
    flags = Globals(
        project=Path(project) if project else None,
        json_out=json_out,
        events=events,
        color=color,
        no_input=no_input,
        verbose=verbose,
        quiet=quiet,
    )
    session = Session(flags, command="")
    ctx.obj = session
    _current = session
    if ctx.invoked_subcommand is None:
        # The help is written as Click formatted it, because a renderer that rewrapped it would
        # print a different page from the one `--help` prints.
        session.err.file.write(ctx.get_help() + "\n")
        session.err.file.write(f"\nProject: {(flags.project or Path.cwd()).as_posix()}\n")
        raise typer.Exit(0)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command line and give back its exit code, with every refusal rendered as one block.

    `standalone_mode` is off because Typer would otherwise print its own panel and exit 2 with no
    object on stdout, which is the one thing `--json` promises never happens.
    """
    global _current  # noqa: PLW0603  (one process runs one command, and a refusal must still render)
    arguments = list(sys.argv[1:] if argv is None else argv)
    _current = Session(Globals(json_out="--json" in arguments), command="")
    parser = get_command(app)
    try:
        answered = parser.main(args=arguments, prog_name=PROGRAM, standalone_mode=False)
    except (UsageError, ClickException) as refused:
        return _session().reported(_usage(refused))
    except (typer.Abort, KeyboardInterrupt):
        return _session().failed(Cancelled("the caller stopped the run."))
    except DeckTalkError as refused:
        return _session().failed(refused)
    except Exception as failure:  # noqa: BLE001  (anything else is a bug, reported as one)
        return _session().bug(failure)
    return int(answered or 0)


def _session() -> Session:
    """The session a refusal renders through, which is this run's own or a bare one."""
    return _current if _current is not None else Session(Globals(), command="")


def _usage(refused: ClickException) -> ErrorInfo:
    """A refused command line as one error object, naming the command that was being parsed.

    No usage block is printed above it, because the block repeats what `--help` says better and the
    reader needs the sentence saying what was wrong with what they wrote.
    """
    where = getattr(refused, "ctx", None)
    path = where.command_path if where is not None else PROGRAM
    return ErrorInfo(
        code=ErrorCode.USAGE,
        message=f"{path}: {refused.format_message()}",
        hint=f"Run {path} --help for this command's flags, or decktalk schema for the whole contract.",
        docs=ErrorCode.USAGE.url,
    )


__all__ = ["DOCS", "PROGRAM", "app", "command", "main"]
