"""Claims in the docs that a reader can act on, held to what the code actually does.

`scripts/check_docs_links.py` already checks that every link resolves and every page is in the
navigation. Nothing checked whether the sentences were true, and two of them were not: the README
said the one-line installer ran on Windows, which `install.sh` refuses by name, and the pinning
example named a version that was never published, so copying it got a resolver error.

Both are the same kind of bug. A sentence is written when something is true, the code moves, and
prose has nothing holding it in place.
"""

from __future__ import annotations

import re

from support.paths import REPO

ROOT = REPO
INSTALLER = ROOT / "site" / "install.sh"


def released_versions() -> set[str]:
    """Every version the changelog lists, which is every version that was released.

    Read from the changelog rather than from `git tag`, because the tags are not there when this
    runs: actions/checkout clones one commit and no tags, so `git tag` came back empty in CI and
    the test failed for a reason that had nothing to do with the docs. The changelog is committed,
    is generated from CHANGELOG.md by release-please, and is present wherever the file it checks is
    present, which is the only property that matters for a source of truth.
    """
    changelog = (ROOT / "docs" / "changelog.mdx").read_text(encoding="utf-8")
    return set(re.findall(r'<Update\s+label="(\d+\.\d+\.\d+)"', changelog))


def test_every_documented_version_pin_is_a_version_that_exists() -> None:
    """`DECKTALK_VERSION=` is shown so a reader can paste it, and a reader who pastes an unreleased
    version gets a resolver failure rather than an install. The docs named 0.4.1 while the newest
    release was 0.4.0, because the number was written from the release that was being prepared."""
    released = released_versions()
    assert released, "the changelog lists no releases, so this test cannot say anything"
    pattern = re.compile(r"DECKTALK_VERSION=(\d+\.\d+\.\d+)")
    seen = []
    for path in [ROOT / "README.md", INSTALLER, *sorted((ROOT / "docs").rglob("*.mdx"))]:
        if path.name == "changelog.mdx":
            continue  # generated from history, where old versions are the point
        for version in pattern.findall(path.read_text(encoding="utf-8")):
            seen.append((path.relative_to(ROOT), version))
    assert seen, "no version pin is documented anywhere, so the example was lost"
    unreleased = [(p, v) for p, v in seen if v not in released]
    assert not unreleased, f"documented as installable but never released: {unreleased}; released: {sorted(released)}"


def test_the_platforms_the_one_liner_claims_are_the_ones_it_accepts() -> None:
    """install.sh refuses Windows by name and tells you to use uv instead, so no page may offer the
    one-liner to Windows. The README did, in the same bullet as the installer's own description."""
    source = INSTALLER.read_text(encoding="utf-8")
    assert "Windows: install with PowerShell instead." in source, (
        "the installer no longer refuses Windows, so this test is asserting the wrong thing"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    bullet = next(line for line in readme.splitlines() if line.startswith("- **Software.**"))
    assert "one-line installer" in bullet, bullet
    head = bullet.split("Installing with", 1)[0].split("On Windows", 1)[0]
    assert "Windows" not in head, f"the one-liner is offered to Windows, which it refuses: {bullet}"
