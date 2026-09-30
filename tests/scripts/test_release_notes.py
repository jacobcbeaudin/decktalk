"""The notes a final release carries are every section its candidates and its own release wrote."""

from __future__ import annotations

import json

import pytest

import build_changelog
import release_notes
from support.paths import REPO

CHANGELOG = """# Changelog

## [0.5.0](https://github.com/o/r/compare/v0.5.0-rc2...v0.5.0) (2026-01-03)


### Bug Fixes

* the last fix

## [0.5.0-rc2](https://github.com/o/r/compare/v0.5.0-rc1...v0.5.0-rc2) (2026-01-02)


### Bug Fixes

* a candidate fix

## [0.5.0-rc1](https://github.com/o/r/compare/v0.4.1...v0.5.0-rc1) (2026-01-01)


### Features

* the feature

## [0.4.1](https://github.com/o/r/compare/v0.4.0...v0.4.1) (2025-12-01)


### Bug Fixes

* an older fix
"""


def test_a_final_release_carries_every_candidate_of_its_series() -> None:
    notes = release_notes.notes(CHANGELOG, "0.5.0")
    assert notes == "### Bug Fixes\n\n* the last fix\n* a candidate fix\n\n### Features\n\n* the feature\n"


def test_a_change_a_squash_merge_carried_twice_is_listed_once() -> None:
    """The notes fold duplicates exactly as the docs page does, because both render through one body."""
    twice = CHANGELOG.replace("* the last fix\n", "* the last fix ([abc1234](l))\n* the last fix ([def5678](l))\n")
    assert release_notes.notes(twice, "0.5.0").count("the last fix") == 1


def test_an_older_release_is_left_out() -> None:
    assert "older" not in release_notes.notes(CHANGELOG, "0.5.0")


@pytest.mark.parametrize(
    ("version", "match"),
    [
        pytest.param("0.5.0-rc2", "candidate", id="a candidate keeps the notes release-please wrote"),
        pytest.param("0.6.0", "no entry for 0.6.0", id="a version the changelog never released"),
    ],
)
def test_a_version_with_no_notes_of_its_own_is_refused(version: str, match: str) -> None:
    with pytest.raises(SystemExit, match=match):
        release_notes.notes(CHANGELOG, version)


def test_every_section_the_changelog_shows_carries_a_docs_tag() -> None:
    """A heading the config shows and the docs page cannot tag would publish a release with no tag."""
    config = json.loads((REPO / "release-please-config.json").read_text(encoding="utf-8"))
    shown = {row["section"] for row in config["packages"]["."]["changelog-sections"] if not row.get("hidden")}
    assert shown <= set(build_changelog.TAGS)
