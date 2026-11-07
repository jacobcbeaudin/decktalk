"""Write the Playwright driver's range in the conda package from the client's range in `[project.dependencies]`.

    pixi run python scripts/build_driver_pin.py --write    # write the driver's line from the client's range
    pixi run python scripts/build_driver_pin.py --check    # exit 1 if the two ranges differ

conda-forge's playwright-python, the Python client, does not pin the Node driver it runs, and a
driver of another minor fails at the first launch. So the conda package names the driver in
`[tool.pixi.package.run-dependencies]` beside the client. The client's range in
`[project.dependencies]` is the one home of the Playwright minor, which Renovate moves, and this
generator writes the driver's line from it, as the client's range is written.

The line is written between two comment markers, so every other line of pyproject.toml stays as a
person wrote it. The script reads no network.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

import generated

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "pyproject.toml"

CLIENT = "playwright"
"""The PyPI name of the Python client in `[project.dependencies]`, which is also the conda name of the driver."""

BLOCK = ("# playwright-driver:start\n", "# playwright-driver:end\n")


def client_range(dependencies: list[str]) -> str:
    """The client's range as `[project.dependencies]` writes it, such as `>=1.64,<1.65`."""
    for line in dependencies:
        requirement = Requirement(line)
        if requirement.name == CLIENT:
            return line.removeprefix(requirement.name).strip()
    raise SystemExit(f"pyproject.toml no longer depends on {CLIENT}, so the conda package names no driver.")


def files() -> dict[Path, str]:
    text = MANIFEST.read_text(encoding="utf-8")
    written = f'{CLIENT} = "{client_range(tomllib.loads(text)["project"]["dependencies"])}"\n'
    return {MANIFEST: generated.splice(text, BLOCK, written, where=MANIFEST)}


if __name__ == "__main__":
    sys.exit(generated.run(files))
