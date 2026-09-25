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

from decktalk.cli import catalog
from decktalk.cli.app import docs_for
from decktalk.explain import explain
from decktalk.settings import KEYS
from support.paths import REPO

ROOT = REPO
INSTALLER = ROOT / "install.sh"


def slugify(heading: str) -> str:
    """A heading as the site anchors it, which is its words lowercased and joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


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


def test_every_docs_link_a_command_prints_reaches_its_own_heading() -> None:
    """Every command's help closes with the reference page and the anchor of its own section, and
    nothing held the two halves together: the anchors named the bare command where the page heads
    each section with the whole command line, so all of them landed at the top of the page."""
    page = (ROOT / "docs" / "reference" / "cli.mdx").read_text(encoding="utf-8")
    headings = {slugify(text) for text in re.findall(r"^#{1,6}\s+(.+?)\s*$", page, re.MULTILINE)}
    rows = catalog.walk()
    assert rows, "the parser offers no command, so this test says nothing"
    missing = [row["command"] for row in rows if docs_for(*row["command"].split()).split("#")[1] not in headings]
    assert not missing, f"the help sends a reader to an anchor the reference page has not got: {missing}"


def test_every_settings_key_sends_a_reader_to_the_table_that_holds_it() -> None:
    """`config explain KEY` publishes a URL per key, and the reference has one page with a heading per
    table. The URL named a page per key, so every one of them was a link into nothing."""
    page = (ROOT / "docs" / "reference" / "configuration.mdx").read_text(encoding="utf-8")
    headings = {slugify(text) for text in re.findall(r"^#{1,6}\s+(.+?)\s*$", page, re.MULTILINE)}
    assert KEYS, "the settings tree publishes no key, so this test says nothing"
    missing = sorted(key.id for key in KEYS if explain(key.id).docs.split("#")[-1] not in headings)
    assert not missing, f"explained with an anchor the configuration page has not got: {missing}"
