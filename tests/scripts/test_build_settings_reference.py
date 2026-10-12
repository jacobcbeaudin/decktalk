"""The configuration reference names the commands `--set` is on, read off the command line itself."""

from __future__ import annotations

import build_settings_reference
from decktalk.cli import catalog


def test_the_set_layer_names_exactly_the_commands_that_take_set() -> None:
    """A command with no `--set` refuses it as USAGE, so the layer list names only the ones that take it."""
    text = build_settings_reference.layers()
    line = next(line for line in text.splitlines() if line.startswith("4. `--set"))
    for row in catalog.walk():
        takes = any("--set" in param["opts"] for param in row["params"])
        assert (f"`{row['command']}`" in line) is takes, row["command"]
