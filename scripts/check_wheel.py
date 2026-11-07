"""Open the built wheel on a machine that has only the wheel, and hold the tag to the version in it.

    pixi run python scripts/check_wheel.py                          # the smoke, with no tag to judge
    RELEASE_TAG=v0.5.0-rc1 pixi run python scripts/check_wheel.py   # the smoke and the tag check

`tests/contract/test_wheel.py` reads what is inside the wheel. This reads what the wheel does, which
is the other half of the same question and the half a file list cannot answer: a wheel whose entry
point is wrong, whose packaged data is unreadable from a site-packages layout or whose dependencies
are understated opens fine in a checkout and fails on the first machine that installs it. So the
smoke installs the wheel into a new virtual environment with nothing else in it, runs
`decktalk init`, and asserts a project appeared. pip resolves the wheel's dependencies from PyPI
there, as a user's installer does, so an understated dependency fails here.

The tag check is a version comparison and never a string comparison. A git tag is semver and a wheel
is PEP 440, so `v0.5.0-rc1` and `0.5.0rc1` are one version spelled two ways, and comparing the text
would refuse every release candidate. The tag is read from `RELEASE_TAG`, which the release workflow
and the rehearsal set around `check.py --group wheel`, so the release runs the wheel row as a pull
request does. When no tag is named the check says out loud that it judged nothing rather than
passing quietly.

This script takes no mode, unlike every generator in the check table, because it writes no file and
has one thing to do. It reads what `python -m build` already left in `dist/` and builds nothing of its own, so that
what is judged here is the artifact that would go to PyPI.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
MANIFEST = ROOT / "pyproject.toml"

TAG = "RELEASE_TAG"
"""The variable that names the tag a release would cut, which the wheel's version is held to."""

SCRIPTS = "Scripts" if os.name == "nt" else "bin"
"""Where a virtual environment keeps its programs, which Windows names differently from every other platform."""

SMOKE_PROJECT = "wheelcheck"
"""What the smoke names the project it writes, which is the one name it has to read back."""

WRITTEN_BY_INIT = ("decktalk.toml", "script.md", "cues.json")
"""The files every new project holds, so a wheel that ships no template fails here rather than later."""


def wheel() -> Path:
    """The one wheel `python -m build` left behind, because a release publishes one artifact and not a choice."""
    built = sorted(DIST.glob("decktalk-*.whl")) if DIST.is_dir() else []
    if not built:
        raise SystemExit(
            f"no wheel in {DIST.relative_to(ROOT)}. Run `python -m build --no-isolation` first, as the wheel row does."
        )
    if len(built) > 1:
        names = ", ".join(path.name for path in built)
        raise SystemExit(
            f"{DIST.relative_to(ROOT)} holds more than one wheel, so it is unclear which would ship: {names}"
        )
    return built[0]


def packaged_version() -> Version:
    """The version this checkout builds, read from the project rather than from the wheel's file name.

    `Version` reads the semver spelling release-please writes, `0.6.0-rc1`, as the PEP 440 version
    the wheel carries, `0.6.0rc1`.
    """
    with MANIFEST.open("rb") as manifest:
        return Version(tomllib.load(manifest)["project"]["version"])


def smoke(built: Path) -> int:
    """Install the wheel where nothing else is installed, write a project with it, and read it back."""
    with tempfile.TemporaryDirectory() as workspace:
        target = Path(workspace) / SMOKE_PROJECT
        venv = Path(workspace) / "venv"
        programs = venv / SCRIPTS
        steps = (
            ("python -m venv", (sys.executable, "-m", "venv", str(venv))),
            (
                "pip install",
                (str(programs / "python"), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", built),
            ),
            ("decktalk init", (str(programs / "decktalk"), "init", str(target))),
        )
        for name, command in steps:
            run = subprocess.run(command, cwd=workspace, check=False, capture_output=True, text=True)
            if run.returncode:
                print(f"the wheel could not write a project: `{name}` exited {run.returncode}")
                # Both streams, because a refusal goes to stderr and anything printed before it to stdout.
                print(f"{run.stdout}{run.stderr}".strip())
                return 1
        missing = [name for name in WRITTEN_BY_INIT if not (target / name).is_file()]
        if missing:
            print(f"the wheel wrote a project with no {', '.join(missing)}, so its packaged template is incomplete.")
            return 1
    print(f"{built.name} installs on its own and writes a project.")
    return 0


def tag_matches(named: str | None) -> int:
    """Hold the tag and the packaged version to the same version, whichever way each one spells it."""
    if not (tag := named):
        print("no tag was named, so the tag was not judged.")
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
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    # Both run whatever the other did, because a red smoke that hid a wrong tag would cost a second
    # run of the release to learn the second fact.
    failed = smoke(wheel())
    return failed | tag_matches(os.environ.get(TAG))


if __name__ == "__main__":
    sys.exit(main())
