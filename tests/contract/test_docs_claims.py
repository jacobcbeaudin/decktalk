"""Claims in the docs that a reader can act on, held to what the code actually does.

`scripts/check_docs_links.py` checks that every link resolves and every page is in the
navigation. This file checks that the sentences are true: a page that says the one-line installer
runs on Windows contradicts `install.sh`, which refuses it by name, and a pinning example that names
an unpublished version sends a reader who copies it to a resolver error.

Each test holds a claim by its behaviour rather than by its words, so a page or the installer can be
reworded freely and a test fails only when the claim stops being true.

Both are the same kind of fault. A sentence is written when something is true, the code moves, and
prose has nothing else holding it in place.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
from pathlib import Path

import pytest

import check_docs_links
import decktalk
from decktalk import ApprovalRequired, InputError, ProviderError
from decktalk.cli import catalog
from decktalk.cli.app import docs_for
from decktalk.explain import explain
from decktalk.results import Layer
from decktalk.settings import BY_ID, KEYS, Scope
from decktalk.speech import SpeechRequest, Word
from support.fakes import FAKE_VOICE_NAME, FakeVoice
from support.installer import fake_path
from support.links import link
from support.paths import REPO
from support.projects import MINIMAL_TOML, write_project
from support.runs import a_machine

INSTALLER = REPO / "install.sh"


def anchors(slug: str) -> set[str]:
    """Every anchor a docs page's headings give it, read the way the link check reads every page."""
    return check_docs_links.read_pages()[slug].anchors


def reader_pages() -> list[Path]:
    """The README and every docs page but the changelog, which is generated from history."""
    pages = [REPO / "README.md", *sorted((REPO / "docs").rglob("*.mdx"))]
    return [page for page in pages if page.name != "changelog.mdx"]


def released_versions() -> set[str]:
    """Every version the changelog lists, which is every version that was released.

    Read from the changelog rather than from `git tag`, because the tags are not there when this
    runs: actions/checkout clones one commit and no tags, so `git tag` comes back empty in CI and
    the test would fail for a reason that has nothing to do with the docs. The changelog is committed,
    is generated from CHANGELOG.md by release-please, and is present wherever the file it checks is
    present, which is the only property that matters for a source of truth.
    """
    changelog = (REPO / "docs" / "changelog.mdx").read_text(encoding="utf-8")
    return set(re.findall(r'<Update\s+label="(\d+\.\d+\.\d+)"', changelog))


def test_every_documented_version_pin_is_a_version_that_exists() -> None:
    """`DECKTALK_VERSION=` is shown so a reader can paste it, and a reader who pastes an unreleased
    version gets a resolver failure rather than an install. A number written from the release being
    prepared names a version that does not exist yet."""
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

    This holds the claim rather than a spelling of it: a page may say anything about
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
    this holds the two halves together. The page heads each section with the whole command line, so
    an anchor that names the bare command lands at the top of the page."""
    headings = anchors("reference/cli")
    rows = catalog.walk()
    assert rows, "the parser offers no command, so this test says nothing"
    missing = [row["command"] for row in rows if docs_for(*row["command"].split()).split("#")[1] not in headings]
    assert not missing, f"the help sends a reader to an anchor the reference page has not got: {missing}"


def test_every_settings_key_sends_a_reader_to_the_table_that_holds_it() -> None:
    """`config explain KEY` publishes a URL per key, and the reference has one page with a heading per
    table. A URL that names a page per key is a link into nothing."""
    headings = anchors("reference/configuration")
    assert KEYS, "the settings tree publishes no key, so this test says nothing"
    missing = sorted(key.id for key in KEYS if explain(key.id).docs.split("#")[-1] not in headings)
    assert not missing, f"explained with an anchor the configuration page has not got: {missing}"


def test_every_origin_member_the_python_reference_names_is_one_an_origin_has(tmp_path: Path) -> None:
    """Every member the reference lists on the origin, in its table or its example, is one the origin
    has, because a reader who copies a missing one meets an `AttributeError`."""
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


LAYERED = [
    pytest.param("video.crf", {Layer.PROJECT: 20, Layer.ENVIRONMENT: 23, Layer.OVERRIDE: 21}, id="a project key"),
    pytest.param("narration.retries", {Layer.MACHINE: 4, Layer.ENVIRONMENT: 5, Layer.OVERRIDE: 6}, id="a machine key"),
]
"""One key of each scope with a different value at every layer that may state it, lowest first."""


def opened_with(root: Path, key: str, values: dict[Layer, int]) -> decktalk.Project:
    """The project in `root` with `key` set to each of `values` at its layer and at no other layer."""
    table, name = key.split(".")
    line = {layer: f"\n[{table}]\n{name} = {value}\n" for layer, value in values.items()}
    root.mkdir()
    write_project(root, MINIMAL_TOML + line.get(Layer.PROJECT, ""))
    (root / "machine.toml").write_text(line.get(Layer.MACHINE, ""), encoding="utf-8")
    environ = {BY_ID[key].environment: str(values[Layer.ENVIRONMENT])} if Layer.ENVIRONMENT in values else {}
    pair = (f"{key}={values[Layer.OVERRIDE]}",) if Layer.OVERRIDE in values else ()
    machine_scoped = BY_ID[key].scope is Scope.MACHINE
    machine = decktalk.Machine.of(
        environ=environ,
        config_path=root / "machine.toml",
        cwd=root,
        cache_dir=root / "cache",
        overrides=pair if machine_scoped else (),
    )
    return decktalk.open(root, machine=machine, overrides=() if machine_scoped else pair)


@pytest.mark.parametrize(("key", "values"), LAYERED)
def test_each_layer_overrides_the_ones_before_it(tmp_path: Path, key: str, values: dict[Layer, int]) -> None:
    """The glossary and the configuration page say each layer overrides the ones before it. Every pair
    of neighbours competes for one key here, so `--set` against the environment is held by value and
    by the record alike, rather than by two layers that each set a different key."""
    layers = list(values)
    assert layers == sorted(layers, key=list(Layer).index), "LAYERED lists its layers lowest first"
    table, name = key.split(".")
    for count in range(1, len(layers) + 1):
        present = {layer: values[layer] for layer in layers[:count]}
        project = opened_with(tmp_path / str(count), key, present)
        top = layers[count - 1]
        assert project.layers.winner(key).layer is top, (key, top)
        assert getattr(getattr(project.settings, table), name) == present[top], (key, top)


def test_a_link_into_a_sibling_whose_name_extends_the_projects_is_refused(tmp_path: Path) -> None:
    """The decktalk.toml reference says a path that resolves outside the project is refused, a link
    included. A sibling named `<project>-evil` starts with the project's own path when both are read
    as text, so this is the one escape that only a comparison of path parts refuses."""
    root, outside = tmp_path / "lesson", tmp_path / "lesson-evil" / "deck"
    outside.mkdir(parents=True)
    (outside / "index.html").write_text("<html></html>", encoding="utf-8")
    root.mkdir()
    write_project(root)
    link(root / "deck", outside)
    with pytest.raises(InputError, match="outside the project"):
        decktalk.open(root, machine=a_machine(root)).status()


PRICED_TOML = """
[project]
name = "t"

[[section]]
number = 1
page = "deck/index.html"
scene = "1"

[elevenlabs]
dollars_per_1000_characters = 0.30
"""
"""One spoken page section at a stated rate, so `--max-cost` has a price to hold against."""


class ClosedVoice(FakeVoice):
    """A voice that keeps every request it is sent and then fails it, so a run the gate let through
    stops at the voice rather than going on to measure a take with tools a unit test does not fetch."""

    def speak(self, request: SpeechRequest) -> tuple[bytes, list[Word]]:
        self.requests.append(request)
        raise ProviderError("the voice under test sells nothing")


def test_the_ceiling_a_refusal_names_is_the_lowest_one_that_lets_the_run_buy(tmp_path: Path) -> None:
    """The requirements page says `--max-cost` refuses a run whose most is over the ceiling, and the
    refusal's hint names the ceiling to raise it to. That ceiling has to let the run through to the
    voice, and one cent under it has to refuse the run before the voice hears a word."""
    write_project(tmp_path, PRICED_TOML)
    (tmp_path / "script.md").write_text("## 1. Open\n\n" + "A bowl of soup sits on the table. " * 8, encoding="utf-8")
    (tmp_path / "deck").mkdir()
    (tmp_path / "deck" / "index.html").write_text("<html></html>", encoding="utf-8")
    voice = ClosedVoice()
    machine = decktalk.Machine.of(
        environ={"DECKTALK_VOICE_ID": "voice-under-test"},
        config_path=tmp_path / "machine.toml",
        cwd=tmp_path,
        cache_dir=tmp_path / "cache",
        speech_providers={FAKE_VOICE_NAME: lambda _context: voice},
    )
    project = decktalk.open(tmp_path, machine=machine)
    with pytest.raises(ApprovalRequired) as refused:
        project.narrate(spend=True, max_cost=0.0)
    named = re.search(r"--max-cost (\d+\.\d\d)", refused.value.hint or "")
    assert named, refused.value.hint
    ceiling = float(named.group(1))
    with pytest.raises(ApprovalRequired):
        project.narrate(spend=True, max_cost=round(ceiling - 0.01, 2))
    assert not voice.requests, "a run refused at its ceiling still reached the voice"
    with pytest.raises(ProviderError):
        project.narrate(spend=True, max_cost=ceiling)
    assert voice.requests, f"--max-cost {ceiling:.2f}, the ceiling the refusal named, was refused"
