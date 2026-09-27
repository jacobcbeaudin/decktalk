# /// script
# requires-python = ">=3.12"
# ///
"""Rehearse the version bump release-please makes, then prove every generated file survives it.

    uv run scripts/rehearse_release.py    # bump a scratch copy of this checkout, regenerate, check

The release path runs for real only on release-please's own pull request, so a generator that cannot
write, or a file the bump makes stale that nothing regenerates, used to surface on the release and
nowhere earlier. This script makes the same bump on every pull request instead. It copies the
checkout into a temporary directory, so the checkout itself is never touched and nothing is ever
committed or pushed. In the copy it writes the version release-please would propose next everywhere
release-please would write one, runs `uv run scripts/check.py --group generated --write`, and then
runs `uv run scripts/check.py --group generated`. It fails when a file cannot take the version,
when a generator cannot write, or when anything is still stale afterwards.

The version comes from `node scripts/next_version.mjs`, which runs release-please's own code over
the history since the last release tag. When nothing releasable has landed it is the version one fix
would bring, so every pull request rehearses a real bump. Before any bump the version is held to the
rules of the candidate cycle, and the rehearsal refuses three things.

- A final version that no `Release-As` footer named, because a final release is a person's decision.
- A candidate with no number, such as `0.6.0-rc`, which a `prerelease-type` without one produces.
- A `Release-As` footer release-please never reads, because its commit touched only excluded paths.

On release-please's own pull request the tree already carries the version it proposes. The rehearsal
then checks that version against the same rules and bumps nothing, because the regenerate job in
ci.yml writes that branch for real.

The bump itself is `node scripts/next_version.mjs --bump`, which runs release-please's own updaters
over every file its config names, so a new extra file is rehearsed on the pull request that adds it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

GENERATED = ("uv", "run", "scripts/check.py", "--group", "generated")
NEXT_VERSION = ("node", "scripts/next_version.mjs")
PRERELEASE_NUMBER = re.compile(r"\d")
"""A candidate's prerelease part carries a number, so the series counts rc1, rc2 rather than rc, rc.1."""


class Refused(Exception):
    """A file release-please would bump and this rehearsal cannot, which is itself a failed rehearsal."""


def next_versions(root: Path) -> dict[str, dict[str, Any]]:
    """What release-please would propose for each package, as scripts/next_version.mjs reports it."""
    found = subprocess.run(NEXT_VERSION, cwd=root, capture_output=True, text=True, check=False)
    if found.returncode != 0:
        raise Refused(f"the next version cannot be computed: {found.stderr.strip()}")
    return json.loads(found.stdout)


def judged(path: str, report: dict[str, Any]) -> str | None:
    """The version to rehearse for one package, or None on release-please's own pull request.

    Raises Refused when the version breaks a rule of the candidate cycle.
    """
    for dropped in report["dropped"]:
        raise Refused(
            f"the Release-As: {dropped['version']} footer in {dropped['sha'][:7]} touches only excluded paths, "
            "so release-please never reads it. Put the footer on a commit that changes an included file."
        )
    unreleased = report["tree"] != report["released"]
    version = report["next"] if unreleased else report["rehearse"]
    if unreleased and version != report["tree"]:
        raise Refused(
            f"{path} carries {report['tree']}, and release-please's rules give {version} for the commits since "
            f"v{report['released']}, so the release pull request and this rehearsal disagree"
        )
    if "-" not in version and report["named"] != version:
        raise Refused(
            f"release-please would release {version} as a final version, and no Release-As footer named it. "
            "A final release is named by a person with a Release-As footer."
        )
    if "-" in version and not PRERELEASE_NUMBER.search(version.split("-", 1)[1]):
        raise Refused(f"{version} is a candidate with no number, so set a numbered prerelease-type such as rc1")
    return None if unreleased else version


def bump(tree: Path, reports: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Make in `tree` every edit release-please makes for a release, and return each package's new version.

    The edits are made by release-please's own updaters, through `next_version.mjs --bump`, so the
    rehearsal makes the bump release-please makes rather than a model of it. A package on
    release-please's own pull request is judged and left as it is.
    """
    bumped: dict[str, str] = {}
    for name, report in reports.items():
        version = judged(name, report)
        if version is None:
            print(f"{name} carries the unreleased {report['tree']}, which the regenerate job writes")
            continue
        bumped[name] = version
    if bumped:
        done = subprocess.run(
            (*NEXT_VERSION, "--bump", str(tree), *bumped), cwd=ROOT, capture_output=True, text=True, check=False
        )
        if done.returncode != 0:
            raise Refused(done.stderr.strip())
        print(done.stdout, end="")
    return bumped


def copy_checkout(source: Path, target: Path) -> None:
    """Every file git would see in `source`, committed or not, copied to `target` with nothing ignored."""
    listed = subprocess.run(
        ("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        cwd=source,
        capture_output=True,
        check=True,
    ).stdout.decode()
    for relative in filter(None, listed.split("\0")):
        origin = source / relative
        if not origin.exists():
            continue
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination, follow_symlinks=False)


def run(command: tuple[str, ...], tree: Path) -> None:
    """Run one command in the copy, and stop the rehearsal on the first one that fails."""
    print(f"\n$ {' '.join(command)}", flush=True)
    # The copy is its own project, so the environment this script runs in must not leak into it.
    env = {key: value for key, value in os.environ.items() if key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    if subprocess.call(command, cwd=tree, env=env) != 0:
        raise SystemExit(f"the rehearsal failed at: {' '.join(command)}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="decktalk-rehearsal-") as scratch:
        tree = Path(scratch)
        copy_checkout(ROOT, tree)
        print(f"rehearsing in a copy of {ROOT} at {tree}")
        try:
            versions = bump(tree, next_versions(ROOT))
        except Refused as refusal:
            raise SystemExit(f"the rehearsal refuses the release: {refusal}") from None
        if not versions:
            print("\nthe release pull request's version keeps the rules of the candidate cycle")
            return 0
        # uv writes the lockfile's own spelling of the version, which is what `uv run` would
        # otherwise do unasked in the first generator it starts.
        run(("uv", "lock"), tree)
        run((*GENERATED, "--write"), tree)
        run(GENERATED, tree)
    print(f"\nthe release path is green at {', '.join(versions.values())}: every generator wrote, nothing is stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
