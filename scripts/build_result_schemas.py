"""Generate schemas/v1/results/*.json, one JSON Schema per command result, from the models.

    uv run scripts/build_result_schemas.py --write    # write every schema
    uv run scripts/build_result_schemas.py --check    # exit 1 if a committed schema would change

Every field's sentence, type, range and default come from the `Field` that declares it, so the
published contract cannot drift from the code. Edit `src/decktalk/results.py`, then run this.

The `v1` in the path is the version of the schemas layout rather than of a result, which carries its
own `schema` number inside every payload. A result whose shape changes says so in that number, and
the directory moves only when the whole published layout does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import generated  # noqa: E402
from decktalk.catalog import result_schemas  # noqa: E402  (after sys.path, so a checkout needs no install)

TARGET = ROOT / "schemas" / "v1" / "results"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def document(name: str, schema: dict[str, Any]) -> str:
    """One schema file as it is committed, which is the model's own schema under a declared dialect."""
    return json.dumps({"$schema": DIALECT, "$id": f"{name}.json", **schema}, indent=2) + "\n"


def documents() -> dict[Path, str]:
    """Every schema file this generator owns, by the path it is written to."""
    return {TARGET / f"{name}.json": document(name, schema) for name, schema in result_schemas().items()}


if __name__ == "__main__":
    sys.exit(generated.run(documents, owned=[TARGET / "*.json"]))
