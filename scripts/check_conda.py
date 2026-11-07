"""Install the built conda package where nothing else is installed, and judge what it holds and does.

    pixi run python scripts/check_conda.py    # after `pixi publish --path . --target-dir dist-conda`

The conda package is what `pixi global install` gives a user, so it is judged the way a user meets
it. The one package `pixi publish` left in `dist-conda/` is installed with `pixi global install`
into an empty Pixi home with an empty package cache, which solves and downloads its dependencies
from conda-forge as a new user's machine does. The cache is empty on purpose: Pixi keeps an
unpacked package under its name, version and build string, which a rebuild of the same version
shares, so a warm cache would install the package an earlier build left and judge that.
Then the check reads what the install recorded and runs the command:

- The package depends on each package pyproject.toml names, and on nothing else, each at the range
  pyproject.toml gives it. A package may name one dependency more than once, as `python >=3.12,<3.15`
  and `python >=3.12`, and a solver holds it to every bound, so the bounds of one name are joined
  before they are compared. The PyPI names are renamed as `pypi-conda-map` renames them, so the
  Python client is `playwright-python` and the Node driver is `playwright`.
- The package carries none of the files an installer writes about the build machine, because a
  package served to every user must not name one build's time or checkout.
- The package is the version pyproject.toml holds, `decktalk --version` runs, and `decktalk doctor`
  passes.

`doctor` reads the tools `decktalk install` fetched into this machine's cache, which the row
prepares, because the command a user meets first on a new machine is the one that reports them.

This script takes no mode, as `check_wheel.py` takes none, because it writes no file of its own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "pyproject.toml"
DIST = ROOT / "dist-conda"
"""Where the row's `pixi publish --target-dir` leaves the package, which release.yml uploads from."""

PACKAGE = "decktalk"

BUILD_MACHINE_FILES = ("direct_url.json", "uv_cache.json", "uv_build.json", "INSTALLER", "REQUESTED")
"""Files an installer writes into dist-info about the machine it ran on: its checkout, its clock, itself."""

STEP_TIMEOUT_SECONDS = 900
"""Calibration: a first solve of the package's dependencies from conda-forge on a slow runner, and no more."""


def built() -> Path:
    """The one package `pixi publish` left, because a release uploads one artifact and not a choice."""
    found = sorted(DIST.glob(f"{PACKAGE}-*.conda")) if DIST.is_dir() else []
    if len(found) != 1:
        names = ", ".join(path.name for path in found) or "nothing"
        raise SystemExit(f"{DIST.name}/ holds {names}, and the row judges one package. Empty it and build again.")
    return found[0]


def run(*argv: str | Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = [str(part) for part in argv]
    print(f"$ {' '.join(command)}", flush=True)
    return subprocess.run(command, env=env, capture_output=True, text=True, timeout=STEP_TIMEOUT_SECONDS, check=False)


def record(home: Path) -> dict[str, Any]:
    """What the install recorded about the package: its version, its dependencies and its files."""
    [path] = (home / "envs" / PACKAGE / "conda-meta").glob(f"{PACKAGE}-*.json")
    return json.loads(path.read_text(encoding="utf-8"))


ANY = "*"
"""How a conda dependency names no bound."""


def joined(lines: Iterable[str]) -> dict[str, SpecifierSet]:
    """Each dependency of `lines`, written as conda writes one, by name, with every bound its lines give it."""
    found: dict[str, SpecifierSet] = {}
    for line in lines:
        name, _, spec = line.partition(" ")
        spec = spec.replace(" ", "")
        found[name] = found.get(name, SpecifierSet()) & SpecifierSet("" if spec == ANY else spec)
    return found


def promised(manifest: dict[str, Any]) -> dict[str, SpecifierSet]:
    """Every dependency pyproject.toml promises the conda package, by its conda name, with its range."""
    package = manifest["tool"]["pixi"]["package"]
    renamed = package["build"]["config"].get("pypi-conda-map", {})
    lines = [f"python {manifest['project']['requires-python']}"]
    for line in manifest["project"]["dependencies"]:
        requirement = Requirement(line)
        lines.append(f"{renamed.get(requirement.name, requirement.name)} {requirement.specifier or ANY}")
    lines += [f"{name} {spec}" for name, spec in package["run-dependencies"].items()]
    return joined(lines)


def problems(held: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """What the installed package says that differs from what pyproject.toml promises a user."""
    found: list[str] = []
    wanted = promised(manifest)
    try:
        depends = joined(str(line) for line in held["depends"])
    except InvalidSpecifier as unread:
        return [f"the package names a dependency this check cannot read: {unread}"]
    for name in sorted(wanted.keys() | depends.keys()):
        if depends.get(name) != wanted.get(name):
            holds = f"to {depends[name] or ANY}" if name in depends else "not at all"
            says = repr(str(wanted[name] or ANY)) if name in wanted else "nothing"
            found.append(f"the package holds {name} {holds} where pyproject.toml says {says}")
    leaked = [file for file in held["files"] if Path(file).name in BUILD_MACHINE_FILES]
    if leaked:
        found.append(f"the package carries files about the machine that built it: {', '.join(leaked)}")
    if Version(str(held["version"])) != Version(manifest["project"]["version"]):
        found.append(f"the package is {held['version']} and pyproject.toml is {manifest['project']['version']}")
    return found


def main() -> int:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    package = built()
    with tempfile.TemporaryDirectory(prefix="decktalk-conda-") as scratch:
        home = Path(scratch)
        env = {**os.environ, "PIXI_HOME": str(home / "home"), "PIXI_CACHE_DIR": str(home / "cache")}
        installed = run("pixi", "global", "install", "--path", package, env=env)
        if installed.returncode:
            print(f"pixi global install refused {package.name}:\n{installed.stdout}{installed.stderr}")
            return 1
        found = problems(record(home / "home"), manifest)
        decktalk = shutil.which(PACKAGE, path=str(home / "home" / "bin"))
        if decktalk is None:
            found.append(f"pixi global install exposed no {PACKAGE} command")
        else:
            for argv in (("--version",), ("doctor", "--no-input")):
                done = run(decktalk, *argv, env=env)
                if done.returncode:
                    found.append(f"`{PACKAGE} {' '.join(argv)}` exited {done.returncode}:\n{done.stdout}{done.stderr}")
    for problem in found:
        print(problem)
    if found:
        return 1
    print(f"{package.name} installs with pixi global install, runs, and holds what pyproject.toml promises.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
