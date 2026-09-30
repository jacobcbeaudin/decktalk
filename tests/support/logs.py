"""The records a test captured, read one way by every test that asks what a record carried or why."""

from __future__ import annotations

import logging
from typing import Any

import pytest


def data_of(record: logging.LogRecord) -> dict[str, Any]:
    """The flat fields a record carries under `extra={"data": ...}`, or none for a record without them."""
    return getattr(record, "data", {})


def decisions(caplog: pytest.LogCaptureFixture, cache: str, *fields: str) -> list[tuple[object, ...]]:
    """Every decision about `cache` since the last read of it, as `fields` of each, `hit` and `why` by default.

    The records it returns leave the capture, so the next read about the same cache sees only what
    the code under test decided after this one, and a decision about another cache stays to be read.
    """
    named = fields or ("hit", "why")
    read = [record for record in caplog.records if data_of(record).get("cache") == cache]
    caplog.records[:] = [record for record in caplog.records if record not in read]
    return [tuple(data_of(record)[field] for field in named) for record in read]
