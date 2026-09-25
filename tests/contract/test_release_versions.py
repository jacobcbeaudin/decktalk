"""Every version the release writes names one version, spelled the way release-please reads it back.

release-please writes the version into the manifest, `pyproject.toml` and every entry of
`extra-files` in `release-please-config.json`, and reads the manifest back on the next release. This
holds those files to one version under PEP 440, so a hand edit that moves one of them is caught on
its own pull request rather than in a wheel that reports a different version than its tag. The files
are read from the config, so a new extra file is held here by being added there.

release-please parses a version with an unanchored semver pattern, so `0.5.0rc1` does not error: it
matches `0.5.0` and drops the prerelease. Every version the release writes, and every version the
config names, is therefore held to the semver spelling with a hyphen before the prerelease part.

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
MANIFEST = REPO / ".release-please-manifest.json"
PYPROJECT = REPO / "pyproject.toml"

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
"""A version as release-please writes one, which is the only spelling it reads back whole."""

LINE_MARKER = "x-release-please-version"
"""The comment that marks a line release-please's generic updater rewrites."""

TOML_ROW = re.compile(
    r"^\$\.(?P<table>[\w-]+)"
    r"\[\?\(@\.(?P<key>[\w-]+)\.value==['\"](?P<match>[^'\"]+)['\"]\)\]"
    r"\.(?P<field>[\w-]+)$"
)
"""The one jsonpath shape the config uses for a TOML file: a field of the array rows one key picks."""

PRERELEASE_VERSIONING = "prerelease"
"""The versioning strategy that numbers the candidates of a series itself."""


def config() -> dict[str, Any]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def packages() -> dict[str, dict[str, Any]]:
    return config()["packages"]


def generic_versions(text: str) -> list[str]:
    """Every version on a line the generic updater rewrites."""
    return [found for line in text.splitlines() if LINE_MARKER in line for found in SEMVER.findall(line)]


def toml_versions(text: str, jsonpath: str) -> list[str]:
    """The field `jsonpath` names, in every array row its filter picks."""
    path = TOML_ROW.match(jsonpath)
    if path is None:
        pytest.fail(f"the jsonpath {jsonpath!r} is not a shape this test can follow, so teach it the new shape")
    rows = tomllib.loads(text).get(path["table"], [])
    return [row[path["field"]] for row in rows if row.get(path["key"]) == path["match"]]


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
            elif extra["type"] == "toml":
                found = toml_versions(text, extra["jsonpath"])
            else:
                pytest.fail(f"{extra['path']} is bumped by the {extra['type']!r} updater, which this test cannot read")
            if len(found) != 1:
                pytest.fail(f"{extra['path']} carries {len(found)} versions release-please rewrites, not one")
            versions[extra["path"]] = found[0]
    return versions


def test_every_version_the_release_writes_is_one_version():
    versions = written_versions()
    try:
        parsed = {place: Version(spelled) for place, spelled in versions.items()}
    except InvalidVersion as error:
        pytest.fail(f"a version the release writes is not a PEP 440 version: {error}")
    assert len(set(parsed.values())) == 1, f"the release writes more than one version: {versions}"


def test_every_version_the_release_writes_is_spelled_as_semver():
    wrong = {place: spelled for place, spelled in written_versions().items() if not SEMVER.fullmatch(spelled)}
    assert not wrong, f"release-please would read these as a different version, so spell them with a hyphen: {wrong}"


def test_the_config_pins_no_version():
    # A pinned version is read on every release until someone removes it, so the release after it
    # proposes the same version again. A version is named with a Release-As footer instead.
    pinned = [path for path, package in packages().items() if "release-as" in package]
    assert "release-as" not in config() and not pinned, (
        "release-please-config.json pins release-as. Name the version with a Release-As footer instead."
    )


@pytest.mark.parametrize("path", list(packages()))
def test_a_series_is_entered_and_left_whole(path):
    package = packages()[path]
    in_series = package.get("versioning") == PRERELEASE_VERSIONING
    assert package.get("prerelease", False) == in_series, (
        f"{path} sets prerelease and versioning apart, so its releases would be flagged against their numbering."
    )
    if "prerelease-type" in package:
        assert in_series, f"{path} sets prerelease-type, which only the prerelease strategy reads."
        assert SEMVER.fullmatch(f"0.0.0-{package['prerelease-type']}"), (
            f"{path} sets a prerelease-type release-please would truncate."
        )
