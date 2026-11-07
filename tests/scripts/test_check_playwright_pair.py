"""`scripts/check_playwright_pair.py`, judged on lists of files shaped as anaconda.org gives them.

The check reads conda-forge over the network in the `playwright-pair` row, which release.yml runs.
These tests reach nothing: they hand the judging functions the lists a channel might hold and the
manifest a change might write. The range they judge is read from pyproject.toml, so a Renovate pull
request that moves the Playwright minor needs no edit here. The listings model conda-forge's own
history, so they are written relative to that range.
"""

from __future__ import annotations

import tomllib
from typing import Any

from packaging.specifiers import SpecifierSet
from packaging.version import Version

import check_playwright_pair as pair


def manifest() -> dict[str, Any]:
    return tomllib.loads(pair.MANIFEST.read_text(encoding="utf-8"))


WANTED = pair.client_range(manifest())
"""The one minor pyproject.toml holds the client to, which is the pair every test below judges."""

FLOOR = Version(next(spec.version for spec in WANTED if spec.operator == ">="))
"""The floor of that minor, such as 1.64, from which each listing below is written."""


def minor(offset: int) -> str:
    """The minor `offset` releases from the floor, as `1.65` for an offset of one."""
    return f"{FLOOR.major}.{FLOOR.minor + offset}"


def files(*rows: tuple[str, str], label: str = "main") -> dict[str, Any]:
    """A package listing with one file per (version, platform), every one on `label`."""
    listed = [{"version": version, "attrs": {"subdir": subdir}, "labels": [label]} for version, subdir in rows]
    return {"files": listed, "versions": sorted({version for version, _ in rows})}


CLIENT = files((f"{minor(-2)}.0", "noarch"), (f"{minor(0)}.1", "noarch"))
DRIVER = files(*((f"{minor(0)}.1", platform) for platform in pair.PLATFORMS))


def test_a_pair_conda_forge_holds_passes() -> None:
    assert pair.channel_problems(WANTED, CLIENT, DRIVER) == []


def test_a_client_minor_conda_forge_skipped_is_named() -> None:
    """conda-forge once built playwright-python 1.62 and then 1.64, so a floor of 1.63 had nothing to solve to."""
    problems = pair.channel_problems(SpecifierSet(f">={minor(-1)},<{minor(0)}"), CLIENT, DRIVER)
    assert any("playwright-python" in problem for problem in problems), problems


def test_a_driver_missing_on_one_platform_names_that_platform() -> None:
    driver = files(*((f"{minor(0)}.1", platform) for platform in pair.PLATFORMS if platform != "win-64"))
    [problem] = pair.channel_problems(WANTED, CLIENT, driver)
    assert problem.endswith("for win-64"), problem


def test_a_file_off_the_main_label_does_not_count() -> None:
    """`pixi global install` reads the main label, so a build parked under another label solves nothing."""
    [problem] = pair.channel_problems(WANTED, files((f"{minor(0)}.1", "noarch"), label="broken"), DRIVER)
    assert "playwright-python" in problem, problem
