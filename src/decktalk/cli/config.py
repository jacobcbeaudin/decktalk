"""The one noun that owns five verbs, which is why it is the one group that earns a level of nesting.

Every setting DeckTalk reads is published with a type, a default, a safe range, a unit, a hazard and
the findings it moves, and these five verbs are how an agent reads that list, reads one row of it,
changes one row and puts one row back. A write goes through the loader a run goes through, so a
value no run could use never lands in a file, and the refusal a caller meets is the loader's own.

`unset` rather than reset, because reset never says whether it means one key or the whole file,
where unset says exactly what happens: the override is removed and the layer below it wins again.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
from typer import Context

from decktalk import settings
from decktalk.cli import session as sessions
from decktalk.cli.app import CONTEXT, DeckTalkGroup, app, command
from decktalk.cli.options import Group, answering
from decktalk.errors import InputError
from decktalk.events import Level
from decktalk.explain import explain as explained
from decktalk.results import (
    ConfigExplainResult,
    ConfigGetResult,
    ConfigListResult,
    ConfigSetResult,
    ConfigUnsetResult,
    Scope,
)
from decktalk.settings import edit, layers

SENTENCE_ENDS = (".", "?", "!")
"""The marks a refusal's own sentence may already end on, which is when no full stop is added."""

PURPOSE = "List, get, set, explain or unset a setting."
"""What the command tree says about the group, which is the five verbs in the order they are met."""

config = typer.Typer(
    cls=DeckTalkGroup,
    name="config",
    help=PURPOSE,
    add_completion=False,
    pretty_exceptions_enable=False,
    rich_markup_mode=None,
    context_settings=CONTEXT,
)
"""The one nested group, whose five verbs all act on the same published key space."""

app.add_typer(config, name="config", rich_help_panel=Group.CONTRACTS.value)

Named = Annotated[str, typer.Argument(metavar="KEY", help="The key's dotted name, such as video.crf.")]
Scoped = Annotated[
    Scope,
    typer.Option("--scope", metavar="SCOPE", help="project writes decktalk.toml, machine writes this machine's file."),
]


@command("list", group=Group.CONTRACTS, to=config)
def list_keys(
    ctx: Context,
    table: Annotated[str | None, typer.Argument(metavar="TABLE", help="One table, such as verify.")] = None,
    defaults: Annotated[bool, typer.Option("--defaults", help="List the defaults rather than what is set.")] = False,
    changed: Annotated[bool, typer.Option("--changed", help="List only the keys something overrode.")] = False,
) -> ConfigListResult:
    """Print every key, its value and the layer that set it.

    It hands over every key at once, and `config explain` reads one whole.
    \f
    An agent cannot change a setting it cannot enumerate, so this is the call that hands it every setting at
    once.
    """
    session = sessions.of(ctx)
    try:
        with _told(session):
            listed = edit.rows(_loaded(session), table, defaults=defaults, changed=changed)
            return ConfigListResult(ok=True, keys=listed)
    except InputError as refused:
        raise _refused(refused, param="TABLE") from refused


@command("get", group=Group.CONTRACTS, to=config)
def get_key(ctx: Context, key: Named) -> ConfigGetResult:
    """Print one key's value and the layer that set it."""
    session = sessions.of(ctx)
    try:
        known = settings.key_named(key)
    except InputError as refused:
        raise _refused(refused) from refused
    with _told(session):
        here = _loaded(session)
    return ConfigGetResult(ok=True, setting=edit.setting_value(here, known))


@command("set", group=Group.CONTRACTS, to=config)
def set_key(
    ctx: Context,
    key: Named,
    value: Annotated[str, typer.Argument(metavar="VALUE", help="The value, spelled as a command line spells it.")],
    scope: Scoped = Scope.PROJECT,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report the change and write nothing.")] = False,
) -> ConfigSetResult:
    """Write one key into decktalk.toml or into this machine's file.

    The would-be file is loaded whole before it lands, so a value no run could use never reaches
    the file, and the comments a person wrote around the key are kept.
    """
    session = sessions.of(ctx)
    path = _file(session, scope)
    try:
        with _told(session):
            return edit.write(path, key, value, scope=scope, environ=session.machine.environ, dry_run=dry_run)
    except InputError as refused:
        raise _refused(refused) from refused


@command("unset", group=Group.CONTRACTS, to=config)
def unset_key(
    ctx: Context,
    key: Named,
    scope: Scoped = Scope.PROJECT,
    table: Annotated[bool, answering("--table", help="Remove a whole table rather than one key.")] = False,
) -> ConfigUnsetResult:
    """Remove one key so the layer below it wins again.

    One key needs no permission. A whole table is many keys at once, so it is removed only when
    `--table` says so, and on a terminal it is confirmed first.

    A table is every key the file states under it, removed in one write, and the value that now
    applies is the first key's, because the three scalars describe one key and `keys` names the rest.
    \f
    The removal itself is the settings layer's, because editing a validated file is library work and
    a second editor here would be a second thing to keep true.
    """
    session = sessions.of(ctx)
    try:
        edit.key_or_table(key)
    except InputError as refused:
        raise _refused(refused) from refused
    path = _file(session, scope)
    if not path.exists():
        raise InputError(
            f"{path.as_posix()} is not there, so it sets nothing to remove.",
            hint=f"Run decktalk config set {key} VALUE first.",
        )
    whole = key in settings.BY_ID or session.approve(table or None, f"Remove everything {key} sets?")
    going = edit.stating(path, key, whole_table=whole)
    with _told(session):
        return edit.unset(path, *going, scope=scope, environ=session.machine.environ)


@command("explain", group=Group.CONTRACTS, to=config)
def explain_key(
    ctx: Context,
    key: Named,
    candidate: Annotated[
        str | None, typer.Option("--candidate", metavar="N", help="A candidate value, held to the same range.")
    ] = None,
) -> ConfigExplainResult:
    """Print one key whole: what it does, what may be set, and what set it.

    It answers four questions in order: what does this change, what may I write, what happens at
    the edge, and which finding does it move.
    \f
    Explaining one setting is the read the whole instruction set rests on.
    """
    session = sessions.of(ctx)
    root = _root(session)
    here = root if (root / settings.PROJECT_FILE).exists() else None
    try:
        with _told(session):
            return explained(key, project=here, value=candidate, machine=session.machine)
    except InputError as refused:
        raise _refused(refused) from refused


@contextmanager
def _told(session: sessions.Session) -> Iterator[None]:
    """Say what the machine and the project file hold that DeckTalk does not read, then run the verb.

    A misspelled key or `DECKTALK_` variable is ignored, so the value these verbs report is the
    default it left in force, and the sentence that names the typo is what explains that default. A
    verb opens a run on the machine for that reason alone, because a run is where the machine says
    what it noticed and where a renderer is listening.
    """
    root = _root(session)
    project = layers.read_project_toml(root) if (root / settings.PROJECT_FILE).is_file() else {}
    with session.watching(session.machine.events), session.machine._run() as run:
        for note in layers.key_warnings(project, settings.PROJECT_FILE):
            run.note(note, level=Level.WARNING)
        yield


def _root(session: sessions.Session) -> Path:
    """The project directory these verbs act on, which is the one `-p` names or the working directory."""
    return session.flags.project or Path.cwd()


def _loaded(session: sessions.Session) -> settings.Loaded:
    """Every layer resolved for this directory, which answers about the machine when no project is here."""
    root = _root(session)
    machine = session.machine
    return layers.load(
        root if (root / settings.PROJECT_FILE).exists() else None,
        machine=machine.tables,
        machine_path=machine.machine_file,
        environ=machine.environ,
    )


def _file(session: sessions.Session, scope: Scope) -> Path:
    """The file a write lands in, which is the project's own or this machine's."""
    if scope is Scope.MACHINE:
        return session.machine.machine_file
    return _root(session) / settings.PROJECT_FILE


def _refused(failure: InputError, *, param: str = "KEY") -> typer.BadParameter:
    """A key or a value the command line got wrong, which is a usage error rather than a broken file.

    The sentence is the loader's own, because a second wording of one refusal is a second contract.
    The loader's hint follows it as a sentence of its own, so a refusal that ends on the value it
    got never runs into the reason that value was refused.
    """
    said = str(failure)
    if failure.hint:
        said = f"{said if said.endswith(SENTENCE_ENDS) else f'{said}.'} {failure.hint}"
    return typer.BadParameter(said, param_hint=param)


__all__ = ["config", "explain_key", "get_key", "list_keys", "set_key", "unset_key"]
