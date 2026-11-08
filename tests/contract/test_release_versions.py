"""Every version the release writes names one version, read the way release-please reads it.

release-please writes the version into the manifest, `pyproject.toml` and every entry of
`extra-files` in `release-please-config.json`, and reads the manifest back on the next release. This
holds those files to one version under PEP 440, so a hand edit that moves one of them is caught on
its own pull request rather than in a wheel that reports a different version than its tag. The files
are read from the config, so a new extra file is held here by being added there.

A marked line is read the way release-please's generic updater reads it, with an unanchored semver
pattern, so a PyPI spelling such as `0.5.0rc1` on a marked line reads as `0.5.0` and fails the one
version rule. The spelling of a whole field, in `pyproject.toml`, is free, because release-please
replaces the field entire. pixi.lock holds no version of the package itself, so it is not a file
the release writes.

The config is held to the candidate cycle RELEASING.md describes under "Candidates and finals": the
package stays in the series, and a final release is named by a footer rather than by editing the config.

`scripts/check_wheel.py` holds the tag to the built wheel at release time. This reads only the
checkout, so the two never judge the same pair.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import Any

import pytest
from packaging.version import InvalidVersion, Version

from support.paths import REPO

CONFIG = REPO / "release-please-config.json"
PACKAGE_JSON = REPO / "package.json"
MANIFEST = REPO / ".release-please-manifest.json"
PYPROJECT = REPO / "pyproject.toml"

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
"""A version as release-please writes one, which is the only spelling it reads back whole."""

LINE_MARKER = "x-release-please-version"
# A block rewrites every version between its start and its end, which the README uses for its pins.
BLOCK_START = "x-release-please-start-version"
BLOCK_END = "x-release-please-end"
"""The comment that marks a line release-please's generic updater rewrites."""

PRERELEASE_VERSIONING = "prerelease"
"""The versioning strategy that numbers the candidates of a series itself."""


def config() -> dict[str, Any]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def packages() -> dict[str, dict[str, Any]]:
    return config()["packages"]


def marked_lines(text: str) -> list[str]:
    """Every line the generic updater rewrites: a line with its marker, or a line between a block's start and end."""
    found: list[str] = []
    inside = False
    for line in text.splitlines():
        if BLOCK_START in line:
            inside = True
            continue
        if BLOCK_END in line:
            inside = False
            continue
        if inside or LINE_MARKER in line:
            found.append(line)
    return found


def generic_versions(text: str) -> list[str]:
    """Every version the generic updater rewrites, on every line it rewrites."""
    return [version for line in marked_lines(text) for version in SEMVER.findall(line)]


def generic_files() -> list[str]:
    """Every extra file the generic updater writes, by its path in the checkout."""
    return [
        extra["path"]
        for package in packages().values()
        for extra in package.get("extra-files", [])
        if extra["type"] == "generic"
    ]


def written_versions() -> dict[str, str]:
    """Every version the release writes, keyed by the file and the place in it that carries it."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    versions = {f"{MANIFEST.name} {path}": manifest[path] for path in packages()}
    versions["pyproject.toml"] = project["version"]
    for package in packages().values():
        for extra in package.get("extra-files", []):
            text = (REPO / extra["path"]).read_text(encoding="utf-8")
            if extra["type"] == "generic":
                found = generic_versions(text)
            else:
                pytest.fail(f"{extra['path']} is bumped by the {extra['type']!r} updater, which this test cannot read")
            if not found:
                pytest.fail(f"{extra['path']} carries no version release-please rewrites")
            if len(set(found)) != 1:
                pytest.fail(f"{extra['path']} carries more than one version in its marked places: {sorted(set(found))}")
            versions[extra["path"]] = found[0]
    return versions


def test_every_version_the_release_writes_is_one_version():
    versions = written_versions()
    try:
        parsed = {place: Version(spelled) for place, spelled in versions.items()}
    except InvalidVersion as error:
        pytest.fail(f"a version the release writes is not a PEP 440 version: {error}")
    assert len(set(parsed.values())) == 1, f"the release writes more than one version: {versions}"


@pytest.mark.parametrize("path", generic_files())
def test_every_marked_line_carries_one_version(path):
    """The generic updater replaces the first version on each line it rewrites and leaves the others,
    so a second version on a marked line keeps the old number on the release branch. Each marked line
    names its version once."""
    lines = marked_lines((REPO / path).read_text(encoding="utf-8"))
    doubled = [line.strip() for line in lines if len(SEMVER.findall(line)) > 1]
    assert not doubled, f"{path} names a version more than once on a line the updater rewrites: {doubled}"


def test_the_config_pins_no_version():
    # A pinned version is read on every release until someone removes it, so the release after it
    # proposes the same version again. A version is named with a Release-As footer instead.
    pinned = [path for path, package in packages().items() if "release-as" in package]
    assert "release-as" not in config() and not pinned, (
        "release-please-config.json pins release-as. Name the version with a Release-As footer instead."
    )


@pytest.mark.parametrize("path", list(packages()))
def test_every_package_stays_in_the_candidate_series(path):
    package = packages()[path]
    assert package.get("versioning") == PRERELEASE_VERSIONING and package.get("prerelease") is True, (
        f"{path} leaves the candidate series. A final release is named with a Release-As footer instead."
    )
    kind = package.get("prerelease-type", "")
    assert re.fullmatch(r"[a-z]+\d+", kind), (
        f"{path} sets prerelease-type {kind!r}, and a series after a final release starts at its exact "
        "spelling, so it names a type and a first number, such as rc1."
    )


def test_release_please_is_pinned_exactly():
    # scripts/next_version.mjs runs release-please's own code, which is only the release's answer
    # while both run one version. A range would let them part, and tests/scripts/test_renovate.py
    # holds Renovate to leaving the pin alone.
    pinned = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["devDependencies"]["release-please"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned), f"package.json names release-please as {pinned!r}, not one version"
