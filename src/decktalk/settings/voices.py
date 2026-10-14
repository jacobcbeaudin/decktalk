"""Each voice a project declares, resolved over its provider's table, with the refusals that keep a voice whole.

A voice is one `[voices.NAME]` table of `decktalk.toml`. Its own three keys, `provider`, `id` and
`speed`, are read like any key, through every layer. Any other key it states is a chosen key of its
provider's table, read with that key's own type and range and laid over the table for this voice
alone, so a specific value always beats the general one whatever layer set either. A key that belongs
to another provider, the rate every voice of a provider is billed at, or a key only the machine may
set is refused when the project loads rather than ignored, because a voice that silently reads
another vendor's setting is a take bought in a voice nobody asked for.

The default voice, which `[narration] voice` names, needs no table of its own only while the project
declares no voice at all. Once one `[voices.NAME]` table is written, every voice, the default
included, is a table, and a variable or a `--set` for a voice the project does not declare is refused,
because a voice renamed in the file would otherwise leave its variable buying in the wrong voice.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, get_type_hints

from decktalk.errors import InputError
from decktalk.findings import Location
from decktalk.locate import locate
from decktalk.results import Scope, Source
from decktalk.settings import (
    OWN_KEYS,
    PROJECT_FILE,
    PROVIDER_TABLES,
    VOICE_NAME,
    VOICES_TABLE,
    DtspConfig,
    ElevenLabsConfig,
    Settings,
    Voice,
    VoiceConfig,
    provider_tables,
)
from decktalk.tomlmap import ENV_PREFIX, variable
from decktalk.tomlmap.read import from_mapping, read_value
from decktalk.tomlmap.suggest import did_you_mean

VOICE_VARIABLE = re.compile(rf"^{ENV_PREFIX}{VOICES_TABLE.upper()}_([A-Z][A-Z0-9]*)_(.+)$")
"""A variable that sets a key of one voice: the voice's name, then the key, after `DECKTALK_VOICES_`.

A voice name holds no underscore, so the first underscore after the name is where the key begins.
"""

DEFAULT_KEY = "narration.voice"
"""The key that names the default voice."""


def resolve(
    settings: Settings,
    *,
    project: Mapping[str, Any],
    environ: Mapping[str, str],
    overrides: Mapping[str, str],
    said: Callable[[str, bool], str],
    opened: bool,
    file: Path,
    text: str | None,
) -> Mapping[str, Voice]:
    """Every voice the project declares, the default first and the rest by name, each resolved whole.

    `project` is the project's own tables and `environ` the environment, with `overrides` the run's
    `--set` pairs by dotted key. `opened` says whether a project is open: with none, as for `doctor`,
    the machine declares no voice, so a variable naming one is never refused here. `file` and `text`
    are the project file and its text, which a refusal names the line of.
    """
    declared = _declared(project)
    default = settings.narration.voice
    names = _names(declared, default, project, environ, overrides, said)
    if opened:
        _refuse_undeclared(names, environ, overrides)
    spelled = {**environ, **{variable(key): value for key, value in overrides.items()}}
    return {name: _voice(settings, name, declared.get(name), spelled, said, file=file, text=text) for name in names}


def _declared(project: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Every `[voices.NAME]` table the project writes, refusing a name or an entry that is not a voice."""
    found = project.get(VOICES_TABLE, {})
    if not isinstance(found, Mapping):
        raise _not_a_table(f"[{VOICES_TABLE}]")
    declared: dict[str, Mapping[str, Any]] = {}
    for name, table in found.items():
        if not re.fullmatch(VOICE_NAME, name):
            raise InputError(
                f"{PROJECT_FILE}: [voices.{name}] is not a voice name, because a voice name is a lower-case letter "
                "followed by lower-case letters and digits.",
                hint=(
                    "Rename the table, such as [voices.guest], and every [voice NAME] line in the script that names it."
                ),
            )
        if not isinstance(table, Mapping):
            raise _not_a_table(f"[{VOICES_TABLE}] {name}")
        declared[name] = table
    return declared


def _not_a_table(spelled: str) -> InputError:
    """The refusal of a value written where a voice's table belongs, which states the shape the table takes."""
    return InputError(
        f"{PROJECT_FILE}: {spelled} is not a table, and [voices] holds one table per voice, such as [voices.narrator].",
        hint="Write each voice as its own table, [voices.NAME], holding its provider, id and speed.",
    )


def _names(
    declared: Mapping[str, Any],
    default: str,
    project: Mapping[str, Any],
    environ: Mapping[str, str],
    overrides: Mapping[str, str],
    said: Callable[[str, bool], str],
) -> tuple[str, ...]:
    """The names of the project's voices, the default first, refusing a default the project does not declare."""
    if not declared:
        return (default,)
    if default not in declared:
        listed = ", ".join(sorted(declared))
        raise InputError(
            f"[narration] voice is {default!r}{_where_default(project, environ, overrides, said)}, and "
            f"{PROJECT_FILE} declares no [voices.{default}] table. The voices are {listed}."
            f"{did_you_mean(default, declared)}",
            hint="Name one of the voices in [narration] voice, or write the table it names.",
        )
    return (default, *sorted(set(declared) - {default}))


def _where_default(
    project: Mapping[str, Any],
    environ: Mapping[str, str],
    overrides: Mapping[str, str],
    said: Callable[[str, bool], str],
) -> str:
    """Where the default voice was named, as the clause the refusal of an undeclared default carries."""
    from_env = DEFAULT_KEY in overrides or variable(DEFAULT_KEY) in environ
    narration = project.get("narration", {})
    if from_env or (isinstance(narration, Mapping) and "voice" in narration):
        return f" in {said(DEFAULT_KEY, from_env)}"
    return ", its default"


def _refuse_undeclared(names: tuple[str, ...], environ: Mapping[str, str], overrides: Mapping[str, str]) -> None:
    """Refuse a variable or a `--set` that sets a key of a voice the project does not declare."""
    named = [(found[1].lower(), name) for name in sorted(environ) if (found := VOICE_VARIABLE.match(name))]
    named += [(key.split(".")[1], f"--set {key}") for key in overrides if key.split(".")[0] == VOICES_TABLE]
    for voice, source in named:
        if voice not in names:
            raise InputError(
                f"{source} sets a key of the voice {voice!r}, and {PROJECT_FILE} declares no [voices.{voice}] table. "
                f"The voices are {', '.join(names)}.{did_you_mean(voice, names)}",
                hint="Unset it, or name a voice the project declares.",
            )


def _voice(
    settings: Settings,
    name: str,
    table: Mapping[str, Any] | None,
    spelled: Mapping[str, str],
    said: Callable[[str, bool], str],
    *,
    file: Path,
    text: str | None,
) -> Voice:
    """One voice: its own keys read through every layer, and every key of its provider it states laid over the table."""
    own = from_mapping(VoiceConfig, base=table, path=(VOICES_TABLE, name), environ=spelled, said=said)
    stated = _stated(name, table or {}, spelled)
    provider = own.provider
    tables = provider_tables()
    over: dict[str, Any] = {}
    for key, (raw, from_env) in stated.items():
        where = f"{VOICES_TABLE}.{name}.{key}"
        located = Location(where=f"[voices.{name}] {key}", file=file, line=locate(text, where) if text else None)
        if provider not in tables:
            if any(key in _field_names(cls) for cls in tables.values()):
                raise _host(name, key, provider, located)
            continue
        cls = tables[provider]
        spec = next((f for f in fields(cls) if f.name == key), None)
        if spec is None:
            other = next((table for table, held in tables.items() if key in _field_names(held)), None)
            if other is not None:
                raise InputError(
                    f"{PROJECT_FILE}: [voices.{name}] {key} is a key of [{other}], and the voice {name} is read by "
                    f"{provider}, which has no such key.",
                    hint=f"Take {key} out of [voices.{name}], or set its provider to {other}.",
                    location=located,
                )
            continue
        if spec.metadata["source"] is not Source.CHOSEN:
            raise InputError(
                f"{PROJECT_FILE}: [voices.{name}] {key} is what [{provider}] charges, which every voice of {provider} "
                f"is billed at, so it belongs in [{provider}] alone.",
                hint=f"Move {key} into [{provider}].",
                location=located,
            )
        if spec.metadata["scope"] is not Scope.PROJECT:
            raise InputError(
                f"{PROJECT_FILE}: [voices.{name}] {key} is machine-scoped, so it belongs in this machine's file under "
                f"[{provider}].",
                hint=(
                    f"Remove it from {PROJECT_FILE} and run decktalk config set {provider}.{key} <value> "
                    "--scope machine."
                ),
                location=located,
            )
        over[key] = read_value(
            get_type_hints(cls)[key],
            raw,
            where=where,
            bounds=spec.metadata["bounds"],
            hazard=spec.metadata["hazard"],
            from_env=from_env,
            said=said(where, from_env),
        )
    if provider not in PROVIDER_TABLES:
        return Voice(name=name, own=own, table=None)
    return Voice(name=name, own=own, table=replace(getattr(settings, provider), **over), stated=tuple(sorted(over)))


def _stated(name: str, table: Mapping[str, Any], spelled: Mapping[str, str]) -> dict[str, tuple[Any, bool]]:
    """Every key beyond its own three that one voice states, with the value in force and whether a variable said it.

    A variable, which a `--set` is spelled as here, beats the file, as it does for every key.
    """
    found: dict[str, tuple[Any, bool]] = {key: (value, False) for key, value in table.items() if key not in OWN_KEYS}
    prefix = f"{ENV_PREFIX}{VOICES_TABLE.upper()}_{name.upper()}_"
    for variable_name, value in spelled.items():
        if variable_name.startswith(prefix):
            key = variable_name.removeprefix(prefix).lower()
            if key not in OWN_KEYS:
                found[key] = (value, True)
    return found


def _field_names(cls: type[ElevenLabsConfig | DtspConfig]) -> set[str]:
    """The keys one provider table declares, whatever their scope or source."""
    return {f.name for f in fields(cls)}


def _host(name: str, key: str, provider: str, located: Location) -> InputError:
    """The refusal of a provider table's key on a voice whose provider a host registered, which owns no table."""
    return InputError(
        f"{PROJECT_FILE}: [voices.{name}] {key} is a key of a provider table, and the voice {name} is read by "
        f"{provider!r}, a provider a host registered, which owns no table.",
        hint=f"Take {key} out of [voices.{name}].",
        location=located,
    )


__all__ = ["resolve"]
