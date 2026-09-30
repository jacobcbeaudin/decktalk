"""What a voice service answers with, and the credentials it is not asked for, spelled once for every speech test."""

from __future__ import annotations

from typing import Any, NoReturn

import pytest


def alignment(text: str, *, per_char: float = 0.1) -> dict[str, list[Any]]:
    """A character alignment of `text` at a fixed pace, which is what the endpoint answers with."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(i * per_char, 3) for i in range(len(text))],
        "character_end_times_seconds": [round((i + 1) * per_char, 3) for i in range(len(text))],
    }


class NoSecrets:
    """The credentials of a context whose provider a test never opens, which fail the test that asks for one."""

    def require(self, *names: str) -> NoReturn:
        pytest.fail(f"a provider asked for {', '.join(names)}, which this test never gives it")
