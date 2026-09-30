"""A committed per-file count that only ever shrinks, shared by the numbers rule and the vocabulary rule.

Both rules fail on day one, so each ships a baseline of what it excuses today. A file off the list
may hold nothing, a file on it may never grow, and `--write` lowers a beaten count and never raises
one. The rule decides what a file holds, and this module decides what the count may do.
"""

from __future__ import annotations

import json
from pathlib import Path


def baseline(path: Path) -> dict[str, int]:
    """The committed to-do list, which is the only thing that excuses a file."""
    return dict(json.loads(path.read_text(encoding="utf-8"))["files"])


def drift(found: dict[str, int], excused: dict[str, int]) -> tuple[list[str], list[str], list[str]]:
    """The files the list does not name, the files that grew and the files that shrank, each as a line."""
    added = [f"{name} ({count})" for name, count in sorted(found.items()) if name not in excused]
    grown = [f"{name} {was} to {found[name]}" for name, was in sorted(excused.items()) if found.get(name, 0) > was]
    stale = [
        f"{name} {was} to {found.get(name, 0)}" for name, was in sorted(excused.items()) if found.get(name, 0) < was
    ]
    return added, grown, stale


def write(path: Path, found: dict[str, int], *, noun: str, refusal: str) -> int:
    """Lower every count the code has beaten, and refuse to raise one or to excuse a new file."""
    committed = baseline(path)
    lowered = {name: min(count, found.get(name, 0)) for name, count in committed.items()}
    kept = {name: count for name, count in sorted(lowered.items()) if count}
    raised = sorted(name for name, count in found.items() if count > committed.get(name, 0))
    path.write_text(json.dumps({"files": kept}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.name} with {len(kept)} files and {sum(kept.values())} {noun}")
    if raised:
        print(f"{refusal}: " + ", ".join(raised))
        return 1
    return 0
