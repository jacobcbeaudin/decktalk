"""The bump `scripts/rehearse_release.py` makes, which has to be the bump release-please makes.

The rehearsal proves the release path on every pull request only while its bump reaches every file
the real one reaches. The whole rehearsal runs as its own row in `scripts/check.py`, so nothing
here regenerates anything. These tests hold the bump itself: each updater on the shape of the
file it edits, the config read rather than restated, and a refusal wherever the rehearsal cannot
follow the config, because a rehearsal that silently skipped a file would pass for the wrong reason.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

from support.paths import REPO


def _load(name: str) -> ModuleType:
    """A script under `scripts/` as a module, which is the only way to reach a file outside the package."""
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rehearse = _load("rehearse_release")
changelog = _load("build_changelog")

TODAY = datetime.date(2026, 1, 2)

LOCK = """version = 1

[[package]]
name = "click"
version = "8.1.0"

[[package]]
name = "decktalk"
version = "0.4.1"
source = { editable = "." }

[package.optional-dependencies]
dev = []
"""


def test_the_rehearsal_version_is_the_next_patch_as_a_prerelease() -> None:
    assert rehearse.rehearsal_version("0.4.1") == "0.4.2-rc.0"
    assert rehearse.rehearsal_version("0.5.0-rc1") == "0.5.1-rc.0"


def test_a_manifest_that_names_no_version_is_refused() -> None:
    with pytest.raises(rehearse.Refused, match="not a version"):
        rehearse.rehearsal_version("next")


def test_the_generic_updater_bumps_a_marked_line_and_nothing_else() -> None:
    text = 'const VERSION = "0.4.1"; // x-release-please-version\nconst OTHER = "0.4.1";\n'
    assert rehearse.bump_generic(text, "0.4.2-rc.0") == (
        'const VERSION = "0.4.2-rc.0"; // x-release-please-version\nconst OTHER = "0.4.1";\n'
    )


def test_the_generic_updater_bumps_every_version_in_a_marked_block() -> None:
    text = "# x-release-please-start-version\na = 0.4.1\nb = 0.4.1\n# x-release-please-end\nc = 0.4.1\n"
    bumped = rehearse.bump_generic(text, "9.9.9")
    assert bumped.count("9.9.9") == 2
    assert bumped.endswith("c = 0.4.1\n")


def test_the_toml_updater_follows_the_lockfile_jsonpath_to_one_package() -> None:
    bumped = rehearse.bump_toml(LOCK, "$.package[?(@.name.value=='decktalk')].version", "0.4.2-rc.0")
    assert 'name = "decktalk"\nversion = "0.4.2-rc.0"' in bumped
    assert 'name = "click"\nversion = "8.1.0"' in bumped
    assert bumped.replace("0.4.2-rc.0", "0.4.1") == LOCK


def test_the_toml_updater_refuses_a_jsonpath_it_cannot_follow() -> None:
    with pytest.raises(rehearse.Refused, match="jsonpath"):
        rehearse.bump_toml(LOCK, "$..version", "1.0.0")


def test_the_json_updater_sets_the_field_the_jsonpath_names() -> None:
    bumped = json.loads(rehearse.bump_json('{"meta": {"version": "0.4.1"}}', "$.meta.version", "0.4.2-rc.0"))
    assert bumped == {"meta": {"version": "0.4.2-rc.0"}}


def test_an_extra_file_of_a_type_the_rehearsal_does_not_know_is_refused() -> None:
    with pytest.raises(rehearse.Refused, match="yaml"):
        rehearse.bump_extra_file(
            "version: 0.4.1\n", {"type": "yaml", "path": "a.yml", "jsonpath": "$.version"}, "1.0.0"
        )


def test_the_pyproject_bump_touches_the_project_version_alone() -> None:
    text = '[project]\nname = "decktalk"\nversion = "0.4.1"\n\n[tool.other]\nversion = "3.0.0"\n'
    bumped = rehearse.bump_pyproject(text, "0.4.2-rc.0")
    assert 'version = "0.4.2-rc.0"' in bumped
    assert 'version = "3.0.0"' in bumped


def test_the_changelog_section_is_a_release_the_changelog_generator_reads() -> None:
    text = "# Changelog\n\n## [0.4.1](https://github.com/o/r/compare/v0.4.0...v0.4.1) (2026-01-01)\n\n* a\n"
    bumped = rehearse.bump_changelog(text, "0.4.1", "0.4.2-rc.0", TODAY)
    first = next(line for line in bumped.splitlines() if line.startswith("## "))
    assert first == "## [0.4.2-rc.0](https://github.com/o/r/compare/v0.4.1...v0.4.2-rc.0) (2026-01-02)"
    heading = changelog.RELEASE_RE.match(first)
    assert heading is not None and heading["base"] == "0.4.2" and heading["suffix"] == "-rc.0"
    assert [release.base for release in changelog.parse(bumped)] == ["0.4.2", "0.4.1"]


def test_a_file_the_bump_leaves_alone_is_refused(tmp_path: Path) -> None:
    (tmp_path / "index.ts").write_text('const VERSION = "0.4.1";\n', encoding="utf-8")
    with pytest.raises(rehearse.Refused, match="changed nothing in index.ts"):
        rehearse.rewrite(tmp_path, "index.ts", lambda text: rehearse.bump_generic(text, "0.4.2-rc.0"))


def test_a_file_the_config_names_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    with pytest.raises(rehearse.Refused, match="does not exist"):
        rehearse.rewrite(tmp_path, "gone.ts", lambda text: text)


@pytest.fixture
def release_files(tmp_path: Path) -> Path:
    """Every file this repository's release-please config names, copied into a directory of its own."""
    config = json.loads((REPO / rehearse.CONFIG).read_text(encoding="utf-8"))
    named = [rehearse.CONFIG, rehearse.MANIFEST, "pyproject.toml", "CHANGELOG.md"]
    for package in config["packages"].values():
        named += [entry if isinstance(entry, str) else entry["path"] for entry in package.get("extra-files", ())]
    for relative in named:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, tmp_path / relative)
    return tmp_path


def test_the_real_config_bumps_every_file_it_names(release_files: Path) -> None:
    manifest = json.loads((release_files / rehearse.MANIFEST).read_text(encoding="utf-8"))
    expected = rehearse.rehearsal_version(manifest["."])
    assert rehearse.bump(release_files, TODAY) == {".": expected}
    assert json.loads((release_files / rehearse.MANIFEST).read_text(encoding="utf-8")) == {".": expected}
    project = (release_files / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(expected)}"$', project, re.MULTILINE)
    runtime = (release_files / "src/decktalk/runtime/src/index.ts").read_text(encoding="utf-8")
    assert f'"{expected}"; // x-release-please-version' in runtime
    lock = (release_files / "uv.lock").read_text(encoding="utf-8")
    assert f'name = "decktalk"\nversion = "{expected}"' in lock


def test_the_copy_carries_the_checkout_and_no_repository_to_commit_to(tmp_path: Path) -> None:
    rehearse.copy_checkout(REPO, tmp_path)
    assert (tmp_path / rehearse.CONFIG).read_bytes() == (REPO / rehearse.CONFIG).read_bytes()
    assert not (tmp_path / ".git").exists()
