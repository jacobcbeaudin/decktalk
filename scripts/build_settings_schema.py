# /// script
# requires-python = ">=3.12"
# ///
"""Generate the JSON Schema for decktalk.toml, and the per-machine filter of it.

    uv run scripts/build_settings_schema.py            # write both schemas and their published copies
    uv run scripts/build_settings_schema.py --check    # exit 1 if either committed file would change

One schema describes the whole project file, its document tables and its tuning tables together,
because an editor binds one schema to one file and `#:schema` is a single directive. Every property
carries the key's whole published record, so an agent that has the schema and nothing else has the
safe range, the unit, the hazard, the scope and the findings the key decides. The numbers that are
deliberately not knobs sit in `x-numbers` outside `properties`, so a reader who goes looking for one
finds the formula rather than nothing.

The per-machine schema is a filter of the same document by `x-scope`, so the two can never disagree
about which keys a machine may hold.

The `v1` in the path is the version of the file shape these two schemas describe, which is the shape
of `decktalk.toml` and of the per-machine settings file beside it. A change that an existing project
file would not survive is a new directory rather than a new revision of this one.

The `#:schema` line in every project file names the committed file by its raw GitHub URL on `main`,
so the one copy the repository holds is the one an editor fetches, and there is no second copy to
fall behind it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from decktalk.results import Scope  # noqa: E402  (after sys.path, so a checkout needs no install)
from decktalk.settings import (  # noqa: E402
    DOCUMENT_TABLES,
    KEYS,
    NUMBERS,
    SHARED_TABLES,
    Settings,
)
from decktalk.tomlmap import Key  # noqa: E402

BASE = "https://raw.githubusercontent.com/jacobcbeaudin/decktalk/main/schemas/v1"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
PROJECT_NAME = "decktalk.json"
MACHINE_NAME = "machine.json"
SCHEMAS = ROOT / "schemas" / "v1"

TITLE = "DeckTalk project file"
MACHINE_TITLE = "DeckTalk per-machine settings file"

DESCRIPTION = (
    "Every table of a DeckTalk project file. A document table says what the film is and a tuning "
    "table says how it is made, and each key publishes the range that is safe to turn it through."
)
MACHINE_DESCRIPTION = (
    "The keys a per-machine settings file may hold, which are the ones that describe this machine "
    "rather than the film. A key about the film belongs in the project file, because the file that "
    "ships carries whatever the runner believes."
)

TYPES = {bool: "boolean", int: "integer", float: "number", str: "string"}
"""The JSON Schema type for each scalar a key can be declared as."""

DOCUMENT_SENTENCES = {
    "project": "The film itself: its name, its deck and the files it is built from.",
    "section": "One section of the script, in the order the film plays them.",
    "transition": "How one section is joined to the next.",
    "soundscape": "The music, the ambience and the sound effects a run generates.",
}
"""One sentence for each table that is project content, which the document parser owns the keys of."""


def type_of(key: Key) -> dict[str, Any]:
    """One key's JSON Schema type, an array being described by the type of what it holds."""
    if key.annotation in TYPES:
        return {"type": TYPES[key.annotation]}
    return {"type": "array", "items": {"type": "number"}}


def property_of(key: Key) -> dict[str, Any]:
    """One key as the schema publishes it, which is its type, its safe range and its whole record."""
    out: dict[str, Any] = {"description": key.description, **type_of(key), "default": json_value(key.default)}
    if key.bounds is not None:
        bounds = key.bounds.json_schema()
        if out["type"] == "array":
            items = out["items"] | bounds.pop("items", {})
            out["items"] = items
        out |= bounds
    out["x-range"] = key.range
    if key.typed is not None:
        out["x-typed-range"] = key.typed.sentence
    if key.unit is not None:
        out["x-unit"] = key.unit
    out["x-scope"] = key.scope.value
    out["x-nature"] = key.nature.value
    out["x-source"] = key.source.value
    if key.evidence is not None:
        out["x-evidence"] = key.evidence
    if key.hazard is not None:
        out["x-hazard"] = key.hazard
    if key.requires is not None:
        out["x-requires"] = key.requires
    if key.see_also:
        out["x-see-also"] = list(key.see_also)
    if key.decides:
        out["x-decides"] = [code.name for code in key.decides]
    out["x-environment"] = key.environment
    return out


def json_value(value: object) -> object:
    """One default as JSON carries it, which turns the tuple a TOML array becomes into a list."""
    return [json_value(item) for item in value] if isinstance(value, tuple) else value


def table_of(name: str, keys: list[Key]) -> dict[str, Any]:
    """One tuning table as the schema publishes it, closed unless the document owns keys in it too."""
    properties: dict[str, Any] = {key.name: property_of(key) for key in keys if key.table == name}
    for inner in sorted({key.table for key in keys if key.table.startswith(f"{name}.")}):
        properties[inner.rsplit(".", 1)[-1]] = table_of(inner, [key for key in keys if key.table == inner])
    shared = name in SHARED_TABLES
    return {
        "type": "object",
        "description": sentence_of(name),
        "x-kind": "mixed" if shared else "tuning",
        "properties": properties,
        "additionalProperties": shared,
    }


def sentence_of(name: str) -> str:
    """The sentence a table publishes, which is the docstring of the class that declares it."""
    holder: Any = Settings
    for part in name.split("."):
        declared = next(f.type for f in holder.__dataclass_fields__.values() if f.name == part)
        holder = getattr(sys.modules["decktalk.settings"], declared) if isinstance(declared, str) else declared
    return " ".join((holder.__doc__ or "").split())


def document_table(name: str) -> dict[str, Any]:
    """One table that is project content, declared so the top level can be closed without owning its keys.

    The document parser owns what is inside, so the schema names the table and leaves it open. The
    closure that matters is at the top level, where an unknown table passes silently today.
    """
    body: dict[str, Any] = {
        "type": "object",
        "description": DOCUMENT_SENTENCES[name],
        "x-kind": "document",
        "additionalProperties": True,
    }
    if name != "section":
        return body
    return {"type": "array", "description": DOCUMENT_SENTENCES[name], "x-kind": "document", "items": body}


def numbers() -> list[dict[str, Any]]:
    """Every number that is deliberately not a knob, with its formula and its value at the defaults."""
    at_defaults = Settings()
    return [
        {
            "id": number.id,
            "kind": number.kind,
            "formula": number.formula,
            "reads": list(number.reads),
            "value": json_value(number.at(at_defaults)),
            "unit": number.unit,
            "nature": number.nature.value,
            "decides": [code.name for code in number.decides],
            "description": number.sentence,
        }
        for number in NUMBERS
    ]


def document(*, machine: bool) -> dict[str, Any]:
    """The whole schema, or the filter of it a per-machine file is judged against."""
    name = MACHINE_NAME if machine else PROJECT_NAME
    keys = [key for key in KEYS if not machine or key.scope is Scope.MACHINE]
    properties: dict[str, Any] = {}
    for table in dict.fromkeys(key.table.split(".")[0] for key in keys):
        properties[table] = table_of(table, [key for key in keys if key.table.split(".")[0] == table])
    if not machine:
        for table in DOCUMENT_TABLES:
            properties[table] = document_table(table)
    out: dict[str, Any] = {
        "$schema": DRAFT,
        "$id": f"{BASE}/{name}",
        "title": MACHINE_TITLE if machine else TITLE,
        "description": MACHINE_DESCRIPTION if machine else DESCRIPTION,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if not machine:
        out["x-numbers"] = numbers()
    return out


def render(*, machine: bool) -> str:
    """The schema as the committed file holds it, which is two-space JSON with a trailing newline."""
    return json.dumps(document(machine=machine), indent=2) + "\n"


def documents() -> dict[Path, str]:
    """Every file this generator owns, which is the project schema and the machine schema."""
    return {SCHEMAS / name: render(machine=machine) for name, machine in ((PROJECT_NAME, False), (MACHINE_NAME, True))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="exit 1 if either committed file would change")
    args = ap.parse_args()
    stale = False
    for target, text in documents().items():
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                print(f"stale: {target.relative_to(ROOT)}. Run `uv run scripts/build_settings_schema.py`.")
                stale = True
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target.relative_to(ROOT)}")
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
