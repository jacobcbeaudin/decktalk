"""The wheel's file list: it holds exactly the files the package means to ship, and nothing rides along.

`uv build` reads the working tree, so a Finder metadata file or a stale cache next to the template
would ship in a wheel cut from a laptop. This builds the wheel and compares its contents with the
files git tracks under `src/decktalk`, minus the patterns `pyproject.toml` excludes, which is read
from that file rather than repeated here so the exclusion is written down once. It needs uv and git
on PATH and a checkout to run in, and skips otherwise.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

from decktalk.template import SKILL_NAMES
from support.paths import REPO

DIST_INFO = re.compile(r"^decktalk-[^/]+\.dist-info/(.+)$")
METADATA_FILES = {
    "METADATA",
    "RECORD",
    "WHEEL",
    "entry_points.txt",
    "licenses/LICENSE",
    "licenses/NOTICE",
    "licenses/THIRD_PARTY_NOTICES.md",
}
STRAY_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "__pycache__", ".env"}
STRAY_SUFFIXES = (".pyc", ".pyo", ".orig", ".rej", ".swp", ".bak", ".tmp", ".log", ".mp4", ".mp3", ".wav", ".webm")

SHIPPED_SUFFIXES = {".py", ".typed", ".js", ".css", ".woff2", ".html", ".json", ".md", ".toml", ".txt", ".webp"}
"""Every kind of file the package ships. A new kind is a deliberate change to this set."""

SHIPPED_BARE_NAMES = {"LICENSE", "gitignore", "env.example"}
"""The three files that ship without a suffix, each of which a project copies under another name."""

KATEX_FONT_FILES = 20
"""How many font files the pinned KaTeX release carries, which a partial copy would not."""


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=REPO, check=True, capture_output=True, text=True).stdout


def excluded() -> tuple[str, ...]:
    """The wheel's own exclude patterns, read from `pyproject.toml` so they are written down once."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return tuple(config["tool"]["uv"]["build-backend"]["wheel-exclude"])


@pytest.fixture(scope="module")
def entries() -> list[str]:
    """The file entries of a freshly built wheel, directories left out."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    out = REPO / "tests" / "out" / "wheel"
    shutil.rmtree(out, ignore_errors=True)
    _run(uv, "build", "--wheel", "--out-dir", str(out))
    with zipfile.ZipFile(next(out.glob("*.whl"))) as wheel:
        return [name for name in wheel.namelist() if not name.endswith("/")]


@pytest.fixture(scope="module")
def tracked() -> set[str]:
    """The files git tracks under `src/decktalk` that the wheel does not exclude, as the wheel names them."""
    if (
        shutil.which("git") is None
        or subprocess.run(["git", "rev-parse"], cwd=REPO, capture_output=True, check=False).returncode
    ):
        pytest.skip("not a git checkout")
    patterns = excluded()
    names = {p.removeprefix("src/") for p in _run("git", "ls-files", "-z", "src/decktalk").split("\0") if p}
    return {name for name in names if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns)}


def test_the_wheel_holds_exactly_the_tracked_package_files(entries, tracked):
    package = {name for name in entries if name.startswith("decktalk/")}
    assert package == tracked, {"only in wheel": sorted(package - tracked), "only in git": sorted(tracked - package)}


def test_the_wheel_metadata_is_the_expected_set(entries):
    meta = {match.group(1) for name in entries if (match := DIST_INFO.match(name))}
    assert meta == METADATA_FILES
    assert all(name.startswith("decktalk/") or DIST_INFO.match(name) for name in entries), entries


def test_nothing_stray_rides_along(entries):
    for name in entries:
        parts = name.split("/")
        assert not STRAY_NAMES & set(parts), name
        assert not name.endswith(STRAY_SUFFIXES), name
        assert not parts[-1].startswith(".env"), name
        if name.startswith("decktalk/"):
            assert parts[-1] in SHIPPED_BARE_NAMES or Path(name).suffix in SHIPPED_SUFFIXES, name


def test_no_typescript_source_rides_into_the_wheel(entries):
    """The runtime ships as its two compiled bundles, and its source is a build input nobody installs."""
    riders = [name for name in entries if name.endswith(".ts") or name.endswith("tsconfig.json")]
    assert riders == [], riders


def test_the_package_ships_what_the_template_and_the_stages_need(entries):
    """The runtime, the contract, the KaTeX release, the template and the type marker are in every wheel."""
    package = set(entries)
    assert "decktalk/py.typed" in package
    assert "decktalk/runtime/decktalk-runtime.js" in package
    assert "decktalk/runtime/decktalk-probe.js" in package
    assert "decktalk/runtime/contract.json" in package
    assert {"decktalk/katex/katex.min.js", "decktalk/katex/katex.min.css", "decktalk/katex/LICENSE"} <= package
    assert len([name for name in package if name.startswith("decktalk/katex/fonts/")]) == KATEX_FONT_FILES
    assert {"decktalk/template/starter/deck/index.html", "decktalk/template/starter/decktalk.toml"} <= package
    assert "decktalk/template/examples/lesson/deck/lesson.html" in package
    assert "decktalk/template/AGENTS.md" in package
    assert {f"decktalk/skills/{name}/SKILL.md" for name in SKILL_NAMES} <= package
