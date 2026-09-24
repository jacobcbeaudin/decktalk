"""The wheel's file list: it holds exactly the files the package means to ship, and nothing rides along.

`uv build` reads the working tree, so a Finder metadata file or a stale cache next to the template
would ship in a wheel cut from a laptop. This builds the wheel and compares its contents with the
files git tracks under src/decktalk. It needs uv and git on PATH and a checkout to run in, and
skips otherwise.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from decktalk.scaffold import SKILL_NAMES
from support.paths import REPO

ROOT = REPO
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
# Every kind of file the package ships. A new kind is a deliberate change to this set.
SHIPPED_SUFFIXES = {".py", ".typed", ".js", ".css", ".woff2", ".html", ".json", ".md", ".toml", ".txt", ".webp"}
SHIPPED_BARE_NAMES = {"LICENSE", "gitignore", "env.example"}


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout


@pytest.fixture(scope="module")
def entries() -> list[str]:
    """The file entries of a freshly built wheel, directories left out."""
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    out = ROOT / "tests" / "out" / "wheel"
    shutil.rmtree(out, ignore_errors=True)
    _run(uv, "build", "--wheel", "--out-dir", str(out))
    with zipfile.ZipFile(next(out.glob("*.whl"))) as wheel:
        return [n for n in wheel.namelist() if not n.endswith("/")]


@pytest.fixture(scope="module")
def tracked() -> set[str]:
    """The files git tracks under src/decktalk, as the wheel names them."""
    if shutil.which("git") is None or subprocess.run(["git", "rev-parse"], cwd=ROOT, capture_output=True).returncode:
        pytest.skip("not a git checkout")
    return {p.removeprefix("src/") for p in _run("git", "ls-files", "-z", "src/decktalk").split("\0") if p}


def test_the_wheel_holds_exactly_the_tracked_package_files(entries, tracked):
    package = {n for n in entries if n.startswith("decktalk/")}
    assert package == tracked, {"only in wheel": sorted(package - tracked), "only in git": sorted(tracked - package)}


def test_the_wheel_metadata_is_the_expected_set(entries):
    meta = {m.group(1) for n in entries if (m := DIST_INFO.match(n))}
    assert meta == METADATA_FILES
    assert all(n.startswith("decktalk/") or DIST_INFO.match(n) for n in entries), entries


def test_nothing_stray_rides_along(entries):
    for name in entries:
        parts = name.split("/")
        assert not STRAY_NAMES & set(parts), name
        assert not name.endswith(STRAY_SUFFIXES), name
        assert not parts[-1].startswith(".env"), name
        if name.startswith("decktalk/"):
            assert parts[-1] in SHIPPED_BARE_NAMES or Path(name).suffix in SHIPPED_SUFFIXES, name


def test_the_package_ships_what_the_scaffold_and_the_stages_need(entries):
    """The runtime, the KaTeX release, the template and the type marker are in every wheel."""
    package = set(entries)
    assert "decktalk/py.typed" in package
    assert "decktalk/runtime/decktalk-runtime.js" in package
    assert "decktalk/runtime/decktalk-probe.js" in package
    assert {"decktalk/katex/katex.min.js", "decktalk/katex/katex.min.css", "decktalk/katex/LICENSE"} <= package
    assert len([n for n in package if n.startswith("decktalk/katex/fonts/")]) == 20
    assert {"decktalk/template/starter/deck/index.html", "decktalk/template/starter/decktalk.toml"} <= package
    assert "decktalk/template/examples/lesson/deck/lesson.html" in package
    assert "decktalk/template/AGENTS.md" in package
    assert {f"decktalk/skills/{name}/SKILL.md" for name in SKILL_NAMES} <= package
