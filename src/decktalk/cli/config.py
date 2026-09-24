"""The one noun that owns five verbs, which is why it is the one group that earns a level of nesting.

Every knob DeckTalk reads is published with a type, a default, a safe range, a unit, a hazard and
the findings it moves, and these five verbs are how an agent reads that list, reads one row of it,
changes one row and puts one row back. A write goes through the loader a run goes through, so a
value no run could use never lands in a file, and the refusal a caller meets is the loader's own.

`unset` rather than reset, because reset never says whether it means one key or the whole file,
where unset says exactly what happens: the override is removed and the layer below it wins again.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Annotated, Any, cast

import tomlkit
import typer
from pydantic import JsonValue
from typer._click import Context

from decktalk import settings as knobs
from decktalk.cli import session as sessions
from decktalk.cli.app import CONTEXT, DOCS, DeckTalkGroup, app, command
from decktalk.cli.options import Group, Where
from decktalk.errors import InputError
from decktalk.explain import Explanation
from decktalk.explain import explain as explained
from decktalk.results import (
    ConfigExplainResult,
    ConfigGetResult,
    ConfigListResult,
    ConfigSetResult,
    ConfigUnsetResult,
    Layer,
    Scope,
    SettingValue,
)
from decktalk.tomlmap import Key as KeyRecord

PURPOSE = "List, get, set, explain or unset a setting."
"""What the command tree says about the group, which is the five verbs in the order they are met."""

config = typer.Typer(
    cls=DeckTalkGroup,
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
    Where,
    typer.Option("--where", metavar="SCOPE", help="project writes decktalk.toml, machine writes this machine's file."),
]


@command("list", group=Group.CONTRACTS, to=config, epilog=f"Docs: {DOCS}#config-list")
def list_keys(
    ctx: Context,
    table: Annotated[str | None, typer.Argument(metavar="TABLE", help="One table, such as verify.")] = None,
    defaults: Annotated[bool, typer.Option("--defaults", help="List the defaults rather than what is set.")] = False,
    changed: Annotated[bool, typer.Option("--changed", help="List only the keys something overrode.")] = False,
) -> ConfigListResult:
    """Print every key, its value and the layer that set it.

    An agent cannot turn a knob it cannot enumerate, so this is the call that hands it every knob at
    once, and `config explain` is the call that reads one whole.
    """
    session = sessions.of(ctx)
    return ConfigListResult(ok=True, keys=_rows(session, table, defaults=defaults, changed=changed))


@command("get", group=Group.CONTRACTS, to=config, epilog=f"Docs: {DOCS}#config-get")
def get_key(ctx: Context, key: Named) -> ConfigGetResult:
    """Print one key's value and the layer that set it."""
    session = sessions.of(ctx)
    known = _known(key)
    here = _loaded(session)
    winner = here.layers.winner(known.id)
    return ConfigGetResult(
        ok=True,
        key=SettingValue(
            key=known.id,
            value=_json(knobs.value_of(here.settings, known.id)),
            default=_json(known.default),
            layer=winner.layer,
            file=winner.file,
        ),
    )


@command("set", group=Group.CONTRACTS, to=config, epilog=f"Docs: {DOCS}#config-set")
def set_key(
    ctx: Context,
    key: Named,
    value: Annotated[str, typer.Argument(metavar="VALUE", help="The value, spelled as a command line spells it.")],
    where: Scoped = Where.PROJECT,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report the change and write nothing.")] = False,
) -> ConfigSetResult:
    """Write one key into decktalk.toml or into this machine's file.

    The would-be file is loaded whole before it lands, so a value no run could use never reaches
    the file, and the comments a person wrote around the key are kept.
    """
    session = sessions.of(ctx)
    path = _file(session, where)
    try:
        written = knobs.write(path, key, value, scope=_scope(where), dry_run=dry_run)
    except InputError as refused:
        raise _refused(refused, "KEY") from refused
    return ConfigSetResult(
        ok=True,
        written=() if dry_run else (written.file,),
        key=written.key,
        value=written.value,
        previous=written.previous,
        scope=written.scope,
        file=written.file,
        effective=written.effective,
        layer=written.layer,
        dry_run=written.dry_run,
    )


@command("unset", group=Group.CONTRACTS, to=config, epilog=f"Docs: {DOCS}#config-unset")
def unset_key(
    ctx: Context,
    key: Named,
    where: Scoped = Where.PROJECT,
    whole: Annotated[bool, typer.Option("--all", help="Remove a whole table rather than one key.")] = False,
) -> ConfigUnsetResult:
    """Remove one key so the layer below it wins again.

    One key needs no permission. A whole table is many keys at once, so it is removed only when
    `--all` says so, and on a terminal it is confirmed first.
    """
    session = sessions.of(ctx)
    path = _file(session, where)
    if not path.exists():
        raise InputError(
            f"{path.as_posix()} is not there, so it sets nothing to remove.",
            hint=f"Run decktalk config set {key} VALUE first.",
        )
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    removed = _removed(document, key, asked=_asked(session, key, whole=whole))
    text = tomlkit.dumps(document)
    _loads(text, path, where)
    path.write_text(text, encoding="utf-8")
    return ConfigUnsetResult(ok=True, written=(path,), keys=removed, scope=_scope(where), file=path)


@command("explain", group=Group.CONTRACTS, to=config, epilog=f"Docs: {DOCS}#config-explain")
def explain_key(
    ctx: Context,
    key: Named,
    value: Annotated[
        str | None, typer.Option("--value", metavar="N", help="A candidate value, held to the same range.")
    ] = None,
) -> ConfigExplainResult:
    """Print one key whole: what it does, what may be set, and what set it.

    This is the knob read the whole instruction set rests on, because it answers the four questions
    in order: what does this change, what may I write, what happens at the edge, and which finding
    does it move.
    """
    session = sessions.of(ctx)
    root = session.flags.project or Path.cwd()
    here = root if (root / knobs.PROJECT_FILE).exists() else None
    try:
        read = _read(key, here, value)
    except InputError as refused:
        raise _refused(refused, "KEY") from refused
    winner = next((layer for layer in read.layers if layer.layer is read.winner), None)
    return ConfigExplainResult(
        ok=True,
        key=read.key,
        type=read.type,
        sentence=read.description,
        value=read.value,
        default=read.default,
        unit=read.unit,
        range=read.range,
        layer=read.winner,
        file=winner.file if winner else None,
        line=winner.line if winner else None,
        layers=read.layers,
        environment=read.environment,
        decides=read.decides,
        numbers=read.numbers,
        clamped=read.clamped,
        hazard=read.hazard,
        docs=read.docs,
    )


def _read(key: str, project: Path | None, value: str | None) -> Explanation:
    """One key explained against this project, or against the defaults when the project cannot be read.

    The explainer reads this project's resolved cue times to say what a value would clamp, and a
    project whose cue times it cannot read still has a key worth explaining, so the answer is given
    without the clamping rather than withheld.
    """
    try:
        return explained(key, project=project, value=value)
    except (TypeError, KeyError, ValueError):
        return explained(key, project=None, value=value)


def _rows(session: sessions.Session, table: str | None, *, defaults: bool, changed: bool) -> tuple[SettingValue, ...]:
    """Every published key as one row, filtered by the table and by whether anything overrode it."""
    here = _loaded(session)
    rows: list[SettingValue] = []
    for key in knobs.KEYS:
        if table and not (key.id == table or key.id.startswith(f"{table}.")):
            continue
        winner = here.layers.winner(key.id)
        if changed and winner.layer is Layer.DEFAULT:
            continue
        rows.append(
            SettingValue(
                key=key.id,
                value=_json(key.default) if defaults else _json(knobs.value_of(here.settings, key.id)),
                default=_json(key.default),
                layer=Layer.DEFAULT if defaults else winner.layer,
                file=winner.file,
            )
        )
    if table and not rows:
        raise typer.BadParameter(f"{table!r} is not a table of decktalk.toml.", param_hint="TABLE")
    return tuple(rows)


def _known(key: str) -> KeyRecord:
    """The published record of one key, or the loader's own refusal naming the nearest name."""
    found = knobs.BY_ID.get(key)
    if found is None:
        raise _refused(
            InputError(
                f"'{key}' is not a settings key.",
                hint="Run decktalk schema settings for every key DeckTalk reads.",
            ),
            "KEY",
        )
    return found


def _loaded(session: sessions.Session) -> knobs.Loaded:
    """Every layer resolved for this directory, which answers about the machine when no project is here."""
    root = session.flags.project or Path.cwd()
    return knobs.load(root if (root / knobs.PROJECT_FILE).exists() else None)


def _asked(session: sessions.Session, key: str, *, whole: bool) -> bool:
    """Whether a whole table may go, which one key never needs and a table needs `--all` or a person."""
    if whole:
        return True
    if not session.asks:
        return False
    return session.confirm(f"Remove everything {key} sets?")


def _removed(document: MutableMapping[str, Any], key: str, *, asked: bool) -> tuple[str, ...]:
    """Take one key or one whole table out of a parsed document, and say what went."""
    parts = key.split(".")
    table: Any = document
    for part in parts[:-1]:
        if not isinstance(table, Mapping) or part not in table:
            raise InputError(f"this file sets nothing under '{key}'.", hint="Run decktalk config list --changed.")
        table = table[part]
    last = parts[-1]
    if not isinstance(table, Mapping) or last not in table:
        raise InputError(f"this file does not set '{key}'.", hint="Run decktalk config list --changed.")
    going = table[last]
    if isinstance(going, Mapping):
        if not asked:
            raise InputError(
                f"'{key}' is a whole table, and removing it would take out {len(going)} keys at once.",
                hint=f"Run decktalk config unset {key} --all to remove all of them.",
            )
        names = tuple(f"{key}.{name}" for name in going)
    else:
        names = (key,)
    del cast("MutableMapping[str, Any]", table)[last]
    return names


def _loads(text: str, path: Path, where: Where) -> None:
    """Refuse a removal that would leave a file no run could load, before the file is written."""
    parsed = dict(tomlkit.parse(text))
    if where is Where.MACHINE:
        knobs.load(project={}, machine=parsed, machine_path=path)
    else:
        knobs.load(project=parsed, machine={})


def _json(value: object) -> JsonValue:
    """One value as JSON carries it, which is what a row and a schema both publish."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    return str(value)


def _file(session: sessions.Session, where: Where) -> Path:
    """The file a write lands in, which is the project's own or this machine's."""
    if where is Where.MACHINE:
        return knobs.machine_config_path()
    return (session.flags.project or Path.cwd()) / knobs.PROJECT_FILE


def _scope(where: Where) -> Scope:
    """The library's word for the file a write lands in."""
    return Scope.MACHINE if where is Where.MACHINE else Scope.PROJECT


def _refused(failure: InputError, hint: str) -> typer.BadParameter:
    """A key or a value the command line got wrong, which is a usage error rather than a broken file.

    The sentence is the loader's own, because a second wording of one refusal is a second contract.
    """
    said = f"{failure} {failure.hint}" if failure.hint else str(failure)
    return typer.BadParameter(said, param_hint=hint)


__all__ = ["config", "explain_key", "get_key", "list_keys", "set_key", "unset_key"]
