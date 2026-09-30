"""The cache decisions a test captured, read one way by every test that asks why something was kept."""

from __future__ import annotations

import pytest


def decisions(caplog: pytest.LogCaptureFixture, cache: str, *fields: str) -> list[tuple[object, ...]]:
    """Every decision about `cache` since the last read of it, as `fields` of each, `hit` and `why` by default.

    The records it returns leave the capture, so the next read about the same cache sees only what
    the code under test decided after this one, and a decision about another cache stays to be read.
    """
    named = fields or ("hit", "why")
    read = [record for record in caplog.records if getattr(record, "data", {}).get("cache") == cache]
    caplog.records[:] = [record for record in caplog.records if record not in read]
    return [tuple(record.data[field] for field in named) for record in read]  # type: ignore[attr-defined]
