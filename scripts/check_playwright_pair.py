"""Check that conda-forge holds the Playwright pair at the minor pyproject.toml names.

    pixi run python scripts/check_playwright_pair.py    # what is missing, or what was found

It exits 1 when something is wrong. The conda package `pixi global install` gives users depends on
two conda-forge packages for Playwright: `playwright-python`, the Python client, and `playwright`,
the Node driver it runs, which the client does not pin. The two must be one minor, so pyproject.toml
names one minor for the PyPI client, and scripts/build_driver_pin.py writes the same range for the
driver. conda-forge once skipped a whole client minor, so a floor that moves to a minor conda-forge
does not have makes the conda package unsolvable. Two things are checked.

- conda-forge has a `playwright-python` in that range, which is one noarch package.
- conda-forge has a `playwright` driver in that range on every platform users install on.

It reads anaconda.org's package API, which lists every file conda-forge has uploaded with its
version, platform and labels. Only files on the `main` label count, because that is the label
`pixi global install` reads.
"""

from __future__ import annotations

import json
import sys
import tomllib
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "pyproject.toml"

CLIENT, DRIVER = "playwright-python", "playwright"
"""The conda-forge names of the Python client and of the Node driver it runs."""

PLATFORMS = ("linux-64", "linux-aarch64", "osx-64", "osx-arm64", "win-64")
"""Where users install: the four targets install.sh downloads Pixi for, and Windows through PowerShell."""

API = "https://api.anaconda.org/package/conda-forge/{name}"

TIMEOUT_SECONDS = 30
"""How long one request to anaconda.org may take before the check fails rather than hangs."""

LABEL = "main"
"""The conda-forge label `pixi global install` reads, so a file under another label solves nothing."""

SHOWN_VERSIONS = 3
"""How many of conda-forge's newest client versions a failure names, enough to show which minor moved."""


def client_range(manifest: dict[str, Any]) -> SpecifierSet:
    """The range `[project.dependencies]` gives the PyPI `playwright`, which is the Python client."""
    for line in manifest["project"]["dependencies"]:
        requirement = Requirement(line)
        if requirement.name == "playwright":
            return requirement.specifier
    raise SystemExit("pyproject.toml no longer depends on playwright, so this check has nothing to hold.")


def fetch(name: str) -> dict[str, Any]:
    """Every file conda-forge has uploaded for one package, as anaconda.org's API lists it."""
    with urllib.request.urlopen(API.format(name=name), timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def in_range(files: Iterable[dict[str, Any]], wanted: SpecifierSet) -> dict[str, set[str]]:
    """The versions in `wanted` on the `main` label, by the platform each file was built for."""
    found: dict[str, set[str]] = {}
    for file in files:
        if LABEL not in file.get("labels", ()):
            continue
        try:
            version = Version(file["version"])
        except InvalidVersion:
            continue
        if wanted.contains(version):
            found.setdefault(file["attrs"]["subdir"], set()).add(str(version))
    return found


def channel_problems(wanted: SpecifierSet, client: dict[str, Any], driver: dict[str, Any]) -> list[str]:
    """What conda-forge lacks for the pair in `wanted`, given what it lists for the client and the driver."""
    problems = []
    if not in_range(client["files"], wanted).get("noarch"):
        newest = ", ".join(client["versions"][-SHOWN_VERSIONS:])
        problems.append(f"conda-forge has no {CLIENT} in {wanted}. Its newest are {newest}.")
    drivers = in_range(driver["files"], wanted)
    missing = [platform for platform in PLATFORMS if not drivers.get(platform)]
    if missing:
        problems.append(f"conda-forge has no {DRIVER} driver in {wanted} for {', '.join(missing)}")
    return problems


def main() -> int:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    wanted = client_range(manifest)
    problems = channel_problems(wanted, fetch(CLIENT), fetch(DRIVER))
    for problem in problems:
        print(problem)
    if problems:
        return 1
    print(f"conda-forge has {CLIENT} and the {DRIVER} driver in {wanted} on {', '.join(PLATFORMS)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
