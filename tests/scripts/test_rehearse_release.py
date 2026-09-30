"""The version `scripts/rehearse_release.py` rehearses, held to the rules of the candidate cycle.

The bump itself is release-please's own code, run by `next_version.mjs --bump`, and
`tests/scripts/next_version.test.mjs` holds it to this repository's config. The whole rehearsal runs
as its own row in `scripts/check.py`, so nothing here regenerates anything. These tests hold the
judgement the rehearsal makes before any bump, and the copy it makes the bump in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rehearse_release as rehearse
from support.paths import REPO

NOTES = (
    "## [0.5.0-rc3](https://github.com/o/r/compare/v0.5.0-rc2...v0.5.0-rc3) (2026-01-02)\n\n\n"
    "### Bug Fixes\n\n* a fix\n"
)


def report(**changes: object) -> dict[str, object]:
    """What scripts/next_version.mjs reports for a tree on 0.5.0-rc2 with one fix since, with `changes` over it."""
    base: dict[str, object] = {
        "tree": "0.5.0-rc2",
        "released": "0.5.0-rc2",
        "next": "0.5.0-rc3",
        "named": None,
        "dropped": [],
        "rehearse": "0.5.0-rc3",
        "rehearseNotes": NOTES,
    }
    return base | changes


def test_the_next_candidate_is_rehearsed() -> None:
    assert rehearse.judged(".", report()) == "0.5.0-rc3"


def test_a_final_version_a_footer_named_is_rehearsed() -> None:
    assert rehearse.judged(".", report(next="0.5.0", rehearse="0.5.0", named="0.5.0")) == "0.5.0"


def test_a_final_version_nobody_named_is_refused() -> None:
    with pytest.raises(rehearse.Refused, match="no Release-As footer named it"):
        rehearse.judged(".", report(next="0.5.0", rehearse="0.5.0"))


def test_a_candidate_with_no_number_is_refused() -> None:
    with pytest.raises(rehearse.Refused, match="no number"):
        rehearse.judged(".", report(next="0.6.0-rc", rehearse="0.6.0-rc"))


def test_a_footer_release_please_never_reads_is_refused() -> None:
    dropped = [{"sha": "abcdef0123", "version": "0.5.0"}]
    with pytest.raises(rehearse.Refused, match="touches only excluded paths"):
        rehearse.judged(".", report(dropped=dropped))


def test_the_release_pull_request_is_judged_and_left_alone() -> None:
    assert rehearse.judged(".", report(tree="0.5.0-rc3")) is None


def test_a_release_pull_request_that_disagrees_with_the_rules_is_refused() -> None:
    with pytest.raises(rehearse.Refused, match="disagree"):
        rehearse.judged(".", report(tree="0.5.0-rc4"))


def test_the_copy_carries_the_checkout_and_no_repository_to_commit_to(tmp_path: Path) -> None:
    rehearse.copy_checkout(REPO, tmp_path)
    config = "release-please-config.json"
    assert (tmp_path / config).read_bytes() == (REPO / config).read_bytes()
    assert not (tmp_path / ".git").exists()
