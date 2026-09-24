# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging>=24"]
# ///
"""Open the built wheel on a machine that has only the wheel, and hold the tag to the version in it.

    uv run scripts/check_wheel.py --check           # the smoke and the tag check
    uv run scripts/check_wheel.py --check --tag v0.5.0-rc1

`tests/contract/test_wheel.py` reads what is inside the wheel. This reads what the wheel does, which
is the other half of the same question and the half a file list cannot answer: a wheel whose entry
point is wrong, whose packaged data is unreadable from a site-packages layout or whose dependencies
are understated opens fine in a checkout and fails on the first machine that installs it. So the
smoke installs the wheel into an environment with no project and nothing else in it, runs
`decktalk init`, and asserts a project appeared.

The tag check is a version comparison and never a string comparison. A git tag is semver and a wheel
is PEP 440, so `v0.5.0-rc1` and `0.5.0rc1` are one version spelled two ways, and comparing the text
would refuse every release candidate the founder cuts. The tag is read from `--tag`, or from
`GITHUB_REF_NAME` when a workflow supplies it, and when neither names one the check says out loud
that it judged nothing rather than passing quietly.

This script has no `--write`, unlike every generator in the check table, because it writes no file.
It reads what `uv build` already left in `dist/` and builds nothing of its own, so that what is
judged here is the artifact that would go to PyPI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

SMOKE_PROJECT = "wheelcheck"
"""What the smoke names the project it writes, which is the one name it has to read back."""

WRITTEN_BY_INIT = ("decktalk.toml", "script.md", "cues.json")
"""The files every new project holds, so a wheel that ships no template fails here rather than later."""

TAG_VARIABLE = "GITHUB_REF_NAME"
"""Where a workflow puts the tag it is building, which is the only tag this check ever judges."""


def wheel() -> Path:
    """The one wheel `uv build` left behind, because a release publishes one artifact and not a choice."""
    built = sorted(DIST.glob("decktalk-*.whl")) if DIST.is_dir() else []
    if not built:
        raise SystemExit(f"no wheel in {DIST.relative_to(ROOT)}. Run `uv build` first, as the wheel group does.")
    if len(built) > 1:
        names = ", ".join(path.name for path in built)
        raise SystemExit(
            f"{DIST.relative_to(ROOT)} holds more than one wheel, so it is unclear which would ship: {names}"
        )
    return built[0]


def packaged_version() -> Version:
    """The version this checkout builds, read from the project rather than from the wheel's file name."""
    printed = subprocess.run(
        ("uv", "version", "--short"), cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    return Version(printed)


def smoke(built: Path) -> int:
    """Install the wheel where nothing else is installed, write a project with it, and read it back."""
    with tempfile.TemporaryDirectory() as workspace:
        target = Path(workspace) / SMOKE_PROJECT
        run = subprocess.run(
            ("uv", "run", "--isolated", "--no-project", "--with", str(built), "--", "decktalk", "init", str(target)),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if run.returncode:
            print(f"the wheel could not write a project: `decktalk init` exited {run.returncode}")
            print(run.stdout.strip() or run.stderr.strip())
            return 1
        missing = [name for name in WRITTEN_BY_INIT if not (target / name).is_file()]
        if missing:
            print(f"the wheel wrote a project with no {', '.join(missing)}, so its packaged template is incomplete.")
            return 1
    print(f"{built.name} installs on its own and writes a project.")
    return 0


def tag_matches(named: str | None) -> int:
    """Hold the tag and the packaged version to the same version, whichever way each one spells it."""
    tag = named or os.environ.get(TAG_VARIABLE, "")
    if not tag:
        print(f"no tag was named and {TAG_VARIABLE} is unset, so the tag was not judged.")
        return 0
    try:
        wanted = Version(tag.removeprefix("v"))
    except InvalidVersion:
        print(f"{tag} is not a version, so the release would be tagged with something a wheel cannot carry.")
        return 1
    packaged = packaged_version()
    if wanted != packaged:
        print(f"the tag is {wanted} and the package is {packaged}, so the wheel would carry the wrong version.")
        return 1
    print(f"the tag {tag} and the packaged version {packaged} are the same version.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="run the wheel smoke and the tag check")
    parser.add_argument("--tag", help=f"the tag to hold the version to, default the value of {TAG_VARIABLE}")
    args = parser.parse_args()
    # Both run whatever the other did, because a red smoke that hid a wrong tag would cost a second
    # run of the release to learn the second fact.
    failed = smoke(wheel())
    return failed | tag_matches(args.tag)


if __name__ == "__main__":
    sys.exit(main())
