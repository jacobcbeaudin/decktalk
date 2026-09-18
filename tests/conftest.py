"""Options shared by every test module."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--gate-timing",
        action="store_true",
        default=False,
        help="fail the pipeline test on OFF CUE on every platform, as it does on Linux by itself",
    )
