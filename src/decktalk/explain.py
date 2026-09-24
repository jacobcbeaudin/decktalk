"""One knob explained: what set it, what it feeds, and what a candidate value would do to this project.

Everything about a key that can be looked up is in the published schema, so this module is only
what has to be computed. Three things are: which of the five layers actually set the value here,
what the derived numbers this key feeds work out to at the values in force, and which cues in this
project a candidate value would clamp. The last one is why the explainer sits above the settings
layer rather than inside it, because naming a cue means reading the project's own resolved times.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field, JsonValue

from .errors import InputError
from .findings import DOCS, MODEL, Code
from .results import Layer, Scope
from .settings import (
    BY_ID,
    NUMBERS,
    LayerValue,
    Loaded,
    Settings,
    load,
    value_of,
)
from .tomlmap import Key, Nature, Source, did_you_mean

CUE_TIMES = Path("build") / "cue-times.json"
"""Where `cue` writes the resolved times the explainer reads, project-relative."""


class NumberView(BaseModel):
    """One derived number this key feeds, with its inputs at the values in force."""

    model_config = MODEL

    id: str = Field(description="The number's name, which is the key it replaced or the constant it is.")
    formula: str = Field(description="The expression this number is, which is what it is published as.")
    reads: dict[str, JsonValue] = Field(description="Every key and constant the formula reads, at its value here.")
    value: JsonValue = Field(description="What the formula works out to at the values in force.")
    candidate: JsonValue | None = Field(None, description="What it would work out to at the candidate, or null.")
    unit: str | None = Field(None, description="The number's true unit, or null when it has none.")
    sentence: str = Field(description="Why this number is not a knob, which opens with its nature.")


class Explanation(BaseModel):
    """One knob read whole, with the layers under it and the arithmetic above it.

    It is the explainer's own result rather than a command's, because the same three computations
    serve `config explain`, a fix an agent applies and a renderer that shows a knob beside the
    finding it moves.
    """

    model_config = MODEL

    key: str = Field(description="The key's dotted name.")
    description: str = Field(description="What this key changes, in one sentence.")
    type: str = Field(description="The key's type, as the schema names it.")
    unit: str | None = Field(None, description="The true unit of the value, or null when it has none.")
    default: JsonValue = Field(description="The value that would be in force with no override at all.")
    value: JsonValue = Field(description="The value in force for this project on this machine.")
    range: str = Field(description="The safe range in words, which is the range the loader enforces.")
    typed_range: str | None = Field(None, description="The wider range the type admits, which is not enforced.")
    scope: Scope = Field(description="Which file this key belongs in.")
    nature: Nature = Field(description="Why this number is a key at all, taste or apparatus.")
    source: Source = Field(description="Where the value is expected to come from.")
    evidence: str | None = Field(None, description="What produces the value, for a stated or measured key.")
    hazard: str | None = Field(None, description="What a value at the edge of the range risks, or null.")
    requires: str | None = Field(None, description="A relation to another key or number, enforced at load.")
    see_also: tuple[str, ...] = Field((), description="Keys and published numbers that move with this one.")
    decides: tuple[Code, ...] = Field((), description="The findings whose verdict this key moves.")
    environment: str = Field(description="The environment variable that sets this key.")
    layers: tuple[LayerValue, ...] = Field(description="Every layer that stated this key, lowest first.")
    winner: Layer = Field(description="The layer the value in force comes from.")
    numbers: tuple[NumberView, ...] = Field((), description="The derived numbers this key feeds.")
    candidate: JsonValue | None = Field(None, description="The value asked about, or null when none was.")
    clamped: tuple[str, ...] = Field((), description="Cues in this project the candidate would clamp.")
    measured: bool = Field(description="True when this project's resolved cue times were there to read.")
    docs: str = Field(description="The docs page for this key.")


@dataclass(frozen=True)
class _Cue:
    """One resolved cue as the explainer reads it, which is its wire id and its second."""

    id: str
    at: float


def explain(key: str, *, project: Path | None = None, value: str | None = None) -> Explanation:
    """One knob, its layers, the numbers it feeds and what a candidate would clamp in this project.

    `project` is a project directory. Without one the answer is about the defaults and the machine
    alone, which is what an agent reading the instruction set before it has a project needs.
    `value` is a candidate spelled the way a command line spells it, held to the same safe range as
    a value that is written, because an explanation of a value the loader would refuse is a lie
    with arithmetic in it.
    """
    known = BY_ID.get(key)
    if known is None:
        raise InputError(
            f"'{key}' is not a settings key.{did_you_mean(key, BY_ID)}",
            hint="Run `decktalk schema settings` for every key DeckTalk reads.",
        )
    here = load(project)
    candidate = _candidate(known, here, value)
    cues = _cues(project) if project else ()
    return Explanation(
        key=known.id,
        description=known.description,
        type=_type_name(known),
        unit=known.unit,
        default=_json(known.default),
        value=_json(value_of(here.settings, known.id)),
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
        layers=here.layers.of(known.id),
        winner=here.layers.winner(known.id).layer,
        numbers=_numbers(known, here.settings, candidate),
        candidate=None if candidate is None else _json(value_of(candidate, known.id)),
        clamped=_clamped(known, candidate or here.settings, cues),
        measured=bool(cues),
        docs=f"{DOCS}/settings/{known.id}",
    )


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
    out: dict[str, Any] = {}
    for dotted, rows in here.layers.rows.items():
        stated = [row for row in rows if row.layer is Layer.PROJECT] if scope is Scope.PROJECT else []
        if not stated:
            continue
        table = out
        parts = dotted.split(".")
        for part in parts[:-1]:
            table = table.setdefault(part, {})
        table[parts[-1]] = stated[-1].value
    return out


def _numbers(key: Key, here: Settings, candidate: Settings | None) -> tuple[NumberView, ...]:
    """Every published number whose formula reads this key, worked out here and at the candidate."""
    return tuple(
        NumberView(
            id=number.id,
            formula=number.formula,
            reads={name: _json(_read(here, name)) for name in number.reads},
            value=_json(number.at(here)),
            candidate=None if candidate is None else _json(number.at(candidate)),
            unit=number.unit,
            sentence=number.sentence,
        )
        for number in NUMBERS
        if key.id in number.reads
    )


def _read(settings: Settings, name: str) -> object:
    """One input of a formula at its effective value, whether it is a key or a published number."""
    if name in BY_ID:
        return value_of(settings, name)
    return next(number.at(settings) for number in NUMBERS if number.id == name)


def _clamped(key: Key, settings: Settings, cues: tuple[tuple[str, tuple[_Cue, ...]], ...]) -> tuple[str, ...]:
    """The cues whose reference frame this value pulls back into the cue before them.

    The reference frame is read one lead before a cue, so two cues closer together than that lead
    make the earlier one the reference for the later one, and the check then compares a change
    against a frame the same change had already reached. Only the keys the lead is computed from
    can do that, which is why every other key names no cue rather than guessing at one.
    """
    feeds = next((number for number in NUMBERS if number.id == "verify.reference_lead_seconds"), None)
    if feeds is None or key.id not in feeds.reads:
        return ()
    lead = float(cast("float", feeds.at(settings)))
    out: list[str] = []
    for _section, rows in cues:
        for earlier, later in zip(rows, rows[1:], strict=False):
            if later.at - earlier.at < lead:
                out.append(later.id)
    return tuple(out)


def _cues(project: Path) -> tuple[tuple[str, tuple[_Cue, ...]], ...]:
    """Every resolved cue of this project in section order, or nothing when the stage has not run.

    The file is read leniently, because a project that has never been cued is the common case and a
    knob is explainable without one.
    """
    path = project / CUE_TIMES
    if not path.exists():
        return ()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        sections = document["sections"]
    except (OSError, ValueError, KeyError, TypeError):
        return ()
    out: list[tuple[str, tuple[_Cue, ...]]] = []
    for name in sorted(sections):
        rows = tuple(_Cue(id=str(row["cue"]), at=float(row["at"])) for row in sections[name])
        out.append((name, tuple(sorted(rows, key=lambda row: row.at))))
    return tuple(out)


def _type_name(key: Key) -> str:
    """The key's type as the schema names it, which is one word for a scalar and a phrase for an array."""
    annotation = key.annotation
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is str:
        return "string"
    return "array of numbers"


def _json(value: object) -> JsonValue:
    """One value as JSON carries it, which turns the tuple a TOML array becomes into a list."""
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    return cast("JsonValue", value)


__all__ = ["CUE_TIMES", "Explanation", "NumberView", "explain"]
