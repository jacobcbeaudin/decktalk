"""Generate schemas/v1/events.json, the JSON Schema of one line of the events file, from the models.

    uv run scripts/build_event_schema.py --write    # write the schema
    uv run scripts/build_event_schema.py --check    # exit 1 if the committed schema would change

The schema is the one `decktalk schema event` prints, read off the event models in
`src/decktalk/events.py`: one line of the stream, discriminated by its `event`, with every kind and
every field. It sits in the folder the `schema` number of every result names, beside the result
schemas. Edit `src/decktalk/events.py`, then run this.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import generated
from decktalk.cli.catalog import SCHEMAS
from decktalk.results import SCHEMA

ROOT = Path(__file__).resolve().parent.parent

TARGET = ROOT / "schemas" / f"v{SCHEMA}" / "events.json"
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def documents() -> dict[Path, str]:
    """The one schema file this generator owns, by the path it is written to."""
    schema = {"$schema": DIALECT, "$id": TARGET.name, **SCHEMAS["event"]()}
    return {TARGET: json.dumps(schema, indent=2) + "\n"}


if __name__ == "__main__":
    sys.exit(generated.run(documents))
