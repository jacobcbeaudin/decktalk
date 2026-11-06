"""`scripts/check_playwright_pair.py`, judged on lists of files shaped as anaconda.org gives them.

The check reads conda-forge over the network in the `playwright-pair` row, on `main`. These tests
reach nothing: they hand the judging functions the lists a channel might hold and the manifest a
change might write.
"""

from __future__ import annotations

import copy
import tomllib
from typing import Any

from packaging.specifiers import SpecifierSet

import check_playwright_pair as pair

WANTED = SpecifierSet(">=1.64,<1.65")


def files(*rows: tuple[str, str], label: str = "main") -> dict[str, Any]:
    """A package listing with one file per (version, platform), every one on `label`."""
    listed = [{"version": version, "attrs": {"subdir": subdir}, "labels": [label]} for version, subdir in rows]
    return {"files": listed, "versions": sorted({version for version, _ in rows})}


CLIENT = files(("1.62.0", "noarch"), ("1.64.1", "noarch"))
DRIVER = files(*(("1.64.1", platform) for platform in pair.PLATFORMS))


def manifest() -> dict[str, Any]:
    return tomllib.loads(pair.MANIFEST.read_text(encoding="utf-8"))


def test_the_manifest_holds_the_driver_to_the_clients_minor() -> None:
    assert pair.manifest_problems(manifest()) == []
    assert pair.client_range(manifest()) == WANTED


def test_a_floor_moved_without_the_driver_is_named() -> None:
    moved = copy.deepcopy(manifest())
    moved["project"]["dependencies"] = [
        "playwright>=1.65,<1.66" if line.startswith("playwright") else line for line in moved["project"]["dependencies"]
    ]
    [problem] = pair.manifest_problems(moved)
    assert "driver" in problem, problem


def test_a_pair_conda_forge_holds_passes() -> None:
    assert pair.channel_problems(WANTED, CLIENT, DRIVER) == []


def test_a_client_minor_conda_forge_skipped_is_named() -> None:
    """conda-forge built playwright-python 1.62 and then 1.64, so a floor of 1.63 had nothing to solve to."""
    problems = pair.channel_problems(SpecifierSet(">=1.63,<1.64"), CLIENT, DRIVER)
    assert any("playwright-python" in problem for problem in problems), problems


def test_a_driver_missing_on_one_platform_names_that_platform() -> None:
    driver = files(*(("1.64.1", platform) for platform in pair.PLATFORMS if platform != "win-64"))
    [problem] = pair.channel_problems(WANTED, CLIENT, driver)
    assert problem.endswith("for win-64"), problem


def test_a_file_off_the_main_label_does_not_count() -> None:
    """`pixi global install` reads the main label, so a build parked under another label solves nothing."""
    [problem] = pair.channel_problems(WANTED, files(("1.64.1", "noarch"), label="broken"), DRIVER)
    assert "playwright-python" in problem, problem
