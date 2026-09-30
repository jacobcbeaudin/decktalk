"""How DeckTalk replaces files a person owns, which is all of them together or none of them.

A fix and a settings write change files an author wrote, so a failure halfway must leave each of
them as it was. Every new text is first written under a fresh temporary name beside its target, and
only once every one of them is on disk does each move over its target. A move that fails puts back
every target an earlier move already replaced, so a fix never reports a refusal over a project it
half changed.

This module sits below every layer, because the settings file and the files a fix edits are
replaced by the same rule and the settings layer cannot reach up into the machine. It also holds the
one way DeckTalk writes JSON text that no digest is taken over, which every layer shares.
"""

from __future__ import annotations

import secrets
import shutil
from collections.abc import Mapping
from pathlib import Path

from pydantic_core import to_json

DRAFT_TOKEN_BYTES = 8
"""The random bytes in a draft's name, which is enough that no file already in the project carries it."""


def replace_all(texts: Mapping[Path, str | bytes]) -> None:
    """Replace every file in `texts` with its new content, all of them or, when anything fails, none.

    Each draft is created fresh under a name nobody could have planted, so a link already in the
    project is never written through, and it takes the mode of the file it replaces, so a change
    never alters who may read a file. Text is written as UTF-8 and bytes exactly as they are, because
    a copy of an engine file must match it byte for byte. Whatever fails is raised to the caller
    after every target has been put back, and no draft is left behind.
    """
    token = secrets.token_hex(DRAFT_TOKEN_BYTES)
    drafts: dict[Path, Path] = {}
    try:
        for path, text in texts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            draft = path.with_name(f".{path.name}.{token}.draft")
            with draft.open("xb") as handle:
                drafts[path] = draft
                handle.write(text if isinstance(text, bytes) else text.encode("utf-8"))
            if path.exists():
                shutil.copymode(path, draft)
        _move_over(drafts)
    finally:
        for draft in drafts.values():
            draft.unlink(missing_ok=True)


def _move_over(drafts: Mapping[Path, Path]) -> None:
    """Move each draft over its target, putting every replaced target back when one move fails."""
    before = {path: path.read_bytes() if path.is_file() else None for path in drafts}
    moved: list[Path] = []
    try:
        for path, draft in drafts.items():
            draft.replace(path)
            moved.append(path)
    except OSError:
        for path in moved:
            original = before[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        raise


def current_text(path: Path) -> str:
    """The text a file holds before a change replaces it, which is empty when the change creates it."""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def json_text(value: object, *, indent: int | None = None) -> str:
    """One value as JSON text, written by pydantic-core, whose serializer is Rust.

    Non-ASCII text is kept as it is, and a value JSON has no spelling for is written as its text. A
    digest is never taken over this text, because it is not the standard library's spelling: it
    puts no space between items, sorts no keys and escapes no non-ASCII character, so every key and
    every file a key reads keeps `json.dumps`.
    """
    return to_json(value, indent=indent, fallback=str).decode("utf-8")


__all__ = ["current_text", "json_text", "replace_all"]
