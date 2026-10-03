"""Claims in the docs that a reader can act on, held to what the code actually does.

`scripts/check_docs_links.py` already checks that every link resolves and every page is in the
navigation. Nothing checked whether the sentences were true, and two of them were not: the README
said the one-line installer ran on Windows, which `install.sh` refuses by name, and the pinning
example named a version that was never published, so copying it got a resolver error.

Each test holds a claim by its behaviour rather than by its words, so a page or the installer can be
reworded freely and a test fails only when the claim stops being true.

Both are the same kind of bug. A sentence is written when something is true, the code moves, and
prose has nothing holding it in place.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
from pathlib import Path

import pytest

import decktalk
from decktalk.cli import catalog
from decktalk.cli.app import docs_for
from decktalk.explain import explain
from decktalk.settings import KEYS
from support.installer import fake_path
from support.paths import REPO
from support.projects import write_project
from support.runs import a_machine

INSTALLER = REPO / "install.sh"


def slugify(heading: str) -> str:
    """A heading as the site anchors it, which is its words lowercased and joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def anchors(page: Path) -> set[str]:
    """Every anchor a page's headings give it, which is what a link into the page may end in."""
    return {
        slugify(text) for text in re.findall(r"^#{1,6}\s+(.+?)\s*$", page.read_text(encoding="utf-8"), re.MULTILINE)
    }


def reader_pages() -> list[Path]:
    """The README and every docs page but the changelog, which is generated from history."""
    pages = [REPO / "README.md", *sorted((REPO / "docs").rglob("*.mdx"))]
    return [page for page in pages if page.name != "changelog.mdx"]


def released_versions() -> set[str]:
    """Every version the changelog lists, which is every version that was released.

    Read from the changelog rather than from `git tag`, because the tags are not there when this
    runs: actions/checkout clones one commit and no tags, so `git tag` came back empty in CI and
    the test failed for a reason that had nothing to do with the docs. The changelog is committed,
    is generated from CHANGELOG.md by release-please, and is present wherever the file it checks is
    present, which is the only property that matters for a source of truth.
    """
    changelog = (REPO / "docs" / "changelog.mdx").read_text(encoding="utf-8")
    return set(re.findall(r'<Update\s+label="(\d+\.\d+\.\d+)"', changelog))


def test_every_documented_version_pin_is_a_version_that_exists() -> None:
    """`DECKTALK_VERSION=` is shown so a reader can paste it, and a reader who pastes an unreleased
    version gets a resolver failure rather than an install. The docs named 0.4.1 while the newest
    release was 0.4.0, because the number was written from the release that was being prepared."""
    released = released_versions()
    assert released, "the changelog lists no releases, so this test cannot say anything"
    pattern = re.compile(r"DECKTALK_VERSION=(\d+\.\d+\.\d+)")
    seen = []
    for path in [*reader_pages(), INSTALLER]:
        for version in pattern.findall(path.read_text(encoding="utf-8")):
            seen.append((path.relative_to(REPO), version))
    assert seen, "no version pin is documented anywhere, so the example was lost"
    unreleased = [(p, v) for p, v in seen if v not in released]
    assert not unreleased, f"documented as installable but never released: {unreleased}; released: {sorted(released)}"


OFFERS_THE_INSTALLER = re.compile(r"install\.sh|one-line installer|one-liner", re.IGNORECASE)
"""What a sentence names when it is about the one-line installer, whether as the command or in words."""

REFUSES = re.compile(r"\b(stops|refuses|refused)\b", re.IGNORECASE)
"""What a sentence about the installer says when it tells a Windows reader the installer is not for them."""

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

WINDOWS_UNAMES = ("MINGW64_NT-10.0-26100", "MSYS_NT-10.0", "CYGWIN_NT-10.0", "Windows_NT")
"""What `uname -s` prints under each shell a Windows machine runs POSIX sh in."""


def sentences(path: Path) -> list[str]:
    """Every sentence of a page a reader meets, with a table row read as one sentence."""
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        found += [line] if line.lstrip().startswith("|") else SENTENCE_END.split(line)
    return found


@pytest.mark.skipif(os.name != "posix", reason="install.sh needs a POSIX shell")
@pytest.mark.parametrize("uname", WINDOWS_UNAMES)
def test_the_installer_stops_on_windows_before_it_installs_anything(tmp_path: Path, uname: str) -> None:
    """The claim every page makes about Windows is that the one-liner does not install there."""
    called = tmp_path / "called"
    env = fake_path(tmp_path, called, uname=uname) | {"TMPDIR": str(tmp_path), "NO_COLOR": "1"}
    done = subprocess.run(["/bin/sh", str(INSTALLER)], env=env, capture_output=True, text=True, check=False)
    assert done.returncode != 0, f"install.sh went on under {uname}:\n{done.stdout}{done.stderr}"
    assert not called.exists(), f"install.sh called {called.read_text(encoding='utf-8')} before it stopped"


def test_no_page_offers_the_one_liner_to_windows() -> None:
    """install.sh stops on Windows, so no sentence about it may name Windows unless it says so.

    The README once offered the one-liner to Windows in the same bullet as the installer's own
    description. This holds the claim rather than a spelling of it: a page may say anything about
    the installer in any words, and a sentence that names both it and Windows has to say it stops.
    """
    offered = [
        f"{page.relative_to(REPO)}: {sentence.strip()}"
        for page in reader_pages()
        for sentence in sentences(page)
        if OFFERS_THE_INSTALLER.search(sentence) and "Windows" in sentence and not REFUSES.search(sentence)
    ]
    assert offered == [], "\n".join(offered)


def test_every_docs_link_a_command_prints_reaches_its_own_heading() -> None:
    """Every command's help closes with the reference page and the anchor of its own section, and
    nothing held the two halves together: the anchors named the bare command where the page heads
    each section with the whole command line, so all of them landed at the top of the page."""
    headings = anchors(REPO / "docs" / "reference" / "cli.mdx")
    rows = catalog.walk()
    assert rows, "the parser offers no command, so this test says nothing"
    missing = [row["command"] for row in rows if docs_for(*row["command"].split()).split("#")[1] not in headings]
    assert not missing, f"the help sends a reader to an anchor the reference page has not got: {missing}"


def test_every_settings_key_sends_a_reader_to_the_table_that_holds_it() -> None:
    """`config explain KEY` publishes a URL per key, and the reference has one page with a heading per
    table. The URL named a page per key, so every one of them was a link into nothing."""
    headings = anchors(REPO / "docs" / "reference" / "configuration.mdx")
    assert KEYS, "the settings tree publishes no key, so this test says nothing"
    missing = sorted(key.id for key in KEYS if explain(key.id).docs.split("#")[-1] not in headings)
    assert not missing, f"explained with an anchor the configuration page has not got: {missing}"


def test_every_origin_member_the_python_reference_names_is_one_an_origin_has(tmp_path: Path) -> None:
    """The reference listed `url`, `port` and `run` on the origin after the refactor removed them, and
    its example printed two of them, so a reader who copied it met an `AttributeError`."""
    page = (REPO / "docs" / "reference" / "python-api.mdx").read_text(encoding="utf-8")
    section = page.split("## `serve` and `Origin`")[1].split("\n## ")[0]
    named = set(re.findall(r"^\| `(\w+)(?:\(\))?` \|", section, re.MULTILINE))
    named |= set(re.findall(r"\borigin\.(\w+)", section))
    assert named, "the reference names no member of the origin, so this test says nothing"
    write_project(tmp_path)
    with decktalk.open(tmp_path, machine=a_machine(tmp_path)).serve(port=0) as origin:
        missing = sorted(name for name in named if not hasattr(origin, name))
    assert not missing, f"the reference names origin members that do not exist: {missing}"


def test_the_python_reference_lists_the_root_and_the_public_modules_as_they_are() -> None:
    """The reference's two surface tables are the first list a newcomer reads, so each names exactly what is there."""
    page = (REPO / "docs" / "reference" / "python-api.mdx").read_text(encoding="utf-8")
    section = page.split("## The whole public surface")[1].split("\n## ")[0]
    root_table, module_table = section.split("| Group | Names |")[1].split("| Module | What it holds |")
    listed = set(re.findall(r"`(\w+)`", root_table.split("\n\n")[0]))
    assert listed == set(decktalk.__all__), sorted(listed ^ set(decktalk.__all__))
    for module_name, held in re.findall(r"^\| `decktalk\.([\w.]+)` \| (.+) \|$", module_table, re.MULTILINE):
        module = importlib.import_module(f"decktalk.{module_name}")
        named = set(re.findall(r"`(\w+)`", held))
        assert named <= set(module.__all__), f"decktalk.{module_name} has no {sorted(named - set(module.__all__))}"
