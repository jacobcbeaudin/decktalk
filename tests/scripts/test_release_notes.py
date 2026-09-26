"""The notes a final release carries are every section its candidates and its own release wrote."""

from __future__ import annotations

import sys

import pytest

from support.paths import REPO

sys.path.insert(0, str(REPO / "scripts"))
import release_notes  # noqa: E402

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


def test_an_older_release_is_left_out() -> None:
    assert "older" not in release_notes.notes(CHANGELOG, "0.5.0")


def test_a_candidate_keeps_the_notes_release_please_wrote() -> None:
    with pytest.raises(SystemExit, match="candidate"):
        release_notes.notes(CHANGELOG, "0.5.0-rc2")


def test_a_version_the_changelog_never_released_is_refused() -> None:
    with pytest.raises(SystemExit, match="no entry for 0.6.0"):
        release_notes.notes(CHANGELOG, "0.6.0")
