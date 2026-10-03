"""Generate schemas/v1/results/*.json, one JSON Schema per command result, from the models.

    uv run scripts/build_result_schemas.py --write    # write every schema
    uv run scripts/build_result_schemas.py --check    # exit 1 if a committed schema would change

Every field's sentence, type, range and default come from the `Field` that declares it, so the
published contract cannot drift from the code. Edit `src/decktalk/results.py`, then run this.

The `v1` in the path is the `schema` number every result carries, read from `SCHEMA`, so a reader
that sees `schema: 1` in a payload opens `schemas/v1` and the two can never disagree.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import generated
from decktalk.results import RESULTS, SCHEMA

ROOT = Path(__file__).resolve().parent.parent

TARGET = ROOT / "schemas" / f"v{SCHEMA}" / "results"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def document(name: str, schema: dict[str, Any]) -> str:
    """One schema file as it is committed, which is the model's own schema under a declared dialect."""
    return json.dumps({"$schema": DIALECT, "$id": f"{name}.json", **schema}, indent=2) + "\n"


def documents() -> dict[Path, str]:
    """Every schema file this generator owns, by the path it is written to."""
    return {
        TARGET / f"{name}.json": document(name, schema)
        for name, schema in ((name, model.model_json_schema()) for name, model in RESULTS.items())
    }


if __name__ == "__main__":
    sys.exit(generated.run(documents, owned=[TARGET / "*.json"]))
