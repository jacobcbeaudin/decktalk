"""renovate.json5, held to the pins it promises to move and the ones it promises to leave alone.

The config is parsed as Renovate parses it, and each custom manager's patterns are run over the
files Renovate would give it. So a pin that changes shape, or a manager that stops matching, fails
here rather than on the first weekly run that opens no pull request. The rules are judged by what
they select, not by how they are written.
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from typing import Any

import json5
import yaml

import check
from support.paths import REPO

FROZEN_REV = re.compile(r"^\s*rev:\s*[a-f0-9]{40}\s+#\s*frozen:\s*\S+$")
"""The one SHA form Renovate's pre-commit manager reads, from `lib/modules/manager/pre-commit/extract.ts`."""


@cache
def config() -> dict[str, Any]:
    return json5.loads((REPO / "renovate.json5").read_text(encoding="utf-8"))


def python_pattern(pattern: str) -> re.Pattern[str]:
    """A Renovate pattern as Python reads it: JavaScript names a group `(?<name>`, Python `(?P<name>`."""
    return re.compile(re.sub(r"\(\?<(?=[A-Za-z])", "(?P<", pattern))


def extracted(description: str) -> list[dict[str, str]]:
    """What the custom manager with this description finds, one dict per match, in every file it reads."""
    (manager,) = (m for m in config()["customManagers"] if m["description"] == description)
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    paths = [p for p in tracked.splitlines() for f in manager["managerFilePatterns"] if re.search(f.strip("/"), p)]
    found = []
    for path in paths:
        text = (REPO / path).read_text(encoding="utf-8")
        for pattern in manager["matchStrings"]:
            found += [match.groupdict() for match in python_pattern(pattern).finditer(text)]
    return found


def rules_for(**dependency: str) -> list[dict[str, Any]]:
    """The package rules that select a dependency with these fields (manager, datasource, packageName)."""
    keys = {"manager": "matchManagers", "datasource": "matchDatasources", "packageName": "matchPackageNames"}
    return [
        rule
        for rule in config()["packageRules"]
        if all(dependency.get(field) in rule[key] for field, key in keys.items() if key in rule)
        and not rule.get("matchUpdateTypes")
    ]


def group_of(**dependency: str) -> str:
    """The group a dependency lands in: the last matching rule that names one, as Renovate applies them."""
    return [rule["groupName"] for rule in rules_for(**dependency) if "groupName" in rule][-1]


def test_the_pixi_manager_reads_the_one_home_of_the_pixi_version() -> None:
    assert [found["currentValue"] for found in extracted("requires-pixi in pyproject.toml")] == [check.pixi_version()]


def test_the_images_manager_finds_every_image_by_tag_and_digest() -> None:
    images = {check.DEBIAN, check.UBUNTU, check.FEDORA, check.ALPINE, check.PIXI_IMAGE}
    found = extracted("Image constants in scripts/check.py")
    assert {check.Image(f["depName"], f["currentValue"], f["currentDigest"]) for f in found} == images


def test_the_pixi_image_moves_only_with_the_generator() -> None:
    """build_pixi_pins.py writes the Pixi image, so a digest move from Renovate alone would be undone by it."""
    rules = rules_for(manager="custom.regex", datasource="docker", packageName=check.PIXI_IMAGE.name)
    assert any(rule.get("enabled") is False for rule in rules)
    distribution = rules_for(manager="custom.regex", datasource="docker", packageName=check.DEBIAN.name)
    assert not any(rule.get("enabled") is False for rule in distribution)


def test_a_pixi_pull_request_names_the_generator_it_needs() -> None:
    (notes,) = [rule["prBodyNotes"] for rule in rules_for(datasource="github-releases", packageName="prefix-dev/pixi")]
    assert "generated" in check.BY_NAME
    assert any("pixi run check --group generated --write" in note for note in notes)


def test_the_pixi_a_job_hands_the_setup_action_is_not_a_second_pixi_pin() -> None:
    """The github-actions manager reads `v${{ inputs.pixi-version }}` in the setup action, which no release matches."""
    action = REPO / ".github" / "actions" / "setup" / "action.yml"
    [step] = yaml.safe_load(action.read_text(encoding="utf-8"))["runs"]["steps"]
    assert step["with"]["pixi-version"] == "v${{ inputs.pixi-version }}"
    read = rules_for(manager="github-actions", datasource="github-releases", packageName="prefix-dev/pixi")
    assert any(rule.get("enabled") is False for rule in read)
    pin = rules_for(manager="custom.regex", datasource="github-releases", packageName="prefix-dev/pixi")
    assert not any(rule.get("enabled") is False for rule in pin)


def test_renovate_moves_the_playwright_client_and_never_the_generated_driver_pin() -> None:
    """The driver pin is written from the client range by scripts/build_driver_pin.py, so one home moves."""
    assert group_of(manager="pep621", datasource="pypi", packageName="playwright") == "playwright"
    descriptions = [m.get("description", "") for m in config()["customManagers"]]
    assert not any("driver" in d.lower() for d in descriptions), descriptions
    driver_line = check.manifest()["tool"]["pixi"]["package"]["run-dependencies"]["playwright"]
    for manager in config()["customManagers"]:
        for pattern in manager["matchStrings"]:
            assert driver_line not in [
                m.group("currentValue")
                for m in python_pattern(pattern).finditer((REPO / "pyproject.toml").read_text(encoding="utf-8"))
                if "currentValue" in m.groupdict()
            ]


def test_the_ffmpeg_pin_is_read_and_waits_for_a_person() -> None:
    assert [found["currentValue"] for found in extracted("FFMPEG_VERSION in the ffmpeg fetch")] == [check.ffmpeg_pin()]
    rules = rules_for(manager="custom.regex", datasource="github-tags", packageName="FFmpeg/FFmpeg")
    assert any(rule.get("dependencyDashboardApproval") for rule in rules)


def test_every_fetched_hook_is_pinned_in_the_form_renovate_moves() -> None:
    hooks = yaml.safe_load((REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    fetched = [repo for repo in hooks["repos"] if "rev" in repo]
    assert fetched and config()["pre-commit"]["enabled"] is True
    lines = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8").splitlines()
    revs = [line for line in lines if line.lstrip().startswith("rev:")]
    assert len(revs) == len(fetched)
    assert all(FROZEN_REV.match(line) for line in revs), revs


def test_release_please_never_moves_apart_from_its_action() -> None:
    """scripts/next_version.mjs runs release-please's own code, so it moves by hand with the action that bundles it."""
    rules = rules_for(manager="npm", datasource="npm", packageName="release-please")
    assert any(rule.get("enabled") is False for rule in rules)


def test_nothing_merges_without_a_person() -> None:
    assert not config().get("automerge")
    assert not any(rule.get("automerge") for rule in config()["packageRules"])
