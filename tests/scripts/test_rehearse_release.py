"""The version `scripts/rehearse_release.py` rehearses, held to the rules of the candidate cycle.

The bump itself is release-please's own code, run by `next_version.mjs --bump`, and
`tests/scripts/next_version.test.mjs` holds it to this repository's config. The whole rehearsal runs
as its own row in `scripts/check.py`, so nothing here regenerates anything. These tests hold the
judgement the rehearsal makes before any bump, the copy it makes the bump in, and the commands it runs there.
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


@pytest.mark.parametrize(
    ("changes", "judged"),
    [
        pytest.param({}, "0.5.0-rc3", id="the next candidate"),
        pytest.param({"next": "0.5.0", "rehearse": "0.5.0", "named": "0.5.0"}, "0.5.0", id="a final a footer named"),
        pytest.param({"tree": "0.5.0-rc3"}, None, id="the release pull request is left alone"),
    ],
)
def test_a_release_the_rules_agree_on_is_rehearsed(changes: dict[str, object], judged: str | None) -> None:
    assert rehearse.judged(".", report(**changes)) == judged


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        pytest.param(
            {"next": "0.5.0", "rehearse": "0.5.0"}, "no Release-As footer named it", id="a final nobody named"
        ),
        pytest.param({"next": "0.6.0-rc", "rehearse": "0.6.0-rc"}, "no number", id="a candidate with no number"),
        pytest.param(
            {"dropped": [{"sha": "abcdef0123", "version": "0.5.0"}]},
            "touches only excluded paths",
            id="a footer release-please never reads",
        ),
        pytest.param({"tree": "0.5.0-rc4"}, "disagree", id="a release pull request that disagrees"),
    ],
)
def test_a_release_the_rules_cannot_vouch_for_is_refused(changes: dict[str, object], match: str) -> None:
    with pytest.raises(rehearse.Refused, match=match):
        rehearse.judged(".", report(**changes))


def test_the_copy_carries_the_checkout_and_no_repository_to_commit_to(tmp_path: Path) -> None:
    rehearse.copy_checkout(REPO, tmp_path)
    config = "release-please-config.json"
    assert (tmp_path / config).read_bytes() == (REPO / config).read_bytes()
    assert not (tmp_path / ".git").exists()


def test_the_bumped_copy_regenerates_then_runs_the_wheel_row_last() -> None:
    """The wheel is built from the regenerated copy, so it carries every file the bump made stale."""
    assert rehearse.REHEARSED[-1][-2:] == ("--group", "wheel")
    assert rehearse.REHEARSED.index((*rehearse.GENERATED, "--write")) < rehearse.REHEARSED.index(rehearse.WHEEL)


def test_every_command_in_the_copy_runs_in_the_copys_own_locked_environment() -> None:
    """The bumped package is installed in the copy's environment, so a command outside it reads the old version."""
    for command in rehearse.REHEARSED:
        assert command == rehearse.LOCK_CHECK or command[: len(rehearse.IN_COPY)] == rehearse.IN_COPY, command


def test_the_activation_is_every_variable_pixi_names_for_this_checkout_but_path() -> None:
    """The names are Pixi's own, so a variable a new Pixi adds, such as `PIXI_EXE`, is scrubbed too."""
    names = rehearse.activation()
    assert {"PIXI_PROJECT_ROOT", "PIXI_ENVIRONMENT_NAME", "PIXI_EXE", "CONDA_PREFIX"} <= names
    assert "PATH" not in names and "PIXI_CACHE_DIR" not in names
    assert not [name for name in names if name.startswith("CONDA_ENV_SHLVL_")], "Pixi answered as a nested activation"


def test_the_copy_runs_without_this_checkouts_activation_and_with_the_tag_to_judge() -> None:
    """A candidate's wheel is named `0.6.0rc1` and tagged `v0.6.0-rc1`, which only a bumped build can show."""
    activated = frozenset({"PIXI_PROJECT_ROOT", "PIXI_ENVIRONMENT_NAME", "CONDA_PREFIX"})
    outer = {
        "PIXI_PROJECT_ROOT": "/checkout",
        "PIXI_ENVIRONMENT_NAME": "py313",
        "CONDA_PREFIX": "/checkout/.pixi/envs/py313",
        "PIXI_CACHE_DIR": "/cache/pixi",
        "CONDA_PKGS_DIRS": "/cache/pkgs",
        "PATH": "/usr/bin",
    }
    assert rehearse.scrubbed(outer, "v0.6.0-rc1", activated) == {
        "PIXI_CACHE_DIR": "/cache/pixi",
        "CONDA_PKGS_DIRS": "/cache/pkgs",
        "PATH": "/usr/bin",
        "RELEASE_TAG": "v0.6.0-rc1",
    }
