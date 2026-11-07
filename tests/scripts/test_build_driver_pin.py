"""`scripts/build_driver_pin.py`, which writes the conda package's driver range from the client's.

The generated row runs it with `--check` on every pull request, so a Renovate pull request that
moves the client's range alone fails there with the command that writes the driver's line.
"""

from __future__ import annotations

import re
import tomllib

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

import build_driver_pin as pin

CLIENT_LINE = re.compile(r'^    "playwright(?P<range>[^"]+)",$', re.M)
DRIVER_LINE = re.compile(r'^playwright = "(?P<range>[^"]+)"$', re.M)


def moved(monkeypatch: pytest.MonkeyPatch, client: str) -> str:
    """The pyproject.toml the generator writes once the client's range in `[project.dependencies]` is `client`."""
    text = pin.MANIFEST.read_text(encoding="utf-8")
    edited = CLIENT_LINE.sub(f'    "playwright{client}",', text, count=1)
    assert edited != text, "pyproject.toml no longer writes the client's range on a line this test can move"
    monkeypatch.setattr(pin.Path, "read_text", lambda path, encoding=None: edited)
    return pin.files()[pin.MANIFEST]


def test_the_driver_holds_the_clients_range() -> None:
    text = pin.MANIFEST.read_text(encoding="utf-8")
    assert pin.files()[pin.MANIFEST] == text
    assert DRIVER_LINE.findall(text) == CLIENT_LINE.findall(text)


CLIENT = SpecifierSet(
    pin.client_range(tomllib.loads(pin.MANIFEST.read_text(encoding="utf-8"))["project"]["dependencies"])
)
FLOOR, CEILING = (Version(spec.version) for operator in (">=", "<") for spec in CLIENT if spec.operator == operator)
"""The client's range today, from which each move below is written, so a Renovate bump needs no edit here."""


@pytest.mark.parametrize(
    "client",
    [
        f">={FLOOR.major}.{FLOOR.minor + 1},<{CEILING.major}.{CEILING.minor + 1}",
        f">={FLOOR},<{CEILING.major}.{CEILING.minor + 1}",
    ],
    ids=["the minor", "the ceiling alone"],
)
def test_a_client_range_that_moves_moves_the_driver_with_both_its_bounds(
    monkeypatch: pytest.MonkeyPatch, client: str
) -> None:
    """A driver whose floor or ceiling differs from the client's admits a driver of another minor."""
    written = moved(monkeypatch, client)
    assert DRIVER_LINE.findall(written) == [client], written
