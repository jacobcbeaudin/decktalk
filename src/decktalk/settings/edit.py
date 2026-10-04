"""One key written into, or removed from, a settings file, judged whole before the file is replaced.

`config set`, `config unset` and a fix that sets a key all write a file here. A value is read by
`read_value`, the rule `--set` and an environment variable meet too, so each one meets the same
refusal, and a file is never left in a shape the loader would refuse.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import tomlkit
from tomlkit.exceptions import ParseError

from decktalk.errors import InputError
from decktalk.files import current_text, replace_all
from decktalk.findings import Location
from decktalk.locate import refused_line
from decktalk.results import ConfigSetResult, ConfigUnsetResult, Layer, Scope, SettingValue, counted
from decktalk.settings import BY_ID, KEYS, PROJECT_FILE, key_named, not_a_key
from decktalk.settings.layers import (
    ABSENT,
    Loaded,
    json_value,
    load,
    not_toml,
    read_toml,
    refuse_off_scope,
    stated,
    value_of,
)
from decktalk.tomlmap import Key
from decktalk.tomlmap.read import read_value


def parse_value(key: Key, text: str) -> object:
    """One value as a command line spells it, read as the key's own type and held to its range.

    It is read the way an environment variable is, through `read_value`, which is the rule `--set`
    meets too, so both meet the same refusal.
    """
    return read_value(
        key.annotation,
        text,
        where=key.id,
        bounds=key.bounds,
        hazard=key.hazard,
        from_env=True,
        said="config set",
    )


def _scoped_key(key: str, scope: Scope, *, action: str, rerun: str) -> Key:
    """The key a write or a removal names, refused when no key has that name or it belongs in the other file."""
    known = key_named(key)
    if known.scope is not scope:
        raise InputError(
            f"'{key}' is {known.scope.value}-scoped, so it cannot be {action} the {scope.value} file.",
            hint=f"Run `{rerun} --scope {known.scope.value}`.",
        )
    return known


@dataclass(frozen=True)
class Edited:
    """One settings file's text with one key set in it, and what that key held before.

    It is text rather than a written file, because a fix that also edits lines of the same file
    stages both changes before either lands, and the whole file is judged once they are all made.
    """

    text: str
    value: object
    previous: object


def edit(text: str, key: str, value: str, *, scope: Scope, file: Path) -> Edited:
    """Set one key in the text of one settings file, keeping every comment the file already has.

    The key is refused when no key has that name, when it belongs in the other file, or when the
    value is not one the key takes. The document is edited rather than rewritten, because a person
    wrote the comments around the key and a writer that dumped a parsed tree would delete them the
    first time an agent changed a setting. The text that comes back is not yet validated as a whole,
    because a caller may have more changes to make to it first.
    """
    known = _scoped_key(key, scope, action="written to", rerun=f"decktalk config set {key} {value}")
    typed = parse_value(known, value)
    document = _document(text, file)
    previous = stated(document, key)
    _put(document, key.split("."), typed)
    return Edited(text=tomlkit.dumps(document), value=typed, previous=previous)


def write(
    path: Path,
    key: str,
    value: str,
    *,
    scope: Scope,
    environ: Mapping[str, str],
    dry_run: bool = False,
) -> ConfigSetResult:
    """Set one key in one file, through the whole loader, keeping every comment the file already has.

    The would-be file is built first and loaded whole, so a value that no run could use never lands
    and the refusal a caller meets is the loader's own, with its file, its line and its near name.
    The file is then replaced whole rather than written in place, so a write that fails leaves it as
    it was.

    `environ` is the machine's environment, which is the layer over the file that decides whether
    the value written is the value in force. The answer is `config set`'s own result, so the command
    renders what the library returns.
    """
    target = _target(path, scope)
    edited = edit(current_text(target), key, value, scope=scope, file=path)
    validate(edited.text, path, scope)
    if not dry_run:
        replace_all({target: edited.text})
    # A write that a higher layer shadows changes the file and not the run, so the result says so
    # rather than reporting a new value the next command will not use.
    tree = _in_force(path, scope, {key: edited.value}, environ)
    return ConfigSetResult(
        ok=True,
        written=() if dry_run else (path,),
        key=key,
        value=json_value(edited.value),
        previous=None if edited.previous is ABSENT else json_value(edited.previous),
        scope=scope,
        file=path,
        dry_run=dry_run,
        effective=json_value(value_of(tree.settings, key)),
        layer=tree.layers.winner(key).layer,
    )


def unset(path: Path, key: str, *more: str, scope: Scope, environ: Mapping[str, str]) -> ConfigUnsetResult:
    """Take one key, or several, out of one file, so the layer below each decides again.

    This is the writer's opposite and it is built the same way: the would-be file is loaded whole
    before a byte lands, so a removal that breaks a relation between two keys never reaches the
    disk, and the document is edited rather than rewritten so the comments a person wrote around the
    key survive. Several keys are one edit and one write, so a table is removed whole or not at all.
    A key the file never stated is taken out of nothing and the call says so, which is what lets an
    agent that cannot read the file call this twice. `previous`, `effective` and `layer` describe
    the first key.
    """
    keys = (key, *more)
    for one in keys:
        _scoped_key(one, scope, action="taken out of", rerun=f"decktalk config unset {one}")
    target = _target(path, scope)
    document = _document(current_text(target), path)
    stating = [one for one in keys if stated(document, one) is not ABSENT]
    previous = stated(document, key)
    for one in stating:
        _take(document, one.split("."))
    if stating:
        text = tomlkit.dumps(document)
        validate(text, path, scope)
        replace_all({target: text})
    tree = _in_force(path, scope, {}, environ)
    return ConfigUnsetResult(
        ok=True,
        written=(path,) if stating else (),
        keys=keys,
        previous=None if previous is ABSENT else json_value(previous),
        scope=scope,
        file=path,
        effective=json_value(value_of(tree.settings, key)),
        layer=tree.layers.winner(key).layer,
    )


def _target(path: Path, scope: Scope) -> Path:
    """The file a settings change replaces, which is `path` with every link followed.

    A link is followed so a machine file kept in a dotfiles repository stays where its owner keeps
    it. A project file that leads out of the project is refused, because a project someone else
    wrote would otherwise choose a file elsewhere on the machine for a fix or `config set` to write.
    A hard link needs no refusal, because the change replaces the file rather than writing into it,
    so the other name keeps its own contents.
    """
    target = path.resolve()
    if scope is Scope.PROJECT and not target.is_relative_to(path.parent.resolve()):
        raise InputError(
            f"{path.name} leads outside the project, so DeckTalk writes nothing through it.",
            hint=f"Replace the link at {path.name} with the file itself.",
            location=Location(where=path.name, file=Path(path.name)),
        )
    return target


def _document(text: str, file: Path) -> tomlkit.TOMLDocument:
    """A settings file parsed for editing, or the loader's own refusal when it is not valid TOML."""
    try:
        return tomlkit.parse(text)
    except ParseError as exc:
        raise not_toml(file, exc, exc.line) from exc


def _put(document: MutableMapping[str, Any], parts: list[str], value: object) -> None:
    """One key set in a parsed document, adding the tables it sits in when they are not there yet."""
    table = document
    for part in parts[:-1]:
        if part not in table:
            table[part] = tomlkit.table()
        table = cast("MutableMapping[str, Any]", table[part])
    table[parts[-1]] = value


def _take(document: MutableMapping[str, Any], parts: list[str]) -> None:
    """One key taken out of a parsed document, leaving the table it sat in where it was.

    A table that the removal empties stays, because a person's comments live around the table and a
    remover that deleted it would delete the sentences they wrote the first time they cleared a key.
    """
    table = document
    for part in parts[:-1]:
        table = cast("MutableMapping[str, Any]", table[part])
    del table[parts[-1]]


def validate(text: str, path: Path, scope: Scope) -> None:
    """The whole settings tree built on the would-be file, so a bad value never reaches the disk.

    A fix may also edit the lines of the file around a key, so the text is parsed here as well as
    loaded, and a line edit that breaks the TOML is refused as the loader would refuse it.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InputError(
            f"{path.name} would not be valid TOML: {exc}.",
            hint="The change was not made, so the file is as it was.",
            location=Location(where=path.name, file=path, line=refused_line(exc)),
        ) from exc
    if scope is Scope.MACHINE:
        refuse_off_scope(data, Scope.MACHINE, file=path, text=text)
    load(
        machine=data if scope is Scope.MACHINE else {},
        project=data if scope is Scope.PROJECT else {},
        environ={},
    )


def _in_force(path: Path, scope: Scope, stated: Mapping[str, object], environ: Mapping[str, str]) -> Loaded:
    """The whole tree as it stands once a write or a removal has landed in the named file.

    Only the key the call touched is handed back to the loader, because no other key in that file
    decides this one: the layer under a settings file is the default, and the layers over it are the
    environment and the run's own overrides. A key is scoped to one file, so the file the call left
    alone states nothing about it either.
    """
    return load(
        machine=nested(stated) if scope is Scope.MACHINE else {},
        project=nested(stated) if scope is Scope.PROJECT else {},
        machine_path=path if scope is Scope.MACHINE else None,
        environ=environ,
    )


def nested(flat: Mapping[str, object]) -> dict[str, Any]:
    """Dotted keys as the nested tables a layer is read from."""
    out: dict[str, Any] = {}
    for dotted, value in flat.items():
        table = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            table = table.setdefault(part, {})
        table[parts[-1]] = value
    return out


def under(published: str, named: str) -> bool:
    """Whether one published key is the key a caller named, or one of the keys of the table they named."""
    return published == named or published.startswith(f"{named}.")


def key_or_table(named: str) -> None:
    """Refuse a name that is neither a key nor a table, before any file is read for it.

    `unset` removes a key or a whole table, so either is a name it takes. A name that is neither is
    the caller's slip, which is refused the way a reader of one key refuses it rather than reported
    as a file that happens not to state it.
    """
    if not any(under(one.id, named) for one in KEYS):
        raise not_a_key(named)


def setting_value(loaded: Loaded, key: Key, *, defaults: bool = False) -> SettingValue:
    """One key as one row: the value in force and the layer that set it, or the default alone under `defaults`."""
    winner = loaded.layers.winner(key.id)
    return SettingValue(
        key=key.id,
        value=json_value(key.default if defaults else value_of(loaded.settings, key.id)),
        default=json_value(key.default),
        layer=Layer.DEFAULT if defaults else winner.layer,
        file=winner.file,
    )


def rows(loaded: Loaded, table: str | None, *, defaults: bool, changed: bool) -> tuple[SettingValue, ...]:
    """Every published key as one row, filtered by the table and by whether anything overrode it."""
    found = tuple(
        setting_value(loaded, key, defaults=defaults)
        for key in KEYS
        if not (table and not under(key.id, table))
        and not (changed and loaded.layers.winner(key.id).layer is Layer.DEFAULT)
    )
    if table and not found:
        raise InputError(f"{table!r} is not a table of {PROJECT_FILE}.", hint="Run decktalk config list.")
    return found


def stating(path: Path, named: str, *, whole_table: bool) -> tuple[str, ...]:
    """Every published key this file states under the name a caller gave, in the order the tables declare them.

    A caller names one key or one table, and a table is refused unless `whole_table` says to take it,
    because a table is many keys at once and a person who typed one word meant one thing.
    """
    document = read_toml(path)
    going = tuple(one.id for one in KEYS if under(one.id, named) and stated(document, one.id) is not ABSENT)
    if not going:
        raise InputError(f"{path.name} sets nothing under '{named}'.", hint="Run decktalk config list --changed.")
    if named not in BY_ID and not whole_table:
        raise InputError(
            f"'{named}' is a whole table, and removing it would take out {counted(len(going), 'key')} at once.",
            hint=f"Run decktalk config unset {named} --table to remove all of them.",
        )
    return going
