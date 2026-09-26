"""The bump `scripts/rehearse_release.py` makes, which has to be the bump release-please makes.

The rehearsal proves the release path on every pull request only while its bump reaches every file
the real one reaches. The whole rehearsal runs as its own row in `scripts/check.py`, so nothing
here regenerates anything. These tests hold the bump itself: each updater on the shape of the
file it edits, the config read rather than restated, and a refusal wherever the rehearsal cannot
follow the config, because a rehearsal that silently skipped a file would pass for the wrong reason.
"""

from __future__ import annotations

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


def test_the_generic_updater_bumps_a_marked_line_and_nothing_else() -> None:
    text = 'const VERSION = "0.4.1"; // x-release-please-version\nconst OTHER = "0.4.1";\n'
    assert rehearse.bump_generic(text, "0.4.2-rc1") == (
        'const VERSION = "0.4.2-rc1"; // x-release-please-version\nconst OTHER = "0.4.1";\n'
    )


def test_the_generic_updater_bumps_every_version_in_a_marked_block() -> None:
    text = "# x-release-please-start-version\na = 0.4.1\nb = 0.4.1\n# x-release-please-end\nc = 0.4.1\n"
    bumped = rehearse.bump_generic(text, "9.9.9")
    assert bumped.count("9.9.9") == 2
    assert bumped.endswith("c = 0.4.1\n")


def test_the_toml_updater_follows_the_lockfile_jsonpath_to_one_package() -> None:
    bumped = rehearse.bump_toml(LOCK, "$.package[?(@.name.value=='decktalk')].version", "0.4.2-rc1")
    assert 'name = "decktalk"\nversion = "0.4.2-rc1"' in bumped
    assert 'name = "click"\nversion = "8.1.0"' in bumped
    assert bumped.replace("0.4.2-rc1", "0.4.1") == LOCK


def test_the_toml_updater_refuses_a_jsonpath_it_cannot_follow() -> None:
    with pytest.raises(rehearse.Refused, match="jsonpath"):
        rehearse.bump_toml(LOCK, "$..version", "1.0.0")


def test_the_json_updater_sets_the_field_the_jsonpath_names() -> None:
    bumped = json.loads(rehearse.bump_json('{"meta": {"version": "0.4.1"}}', "$.meta.version", "0.4.2-rc1"))
    assert bumped == {"meta": {"version": "0.4.2-rc1"}}


def test_an_extra_file_of_a_type_the_rehearsal_does_not_know_is_refused() -> None:
    with pytest.raises(rehearse.Refused, match="yaml"):
        rehearse.bump_extra_file(
            "version: 0.4.1\n", {"type": "yaml", "path": "a.yml", "jsonpath": "$.version"}, "1.0.0"
        )


def test_the_pyproject_bump_touches_the_project_version_alone() -> None:
    text = '[project]\nname = "decktalk"\nversion = "0.4.1"\n\n[tool.other]\nversion = "3.0.0"\n'
    bumped = rehearse.bump_pyproject(text, "0.4.2-rc1")
    assert 'version = "0.4.2-rc1"' in bumped
    assert 'version = "3.0.0"' in bumped


def test_the_changelog_entry_is_a_release_the_changelog_generator_reads() -> None:
    text = "# Changelog\n\n## [0.5.0-rc2](https://github.com/o/r/compare/v0.5.0-rc1...v0.5.0-rc2) (2026-01-01)\n\n* a\n"
    bumped = rehearse.bump_changelog(text, NOTES)
    first = next(line for line in bumped.splitlines() if line.startswith("## "))
    assert first == "## [0.5.0-rc3](https://github.com/o/r/compare/v0.5.0-rc2...v0.5.0-rc3) (2026-01-02)"
    heading = changelog.RELEASE_RE.match(first)
    assert heading is not None and heading["base"] == "0.5.0" and heading["suffix"] == "-rc3"
    # The generator folds a version's candidates into one entry, so the new candidate joins its series.
    assert [release.base for release in changelog.parse(bumped)] == ["0.5.0"]


def test_a_file_the_bump_leaves_alone_is_refused(tmp_path: Path) -> None:
    (tmp_path / "index.ts").write_text('const VERSION = "0.4.1";\n', encoding="utf-8")
    with pytest.raises(rehearse.Refused, match="changed nothing in index.ts"):
        rehearse.rewrite(tmp_path, "index.ts", lambda text: rehearse.bump_generic(text, "0.4.2-rc1"))


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
    current = manifest["."]
    expected = "9.9.9-rc1"
    reports = {".": report(tree=current, released=current, next=None, rehearse=expected)}
    assert rehearse.bump(release_files, reports) == {".": expected}
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
