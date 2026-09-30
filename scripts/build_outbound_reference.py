"""Generate docs/reference/what-leaves-your-machine.mdx from docs/data/outbound.toml.

    uv run scripts/build_outbound_reference.py --write    # write the page
    uv run scripts/build_outbound_reference.py --check    # exit 1 if the committed page would change

Every host on the page comes from the data file, so the page is a complete list by construction.
Add a host to docs/data/outbound.toml, then run this. The prose around the two tables lives in
scripts/what-leaves-your-machine.mdx.j2, which reads as the page does.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import jinja2

import generated

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "data" / "outbound.toml"
TARGET = ROOT / "docs" / "reference" / "what-leaves-your-machine.mdx"

TEMPLATE = ROOT / "scripts" / "what-leaves-your-machine.mdx.j2"
"""The page itself, with a loop where each table and each run of host notes is written from the data.

A field the data file leaves out stops the run, because a blank cell would publish a host with no
account of what it is sent.
"""


def documents() -> dict[Path, str]:
    """The page of every host DeckTalk contacts, written from the data file that lists them."""
    template = jinja2.Template(
        TEMPLATE.read_text(encoding="utf-8"), keep_trailing_newline=True, undefined=jinja2.StrictUndefined
    )
    return {TARGET: template.render(tomllib.loads(SOURCE.read_text(encoding="utf-8")))}


if __name__ == "__main__":
    sys.exit(generated.run(documents))
