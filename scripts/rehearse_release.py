# /// script
# requires-python = ">=3.12"
# ///
"""Rehearse the version bump release-please makes, then prove every generated file survives it.

    uv run scripts/rehearse_release.py    # bump a scratch copy of this checkout, regenerate, check

The release path runs for real only on release-please's own pull request, so a generator that cannot
write, or a file the bump makes stale that nothing regenerates, used to surface on the release and
nowhere earlier. This script makes the same bump on every pull request instead. It copies the
checkout into a temporary directory, so the checkout itself is never touched and nothing is ever
committed or pushed. In the copy it writes a throwaway prerelease version everywhere
release-please would write one, runs `uv run scripts/check.py --group generated --write`, and then
runs `uv run scripts/check.py --group generated`. It fails when a file cannot take the version,
when a generator cannot write, or when anything is still stale afterwards.

Where the version goes is read from `release-please-config.json` and `.release-please-manifest.json`
rather than listed here. Each package's manifest entry and its changelog are bumped, its release
type decides the project file, and every entry of its `extra-files` is bumped by the updater its
`type` names. A new extra file is therefore rehearsed on the pull request that adds it to the config.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG = "release-please-config.json"
MANIFEST = ".release-please-manifest.json"

REHEARSAL_PRERELEASE = "rc.0"
"""The prerelease the rehearsal bumps to. It is never committed, so it only has to differ from the tree's version."""

SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
"""A version as release-please's generic updater finds one on a marked line."""

LINE_MARKER = "x-release-please-version"
BLOCK_START, BLOCK_END = "x-release-please-start-version", "x-release-please-end"

JSONPATH = re.compile(
    r"^\$\.(?P<table>[\w-]+)"
    r"(?:\[\?\(@\.(?P<key>[\w-]+)(?:\.value)?==['\"](?P<match>[^'\"]+)['\"]\)\])?"
    r"\.(?P<field>[\w-]+)$"
)
"""The jsonpath shapes this rehearsal can follow: a field of a table, or of the array rows one key picks.

release-please parses TOML into `{value, start, end}` nodes, which is why a filter on a TOML key is
written `@.name.value`. The `.value` is accepted and dropped, because here the file is read as text.
"""

GENERATED = ("uv", "run", "scripts/check.py", "--group", "generated")


class Refused(Exception):
    """A file release-please would bump and this rehearsal cannot, which is itself a failed rehearsal."""


def rehearsal_version(current: str) -> str:
    """The next patch version as a prerelease, which differs from every version the tree names."""
    found = re.match(r"(\d+)\.(\d+)\.(\d+)", current)
    if found is None:
        raise Refused(f"the manifest names {current!r}, which is not a version")
    major, minor, patch = (int(part) for part in found.groups())
    return f"{major}.{minor}.{patch + 1}-{REHEARSAL_PRERELEASE}"


def bump_generic(text: str, version: str) -> str:
    """Every version on a marked line or inside a marked block, replaced as release-please's generic updater does."""
    lines, inside = [], False
    for line in text.splitlines(keepends=True):
        if BLOCK_START in line:
            inside = True
        if inside or LINE_MARKER in line:
            line = SEMVER.sub(version, line)
        if BLOCK_END in line:
            inside = False
        lines.append(line)
    return "".join(lines)


def bump_toml(text: str, jsonpath: str, version: str) -> str:
    """The field `jsonpath` names, set to `version` in every table or array row it selects."""
    path = JSONPATH.match(jsonpath)
    if path is None:
        raise Refused(f"the jsonpath {jsonpath!r} is not a shape this rehearsal can follow")
    rows: list[list[str]] = [[]]
    for line in text.splitlines(keepends=True):
        if line.startswith("["):
            rows.append([])
        rows[-1].append(line)
    headers = (f"[{path['table']}]", f"[[{path['table']}]]")
    field = re.compile(rf"^{re.escape(path['field'])}\s*=\s*\"[^\"]*\"", re.MULTILINE)
    picked = None
    if path["key"]:
        picked = re.compile(rf"^{re.escape(path['key'])}\s*=\s*\"{re.escape(path['match'])}\"", re.MULTILINE)
    for row in rows:
        block = "".join(row)
        if not row or row[0].strip() not in headers:
            continue
        if picked is not None and not picked.search(block):
            continue
        row[:] = field.sub(f'{path["field"]} = "{version}"', block, count=1).splitlines(keepends=True)
    return "".join(line for row in rows for line in row)


def bump_json(text: str, jsonpath: str, version: str) -> str:
    """The field `jsonpath` names, set to `version` in the object or array rows it selects."""
    path = JSONPATH.match(jsonpath)
    if path is None:
        raise Refused(f"the jsonpath {jsonpath!r} is not a shape this rehearsal can follow")
    data = json.loads(text)
    found = data[path["table"]]
    for row in found if isinstance(found, list) else [found]:
        if path["key"] is None or row.get(path["key"]) == path["match"]:
            row[path["field"]] = version
    return json.dumps(data, indent=2) + "\n"


def bump_extra_file(text: str, entry: str | dict[str, str], version: str) -> str:
    """One entry of `extra-files`, bumped by the updater its `type` names, as release-please does."""
    if isinstance(entry, str) or entry.get("type", "generic") == "generic":
        return bump_generic(text, version)
    kind = entry["type"]
    if kind == "toml":
        return bump_toml(text, entry["jsonpath"], version)
    if kind == "json":
        return bump_json(text, entry["jsonpath"], version)
    raise Refused(f"{entry['path']} is a {kind} extra file, and this rehearsal bumps generic, toml and json alone")


def bump_pyproject(text: str, version: str) -> str:
    """The `version` line of the `[project]` table, which the python release type bumps."""
    head, marker, rest = text.partition("[project]\n")
    bumped = re.sub(r'^version\s*=\s*"[^"]*"', f'version = "{version}"', rest, count=1, flags=re.MULTILINE)
    return head + marker + bumped


COMPARE = re.compile(r"^## \[[^\]]+\]\((?P<repository>https://[^)]+?)/compare/", re.MULTILINE)
"""The repository a changelog's newest compare link points into, which a new heading links into too."""


def bump_changelog(text: str, previous: str, version: str, today: datetime.date) -> str:
    """The changelog with a one-fix release above its newest one, headed the way release-please heads it."""
    linked = COMPARE.search(text)
    title = f"[{version}]({linked['repository']}/compare/v{previous}...v{version})" if linked else version
    section = f"## {title} ({today.isoformat()})\n\n\n### Bug Fixes\n\n* rehearse the release path\n"
    head, marker, rest = text.partition("\n## ")
    if not marker:
        return text.rstrip("\n") + "\n\n" + section
    return f"{head}\n{section}\n## {rest}"


def rewrite(base: Path, relative: str, bump: Callable[[str], str]) -> None:
    """Rewrite one file with `bump`, refusing a file the bump would leave exactly as it was."""
    path = base / relative
    if not path.exists():
        raise Refused(f"{relative} does not exist, and release-please would bump it")
    before = path.read_text(encoding="utf-8")
    after = bump(before)
    if after == before:
        raise Refused(f"the bump changed nothing in {relative}, so release-please would leave its version behind")
    path.write_text(after, encoding="utf-8")
    print(f"bumped {relative}")


def bump_package(base: Path, package: dict[str, Any], previous: str, version: str, today: datetime.date) -> None:
    """Make in one package every edit release-please makes to it: the project file, the changelog, the extras."""
    rewrite(base, "pyproject.toml", lambda text: bump_pyproject(text, version))
    changelog = package.get("changelog-path", "CHANGELOG.md")
    rewrite(base, changelog, lambda text: bump_changelog(text, previous, version, today))
    for entry in package.get("extra-files", ()):
        relative = entry if isinstance(entry, str) else entry["path"]
        rewrite(base, relative, partial(bump_extra_file, entry=entry, version=version))


def bump(tree: Path, today: datetime.date) -> dict[str, str]:
    """Make in `tree` every edit release-please makes for a release, and return each package's new version."""
    config = json.loads((tree / CONFIG).read_text(encoding="utf-8"))
    manifest = json.loads((tree / MANIFEST).read_text(encoding="utf-8"))
    bumped: dict[str, str] = {}
    for name, package in config["packages"].items():
        release_type = package.get("release-type", config.get("release-type"))
        if release_type != "python":
            raise Refused(f"the package {name} is released as {release_type}, and this rehearsal knows python alone")
        bumped[name] = rehearsal_version(manifest[name])
        bump_package(tree / name, package, manifest[name], bumped[name], today)
    (tree / MANIFEST).write_text(json.dumps(manifest | bumped, indent=2) + "\n", encoding="utf-8")
    print(f"bumped {MANIFEST}")
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
            versions = bump(tree, datetime.date.today())
        except Refused as refusal:
            raise SystemExit(f"the rehearsal cannot bump the version: {refusal}") from None
        # uv writes the lockfile's own spelling of the version, which is what `uv run` would
        # otherwise do unasked in the first generator it starts.
        run(("uv", "lock"), tree)
        run((*GENERATED, "--write"), tree)
        run(GENERATED, tree)
    print(f"\nthe release path is green at {', '.join(versions.values())}: every generator wrote, nothing is stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
