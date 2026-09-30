"""One knob explained: what set it, what it feeds, and what a candidate value would do to this project.

Everything about a key that can be looked up is in the published schema, so this module is only
what has to be computed. Three things are: which of the five layers actually set the value here,
what the derived numbers this key feeds work out to at the values in force, and which cues in this
project a candidate value would clamp. The last one is why the explainer sits in the SDK layer
beside the machine rather than inside the settings, because naming a cue means reading the project's
own resolved times, through the artifact that holds them and the build directory the project names.
"""

from __future__ import annotations

import itertools
import logging
from pathlib import Path
from typing import Any

from .errors import DeckTalkError, InputError
from .findings import DOCS
from .inputs import Inputs
from .machine import Machine
from .results import ConfigExplainResult, Layer, NumberView, Scope, SectionCues
from .settings import (
    NUMBERS,
    NUMBERS_BY_ID,
    Loaded,
    Settings,
    effective,
    json_value,
    key_named,
    load,
    nested,
    value_of,
)
from .tomlmap import Key

log = logging.getLogger(__name__)

Cue = tuple[float, str]
"""One resolved cue as the explainer reads it, which is its second and its wire id, in that order so it sorts."""

TYPE_NAMES: dict[object, str] = {bool: "boolean", int: "integer", float: "number", str: "string"}
"""A scalar key's type as the schema names it."""

ARRAY_TYPE = "array of numbers"
"""The type of every key that is not a scalar, which is a TOML array of numbers."""


def explain(
    key: str, *, project: Path | None = None, value: str | None = None, machine: Machine | None = None
) -> ConfigExplainResult:
    """One knob, its layers, the numbers it feeds and what a candidate would clamp in this project.

    `project` is a project directory. Without one the answer is about the defaults and the machine
    alone, which is what an agent reading the instruction set before it has a project needs.
    `machine` is the machine whose file and environment are the layers under and over the project,
    which is this process's own when none is given, as it is for `init`.
    `value` is a candidate spelled the way a command line spells it, held to the same safe range as
    a value that is written, because an explanation of a value the loader would refuse is a lie
    with arithmetic in it.
    """
    known = key_named(key)
    on = machine or Machine.from_environment()
    opened = _opened(project, on) if project else None
    here = (
        Loaded(settings=opened.settings, layers=opened.layers)
        if opened
        else load(project, machine=on.tables, machine_path=on.config_path, environ=on.environ)
    )
    candidate = _candidate(known, here, value)
    cues = _cues(opened) if opened else ()
    layers = here.layers.of(known.id)
    return ConfigExplainResult(
        ok=True,
        key=known.id,
        sentence=known.description,
        type=TYPE_NAMES.get(known.annotation, ARRAY_TYPE),
        unit=known.unit,
        default=json_value(known.default),
        value=json_value(value_of(here.settings, known.id)),
        range=known.range,
        typed_range=known.typed.sentence if known.typed else None,
        scope=known.scope,
        nature=known.nature,
        source=known.source,
        evidence=known.evidence,
        hazard=known.hazard,
        requires=known.requires,
        see_also=known.see_also,
        decides=known.decides,
        environment=known.environment,
        layers=layers,
        layer=here.layers.winner(known.id).layer,
        numbers=_numbers(known, here.settings, candidate),
        candidate=None if candidate is None else json_value(value_of(candidate, known.id)),
        clamped=_clamped(known, candidate or here.settings, cues),
        measured=bool(cues),
        docs=f"{DOCS}/configuration#{known.table.replace('.', '-')}",
    )


def _opened(project: Path, machine: Machine) -> Inputs | None:
    """The project whole, or None while its document does not parse yet.

    A knob is explainable in a project whose sections are still being written, so a document the
    loader refuses costs the answer its cues and nothing else. A refused setting is not swallowed,
    because the settings-only load that follows meets the same refusal and raises it.
    """
    try:
        return Inputs.load(project, environ=machine.environ, machine=machine.tables)
    except InputError as refused:
        log.info("The project did not load (%s), so the key is explained without the project's own layer.", refused)
        return None


def _candidate(key: Key, here: Loaded, value: str | None) -> Settings | None:
    """The whole settings tree as it would be with this one key at the candidate, or null.

    The tree is rebuilt rather than patched, so the candidate meets the same range and the same
    cross-table relations a written value would, and every number computed from it is computed the
    way a run would compute it.
    """
    if value is None:
        return None
    return load(project=_layer(here, key.scope), machine={}, environ={key.environment: value}).settings


def _layer(here: Loaded, scope: Scope) -> dict[str, Any]:
    """The project's own stated keys, so a candidate is explained against the file rather than the defaults."""
    if scope is not Scope.PROJECT:
        return {}
    rows = here.layers.rows.items()
    return nested({key: said[-1].value for key, row in rows if (said := [r for r in row if r.layer is Layer.PROJECT])})


def _numbers(key: Key, here: Settings, candidate: Settings | None) -> tuple[NumberView, ...]:
    """Every published number whose formula reads this key, worked out here and at the candidate."""
    return tuple(
        NumberView(
            id=number.id,
            formula=number.formula,
            reads={name: json_value(effective(here, name)) for name in number.reads},
            value=json_value(number.at(here)),
            candidate=None if candidate is None else json_value(number.at(candidate)),
            unit=number.unit,
            sentence=number.sentence,
        )
        for number in NUMBERS
        if key.id in number.reads
    )


def _clamped(key: Key, settings: Settings, cues: tuple[tuple[str, tuple[Cue, ...]], ...]) -> tuple[str, ...]:
    """The cues whose reference frame this value pulls back into the cue before them.

    The reference frame is read one lead before a cue, so two cues closer together than that lead
    make the earlier one the reference for the later one, and the check then compares a change
    against a frame the same change had already reached. Only the keys the lead is computed from
    can do that, which is why every other key names no cue rather than guessing at one.
    """
    feeds = NUMBERS_BY_ID.get("verify.reference_lead_seconds")
    if feeds is None or key.id not in feeds.reads:
        return ()
    lead = feeds.at(settings)
    out: list[str] = []
    for _section, rows in cues:
        for (earlier, _), (later, cue) in itertools.pairwise(rows):
            if later - earlier < lead:
                out.append(cue)
    return tuple(out)


def _cues(project: Inputs) -> tuple[tuple[str, tuple[Cue, ...]], ...]:
    """Every resolved cue of this project in section order, or nothing when the stage has not run.

    The times are read through `CueTimes`, the model `cue` writes them with, from the build directory
    the project names, so a moved build directory is read where it is. A row whose second is null was
    never resolved against a word, so it is left out rather than read as a cue at zero. A file that is
    there and cannot be read explains nothing about cues, because a knob is explainable without them.
    """
    try:
        resolved = project.cue_times()
    except DeckTalkError as refused:
        log.info("The cue times could not be read (%s), so no cue is weighed against the key.", refused)
        return ()
    if resolved is None:
        return ()
    return tuple(_block(block) for block in resolved.sections)


def _block(section: SectionCues) -> tuple[str, tuple[Cue, ...]]:
    """One section of the artifact as the explainer reads it, which is its key and its resolved cues."""
    return section.key, tuple(sorted((row.seconds, row.cue) for row in section.cues if row.seconds is not None))


__all__ = ["explain"]
