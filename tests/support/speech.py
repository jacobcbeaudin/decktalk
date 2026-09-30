"""What a voice service answers with, spelled once for the provider tests and the canary run."""

from __future__ import annotations

from typing import Any


def alignment(text: str, *, per_char: float = 0.1) -> dict[str, list[Any]]:
    """A character alignment of `text` at a fixed pace, which is what the endpoint answers with."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(i * per_char, 3) for i in range(len(text))],
        "character_end_times_seconds": [round((i + 1) * per_char, 3) for i in range(len(text))],
    }
