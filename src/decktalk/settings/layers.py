"""The five layers that set a key, read and merged into one settings tree with the record of who set each.

The default, the per-machine file, the project's `decktalk.toml`, the `DECKTALK_<TABLE>_<KEY>`
variable and a `--set` for one run, lowest to highest. Each file is held to its own scope, every key
nobody reads is warned about with the key it most likely meant, and the relations one key declares
against another are enforced once the whole tree is built.
"""

from __future__ import annotations

import functools
import operator
import os
import sys
import tomllib
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue
from pydantic_core import to_jsonable_python

from decktalk.errors import InputError
from decktalk.files import current_text
from decktalk.findings import Location
from decktalk.locate import locate, refused_line
from decktalk.results import Layer, LayerValue, Scope
from decktalk.settings import (
    BY_ID,
    DOCUMENT_TABLES,
    KEYS,
    MACHINE_FILE,
    MACHINE_FILE_VARIABLE,
    PROJECT_FILE,
    SHARED_TABLES,
    STANDALONE_ENV,
    Layers,
    Loaded,
    Settings,
    key_named,
)
from decktalk.settings.numbers import NUMBERS_BY_ID
from decktalk.tomlmap import ENV_PREFIX, variable
from decktalk.tomlmap.read import from_mapping
from decktalk.tomlmap.suggest import named_key, unknown_key_message, unknown_key_warnings


def machine_config_path(environ: Mapping[str, str], home: Path, platform: str = sys.platform) -> Path:
    """The per-machine settings file this environment names, which DECKTALK_MACHINE_FILE moves.

    The environment and the home directory are arguments, because the machine that owns them is the
    one reader of the process, and a host that builds its machine by hand names its own file.
    """
    override = environ.get(MACHINE_FILE_VARIABLE)
    if override:
        return Path(override)
    if platform == "darwin":
        root = home / "Library" / "Application Support"
    elif platform == "win32":
        root = Path(environ.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        root = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    return root / "decktalk" / MACHINE_FILE


def read_toml(path: Path) -> dict[str, Any]:
    """One TOML file parsed, or {} when it is absent. Malformed TOML is refused with its own line."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise not_toml(path, exc, refused_line(exc)) from exc


def not_toml(path: Path, exc: Exception, line: int | None) -> InputError:
    """The refusal of a settings file that is not valid TOML, at the line the parser names."""
    return InputError(
        f"{path.name} is not valid TOML: {exc}.",
        hint="Fix the line this message names, which is usually a quote or a bracket left open.",
        location=Location(where=path.name, file=path, line=line),
    )


def read_project_toml(root: Path) -> dict[str, Any]:
    """The project's `decktalk.toml`, or {} when the project has none yet."""
    return read_toml(root / PROJECT_FILE)


WHERE_SCOPE_BELONGS = {
    Scope.MACHINE: "this machine's file, where the machine that runs the build states what it runs",
    Scope.PROJECT: f"the project's {PROJECT_FILE}, where the film that ships carries it",
}
"""Where each scope's keys are written, which is the sentence a refusal of a misplaced key ends on."""


def refuse_off_scope(data: Mapping[str, Any], allowed: Scope, *, file: Path, text: str | None = None) -> None:
    """Refuse the first key in `data` that belongs to a scope other than `allowed`.

    The refusal is symmetric. A per-machine file cannot carry a key about the film, and a project
    cannot carry a key about the machine, because those keys name executables and directories the
    machine trusts. A project that someone else wrote would otherwise choose the program DeckTalk
    launches as the browser, so a machine-scoped key in a project is refused rather than ignored.
    """
    for dotted, _value in _flatten(data):
        key = BY_ID.get(dotted)
        if key is None or key.scope is allowed:
            continue
        where = key.scope.value
        raise InputError(
            f"{file.name}: '{dotted}' is {where}-scoped, so it belongs in {WHERE_SCOPE_BELONGS[key.scope]}.",
            hint=f"Remove it from {file.name} and run `decktalk config set {dotted} <value> --scope {where}`.",
            location=Location(
                where=f"[{key.table}] {key.name}",
                file=file,
                line=locate(text, dotted) if text is not None else None,
            ),
        )


def read_machine_toml(path: Path) -> dict[str, Any]:
    """The per-machine tuning tables, refusing any key that belongs in the project instead.

    The file is restricted by key and not by table, because a limit is a statement about the film
    and the file ships whatever the runner believes. A project-scoped key found here is refused by
    name rather than warned about, since a warning would put a correctly spelled key in a weaker
    class than a typo and a reader of the JSON never sees a log line at all.
    """
    data = read_toml(path)
    if not data:
        return {}
    text = path.read_text(encoding="utf-8")
    tables = {key.table.split(".")[0] for key in KEYS}
    unknown = sorted(set(data) - tables)
    if unknown:
        raise InputError(
            f"{path.name}: {', '.join(unknown)} is not a tuning table, so it does not belong in this file.",
            hint=f"The tables the per-machine file may hold are {', '.join(sorted(tables))}.",
            location=Location(where=path.name, file=path),
        )
    refuse_off_scope(data, Scope.MACHINE, file=path, text=text)
    return data


def key_warnings(doc: Mapping[str, Any], where: str) -> list[str]:
    """One warning per key inside a tuning table that DeckTalk does not read, with a near name when there is one.

    The near name is looked for in the same table first and then across every table, because a key
    is often written under a table it does not live in, and a key is read under its own name alone.
    A table that also holds project content, such as `[mix]`, is left alone, because the document
    parser owns the rest of that table and warning here would call one of its keys unknown.
    """
    out: list[str] = []
    tables = {key.table for key in KEYS}
    for table in sorted(tables - SHARED_TABLES):
        found = _table(doc, table)
        if found is None:
            continue
        known = {key.name for key in KEYS if key.table == table} | {
            inner.removeprefix(f"{table}.").split(".")[0] for inner in tables if inner.startswith(f"{table}.")
        }
        out += unknown_key_warnings(found, known, f"{where}: [{table}]", at=table, anywhere=BY_ID)
    return out


def env_warnings(environ: Mapping[str, str]) -> list[str]:
    """One warning per DECKTALK_ variable DeckTalk does not read, naming the closest one it does.

    A variable DeckTalk does not read has no effect, so the warning is what tells a reader that a
    typed name never took hold.
    """
    known = {key.environment for key in KEYS} | STANDALONE_ENV
    return [
        _unknown_variable(name, known)
        for name in sorted(n for n in environ if n.startswith(ENV_PREFIX) and n not in known)
    ]


def _unknown_variable(name: str, known: set[str]) -> str:
    """The warning for one variable, read as the key it spells so a key that moved tables is found.

    The variable is read as a key of the longest table its name opens with, and the key that one
    names, by the rule the files' own warnings use, is offered by its variable. A name that opens
    with no table is offered the closest variable by spelling.
    """
    spelled = name.removeprefix(ENV_PREFIX).lower()
    tables = sorted({key.table for key in KEYS}, key=len, reverse=True)
    table = next((t for t in tables if spelled.startswith(t.replace(".", "_") + "_")), None)
    if table is None:
        return unknown_key_message(name, known, "environment")
    dotted = f"{table}.{spelled.removeprefix(table.replace('.', '_') + '_')}"
    meant = named_key(dotted, BY_ID)
    if meant is None:
        return unknown_key_message(name, known, "environment")
    return f"environment: ignoring unknown key '{name}' (did you mean '{BY_ID[meant].environment}'?)."


def _table(doc: Mapping[str, Any], dotted: str) -> Mapping[str, Any] | None:
    """One nested table of a parsed document by its dotted name, or null when it is not there."""
    found = stated(doc, dotted)
    return found if isinstance(found, Mapping) else None


def _flatten(doc: Mapping[str, Any], prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar of a parsed document with its dotted name, so a key can be looked up by id."""
    for name, value in doc.items():
        dotted = f"{prefix}{name}"
        if isinstance(value, Mapping):
            yield from _flatten(value, f"{dotted}.")
        else:
            yield dotted, value


def merge_tables(base: Mapping[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    """`over` on top of `base`, table by table, all the way down, which is how tuning tables nest."""
    out: dict[str, Any] = {k: dict(v) if isinstance(v, Mapping) else v for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = merge_tables(out[k], v)
        else:
            out[k] = dict(v) if isinstance(v, Mapping) else v
    return out


def route(overrides: tuple[str, ...]) -> dict[str, str]:
    """Every `--set table.key=value` pair read into keys, refusing anything that is not one.

    The whole tuple reaches both the machine and the project, and the pairs are routed by the scope
    each key publishes, so no caller has to know which layer a key belongs to. A key nobody knows is
    refused before a stage is imported, with the nearest key it could have meant.
    """
    out: dict[str, str] = {}
    for pair in overrides:
        name, sep, value = pair.partition("=")
        key = name.strip()
        if not sep:
            raise InputError(
                f"--set takes table.key=value, and '{pair}' has no '='.",
                hint="Write it as --set verify.cue_offset_max_ms=120.",
            )
        if key.split(".")[0] in DOCUMENT_TABLES:
            raise InputError(
                f"'{key}' is project content rather than a setting, so no override can set it.",
                hint=f"Edit [{key.split('.')[0]}] in {PROJECT_FILE} instead.",
            )
        key_named(key)
        out[key] = value
    return out


def scoped(overrides: Mapping[str, str], scope: Scope) -> dict[str, str]:
    """The overrides that belong to one scope, which is how the machine and the project each take their own."""
    return {key: value for key, value in overrides.items() if BY_ID[key].scope is scope}


def named_file(path: Path, scope: Scope) -> Path:
    """A settings file as DeckTalk names it: the project's own project-relative, the machine's whole.

    The project file sits at the project root, so its name is its project-relative path. The machine
    file sits outside every project and one machine file serves them all, so its absolute path names
    it, which is how every result names a file outside the project. A result of `config set` and a
    refusal of a value the file holds name it this one way.
    """
    return Path(path.name) if scope is Scope.PROJECT else Path(os.path.abspath(path))


def load(
    root: Path | None = None,
    *,
    project: Mapping[str, Any] | None = None,
    machine: Mapping[str, Any] | None = None,
    machine_path: Path | None = None,
    environ: Mapping[str, str],
    overrides: tuple[str, ...] = (),
) -> Loaded:
    """Every key resolved through the five layers, with the record of which layer set each one.

    An override is spelled the way an environment variable is, a string the key's own type reads,
    so one conversion serves both and an override cannot be admitted by a route the environment is
    refused by. It sits above the environment because it is given for one run on purpose.

    The environment is required and never read from the process, because the machine is the one
    reader of the process and a host that built its machine by hand chose what it holds. The machine
    layer is the tables given, or the file at `machine_path`, or nothing when neither is named. A
    refusal of one value names the layer that wrote it: the file, the variable or the `--set`.
    """
    env = dict(environ)
    from_machine = dict(machine) if machine is not None else (read_machine_toml(machine_path) if machine_path else {})
    from_project = dict(project) if project is not None else (read_project_toml(root) if root else {})
    project_file = (root / PROJECT_FILE) if root else Path(PROJECT_FILE)
    project_text = project_file.read_text(encoding="utf-8") if root and project_file.is_file() else None
    refuse_off_scope(from_project, Scope.PROJECT, file=project_file, text=project_text)
    pairs = route(overrides)
    base = merge_tables(from_machine, from_project)
    env_and_overrides = {**env, **{BY_ID[key].environment: value for key, value in pairs.items()}}

    def said(dotted: str, from_env: bool) -> str:
        if from_env:
            return f"--set {dotted}" if dotted in pairs else variable(dotted)
        if stated(from_project, dotted) is not ABSENT:
            return project_file.name
        return named_file(machine_path, Scope.MACHINE).as_posix() if machine_path else "the machine's tables"

    settings = from_mapping(Settings, base=base, environ=env_and_overrides, said=said)
    _require(settings)
    files = {
        Layer.MACHINE: machine_path,
        Layer.PROJECT: (root / PROJECT_FILE) if root else None,
    }
    return Loaded(settings=settings, layers=_layers(settings, from_machine, from_project, env, pairs, files))


def _layers(
    settings: Settings,
    machine: Mapping[str, Any],
    project: Mapping[str, Any],
    environ: Mapping[str, str],
    overrides: Mapping[str, str],
    files: Mapping[Layer, Path | None],
) -> Layers:
    """One row per layer that stated each key, lowest first, which is what the merge itself cannot say.

    The layers are read separately rather than after merging, because a merged mapping has already
    forgotten which file wrote each key, and the file is half of what makes the record useful.
    """
    texts = {layer: current_text(path) if path else "" for layer, path in files.items()}
    rows: dict[str, tuple[LayerValue, ...]] = {}
    for key in KEYS:
        found = [LayerValue(layer=Layer.DEFAULT, value=json_value(key.default))]
        for layer, doc in ((Layer.MACHINE, machine), (Layer.PROJECT, project)):
            said = stated(doc, key.id)
            if said is not ABSENT:
                path = files.get(layer)
                found.append(
                    LayerValue(
                        layer=layer,
                        value=json_value(said),
                        file=path,
                        line=locate(texts.get(layer, ""), key.id) if path else None,
                    )
                )
        if key.environment in environ:
            found.append(LayerValue(layer=Layer.ENVIRONMENT, value=environ[key.environment]))
        if key.id in overrides:
            found.append(LayerValue(layer=Layer.OVERRIDE, value=overrides[key.id]))
        # The winning row carries the value the tree holds rather than the text a layer wrote, so a
        # reader of the record and a reader of the settings never disagree about one number.
        found[-1] = found[-1].model_copy(update={"value": json_value(value_of(settings, key.id))})
        rows[key.id] = tuple(found)
    return Layers(rows=rows)


ABSENT = object()
"""The answer to a lookup for a key a layer never stated, which None cannot be because None is a value."""


def stated(doc: Mapping[str, Any], dotted: str) -> object:
    """What one layer's document says about one key, or `ABSENT` when it says nothing."""
    found: object = doc
    for part in dotted.split("."):
        if not isinstance(found, Mapping) or part not in found:
            return ABSENT
        found = found[part]
    return found


json_value: Callable[[object], JsonValue] = functools.partial(to_jsonable_python, fallback=str)
"""One value as JSON carries it: a tuple as a list, an enum as its value, and anything else unknown as its text."""


HOME_VARIABLES = ("HOME", "USERPROFILE")
"""Where a machine's environment names its home directory, which a folder that starts with `~` is under."""


def machine_folder(loaded: Loaded, key: str, environ: Mapping[str, str]) -> Path | None:
    """The folder a machine key names, with `~` expanded to the machine's home, or None when it names none.

    A machine folder belongs to no project, so a relative one has nothing sensible to be relative to:
    read against each project it would put one folder inside every project, and read against the
    working directory it would move with the shell. Only an absolute path or one under `~` is read, and
    any other is refused naming the layer that set it, which for a machine file is the file itself.
    """
    named = str(value_of(loaded.settings, key))
    if not named:
        return None
    said = loaded.layers.winner(key)
    if said.layer is Layer.ENVIRONMENT:
        source = BY_ID[key].environment
    else:
        source = str(said.file) if said.file is not None else f"the {said.layer.value} layer"
    table, name = key.rsplit(".", 1)
    spelled = f"[{table}] {name} is {named!r} in {source}"
    location = Location(where=f"[{table}] {name}", file=said.file, line=said.line)
    if named == "~" or named.startswith(("~/", "~\\")):
        home = next((environ[variable] for variable in HOME_VARIABLES if environ.get(variable)), None)
        if home is None:
            raise InputError(
                f"{spelled}, which starts with ~, and this machine names no home directory to read it under.",
                hint="Write the folder as an absolute path.",
                location=location,
            )
        return Path(home, named[2:])
    if not Path(named).is_absolute():
        raise InputError(
            f"{spelled}, which is relative, so it would name a different folder for every project and every "
            "working directory.",
            hint=f"Write an absolute path or one that starts with ~, such as ~/{name.removesuffix('_dir')}.",
            location=location,
        )
    return Path(named)


def value_of(settings: Settings, dotted: str) -> object:
    """The value one dotted key holds in a settings tree."""
    return operator.attrgetter(dotted)(settings)


def effective(settings: Settings, name: str) -> object:
    """One input of a relation or a formula at its effective value, whether it is a key or a published number."""
    return value_of(settings, name) if name in BY_ID else NUMBERS_BY_ID[name].at(settings)


def _require(settings: Settings) -> None:
    """Every cross-table relation a key declares, enforced once the whole tree is built.

    A relation is a comparison an editor cannot check, because one side of it lives in another
    table or is a published number rather than a key, so the loader is the only place it can hold.
    """
    for key in KEYS:
        if key.requires is None:
            continue
        left, op, right = key.requires.split()
        if not COMPARISONS[op](_side(settings, left), _side(settings, right)):
            raise InputError(
                f"{key.id} must satisfy {key.requires}, and it does not.",
                hint=key.hazard,
            )


def _side(settings: Settings, token: str) -> float:
    """One side of a declared relation, which is a key, a published number or a literal."""
    if token in BY_ID or token in NUMBERS_BY_ID:
        return float(cast("float", effective(settings, token)))
    return float(token)


COMPARISONS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
"""The four comparisons a declared relation may use."""
