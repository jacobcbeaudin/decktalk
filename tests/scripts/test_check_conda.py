"""`scripts/check_conda.py`, judged on the records a `pixi global install` of the package leaves.

The conda row builds and installs the real package. These tests hand the judging function the
records such an install might leave, so each promise fails on its own.
"""

from __future__ import annotations

import tomllib
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

import check_conda


def manifest() -> dict[str, Any]:
    return tomllib.loads(check_conda.MANIFEST.read_text(encoding="utf-8"))


def built_depends() -> list[str]:
    """The dependencies pixi-build writes for a package that keeps every promise, in its own shape.

    pixi-build writes each `[project.dependencies]` entry under its conda name, `requires-python` and
    a bare `python`, and each run dependency, so one name may appear more than once.
    """
    package = manifest()["tool"]["pixi"]["package"]
    renamed = package["build"]["config"]["pypi-conda-map"]
    project = [Requirement(line) for line in manifest()["project"]["dependencies"]]
    return [
        *(f"{name} {spec}" for name, spec in package["run-dependencies"].items()),
        f"python {manifest()['project']['requires-python']}",
        "python *",
        *(f"{renamed.get(r.name, r.name)} {r.specifier}" for r in project),
    ]


def held(**over: object) -> dict[str, Any]:
    """The record of a package that keeps every promise, with `over` written over it."""
    record: dict[str, Any] = {
        "version": manifest()["project"]["version"],
        "depends": built_depends(),
        "files": ["site-packages/decktalk/__init__.py", "site-packages/decktalk-0.6.0.dist-info/METADATA"],
    }
    return record | over


def without(name: str) -> list[str]:
    return [line for line in built_depends() if line.partition(" ")[0] != name]


NAMES = sorted(check_conda.promised(manifest()))


def test_a_package_that_keeps_every_promise_passes() -> None:
    assert check_conda.problems(held(), manifest()) == []


def test_the_promise_names_the_client_the_driver_and_the_python_range() -> None:
    promised = check_conda.promised(manifest())
    pins = manifest()["tool"]["pixi"]["package"]["run-dependencies"]
    assert promised["python"] == SpecifierSet(pins["python"]) & SpecifierSet(manifest()["project"]["requires-python"])
    assert promised["playwright"] == SpecifierSet(pins["playwright"])
    assert "playwright-python" in promised


@pytest.mark.parametrize("name", check_conda.BUILD_MACHINE_FILES)
def test_a_file_about_the_build_machine_is_named(name: str) -> None:
    record = held(files=["site-packages/decktalk/__init__.py", f"site-packages/decktalk-0.6.0.dist-info/{name}"])
    [problem] = check_conda.problems(record, manifest())
    assert name in problem


@pytest.mark.parametrize("name", NAMES)
def test_a_dependency_the_package_drops_is_named(name: str) -> None:
    [problem] = check_conda.problems(held(depends=without(name)), manifest())
    assert f"holds {name} not at all" in problem, problem


@pytest.mark.parametrize("name", NAMES)
def test_a_dependency_held_to_another_range_is_named(name: str) -> None:
    """One more bound on a name moves its range, whichever of its lines carries the bound."""
    [problem] = check_conda.problems(held(depends=[*built_depends(), f"{name} <0.0.1"]), manifest())
    assert f"holds {name} to" in problem, problem


def test_a_dependency_pyproject_does_not_name_is_named() -> None:
    [problem] = check_conda.problems(held(depends=[*built_depends(), "numpy >=2"]), manifest())
    assert "numpy" in problem and "says nothing" in problem, problem


def test_a_package_of_another_version_is_named() -> None:
    [problem] = check_conda.problems(held(version="0.0.1"), manifest())
    assert "0.0.1" in problem
