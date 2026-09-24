"""The projects and the skills packaged in the wheel, judged as the data they are.

`tests/decktalk/test_template.py` holds what `decktalk.template` does. This file holds what it
carries: the starter, the lesson example and the six skills, read from the package the way a reader
of the wheel reads them.

Two rules matter most. Every attribute the page contract publishes is written by one of the two
example projects, which is the founder's guard that no knob ships untested, and the declared table
below says which example writes which row, so a row nothing exercises is named rather than
discovered. And a skill names a command of the final vocabulary or it names none, because the CLI is
the instruction set and a copy of it inside a skill is a copy that goes stale.

This module runs in the default fast suite: no browser, no network and no spend.
"""

from __future__ import annotations

import json
import re
import tomllib
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

import pytest

from decktalk import template
from decktalk.inputs.cues import load_cues
from decktalk.inputs.document import Document
from decktalk.inputs.script import parse_script
from decktalk.page import ATTRS, MOMENTS, PAIR_MARK, PAIR_SEPARATOR, WIRE_MARK, Attr
from decktalk.pipeline import Stage
from decktalk.toolchain import assets

# ---- what the wheel carries ---------------------------------------------------------------

STARTER = "starter"
LESSON = "lesson"


def packaged(name: str) -> Path:
    """One packaged project's directory, named the way `--example` names it."""
    if name == STARTER:
        return assets.package_file(f"template/{template.STARTER}")
    found = template.example(name)
    assert found.path is not None, f"{name} is a reserved name and ships no project"
    return assets.package_file(f"template/{found.path}")


PROJECTS = {name: packaged(name) for name in (STARTER, LESSON)}
SKILLS = assets.package_file("skills")


# ---- reading a packaged deck page ----------------------------------------------------------


class Deck(HTMLParser):
    """Every scene, slide, wire id and attribute one deck page writes, read without a browser.

    A static read is enough here because a packaged page is written entirely in markup, which is the
    shape the slide guide teaches. The one cue a handler serves is declared in `data-owns`, so it is
    read from the markup like any other.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scenes: dict[str, str] = {}
        self.cues: dict[str, list[str]] = {}
        self.attrs: set[str] = set()
        self._slide: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        written = {name: (value or "") for name, value in attrs}
        self.attrs.update(name for name in written if name.startswith("data-"))
        if Attr.SCENE.value in written:
            self.scenes[written[Attr.SCENE.value]] = written.get(Attr.NAME.value, "")
        if tag == "template" and Attr.SLIDE.value in written:
            self._slide = written[Attr.SLIDE.value]
            self.cues.setdefault(self._slide, [])
        if self._slide is None:
            return
        for moment in MOMENTS:
            if written.get(moment.value):
                self._add(written[moment.value])
        for pair in written.get(Attr.CLASS.value, "").split(PAIR_SEPARATOR):
            if PAIR_MARK in pair:
                self._add(pair.split(PAIR_MARK, 1)[0])
        for local in written.get(Attr.OWNS.value, "").split():
            self._add(local)

    def handle_endtag(self, tag: str) -> None:
        if tag == "template":
            self._slide = None

    def _add(self, local: str) -> None:
        slide = self._slide
        assert slide is not None
        wire = f"{slide}{WIRE_MARK}{local.strip()}"
        if wire not in self.cues[slide]:
            self.cues[slide].append(wire)


class Project(NamedTuple):
    """One packaged project, parsed by the readers the library itself parses it with."""

    name: str
    root: Path
    document: Document
    decks: dict[str, Deck]

    @property
    def wire_ids(self) -> set[str]:
        return {cue for deck in self.decks.values() for cues in deck.cues.values() for cue in cues}

    @property
    def attrs(self) -> set[str]:
        return {name for deck in self.decks.values() for name in deck.attrs}


def read(name: str) -> Project:
    """One packaged project with its placeholders filled, exactly as `init` fills them."""
    root = PROJECTS[name]
    raw = (root / "decktalk.toml").read_text(encoding="utf-8")
    document = Document.from_toml(tomllib.loads(filled(raw)), default_name=name)
    decks: dict[str, Deck] = {}
    for page in sorted({section.page for section in document.page_sections}):
        deck = Deck()
        deck.feed(filled((root / page).read_text(encoding="utf-8")))
        decks[page] = deck
    return Project(name, root, document, decks)


def filled(text: str) -> str:
    """The two placeholders resolved, so a packaged file parses as a written one would."""
    return text.replace(template.NAME_MARK, "demo").replace(template.TITLE_MARK, "Demo")


PARSED = {name: read(name) for name in PROJECTS}


# ---- the exercised attribute table ----------------------------------------------------------

EXERCISED: dict[Attr, tuple[str, ...]] = {
    Attr.IN: (STARTER, LESSON),
    Attr.DESCRIBE: (STARTER, LESSON),
    Attr.TEX: (STARTER,),
    Attr.TEX_DISPLAY: (STARTER,),
    Attr.IN_STYLE: (STARTER, LESSON),
    Attr.BACK: (STARTER,),
    Attr.FRONT: (STARTER,),
    Attr.OUT: (LESSON,),
    Attr.WORDS: (STARTER,),
    Attr.COUNT: (STARTER, LESSON),
    Attr.CLASS: (LESSON,),
    Attr.DESCRIBE_CLASS: (LESSON,),
    Attr.STAGGER: (LESSON,),
    Attr.STEPS: (LESSON,),
    Attr.IN_SECONDS: (LESSON,),
    Attr.OUT_STYLE: (LESSON,),
    Attr.SWAPS: (LESSON,),
    Attr.DESCRIBE_OUT: (LESSON,),
    Attr.SCENE: (STARTER, LESSON),
    Attr.NAME: (STARTER, LESSON),
    Attr.SLIDE: (STARTER, LESSON),
    Attr.HOLD: (STARTER, LESSON),
    Attr.OWNS: (LESSON,),
    Attr.ENTER: (LESSON,),
}
"""Which packaged example writes which attribute, declared so a gap is named rather than found.

The founder's guard is that every row of the published table is exercised by the starter or by the
lesson, and that a row nothing exercises is cut before the release. This table is what makes the
guard readable: the test below holds it equal to what the two pages really write, in both
directions, so a row that loses its last writer fails here with its own name.
"""


def test_the_declared_table_names_every_attribute_the_contract_publishes() -> None:
    assert sorted(attr.value for attr in EXERCISED) == sorted(attr.value for attr in ATTRS), (
        "EXERCISED and the page contract have drifted. A new attribute needs an example that writes "
        "it, and an attribute nothing writes is cut before the release."
    )


@pytest.mark.parametrize("attr", list(ATTRS), ids=lambda attr: attr.value)
def test_every_attribute_of_the_contract_is_written_by_an_example(attr: Attr) -> None:
    written = tuple(name for name, project in PARSED.items() if attr.value in project.attrs)
    assert written, f"no packaged example writes {attr.value}, so the row is untested and is cut."
    assert written == EXERCISED[attr], f"{attr.value} is written by {written} and EXERCISED declares {EXERCISED[attr]}."


# ---- the packaged projects hold together ------------------------------------------------------


@pytest.mark.parametrize("project", PARSED.values(), ids=lambda p: p.name)
def test_the_project_file_opens_with_the_line_an_editor_binds_a_schema_by(project: Project) -> None:
    first = (project.root / "decktalk.toml").read_text(encoding="utf-8").splitlines()[0]
    assert first == "#:schema https://decktalk.ai/schema/decktalk-1.json", first


@pytest.mark.parametrize("project", PARSED.values(), ids=lambda p: p.name)
def test_every_section_names_a_scene_the_page_it_names_declares(project: Project) -> None:
    for section in project.document.page_sections:
        scenes = project.decks[section.page].scenes
        assert section.scene in scenes, (
            f"section {section.number} names scene {section.scene}, and {section.page} declares {sorted(scenes)}"
        )


@pytest.mark.parametrize("project", PARSED.values(), ids=lambda p: p.name)
def test_the_page_and_the_cue_file_name_the_same_moments(project: Project) -> None:
    listed = {
        cue.cue
        for section in load_cues(
            project.root / project.document.cues, project.root, {s.number for s in project.document.sections}
        )
        for cue in section.cues
    }
    assert listed == project.wire_ids, (
        f"{sorted(listed - project.wire_ids)} are listed and undeclared, and "
        f"{sorted(project.wire_ids - listed)} are declared and unlisted."
    )


@pytest.mark.parametrize("project", PARSED.values(), ids=lambda p: p.name)
def test_every_cue_phrase_occurs_exactly_once_in_its_own_section(project: Project) -> None:
    script = filled((project.root / project.document.script).read_text(encoding="utf-8"))
    spoken = {segment.index: words(segment.spoken) for segment in parse_script(script)}
    for section in load_cues(
        project.root / project.document.cues, project.root, {s.number for s in project.document.sections}
    ):
        for cue in section.cues:
            phrase = words(cue.on)
            found = len(re.findall(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", spoken[section.number]))
            assert found == 1, f"{cue.cue} lands on {cue.on!r}, which the section says {found} times."


def words(text: str) -> str:
    """The text as a cue match compares it, which ignores case and everything between the words."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


RETIRED = (
    "data-cue", "data-reveal", "data-duration", "data-text", "data-delay", "data-preview",
    "data-camera", "data-ease", "data-distance", "data-sync-lead", "min_tail_seconds",
    "max_offset_frames", "true_peak_db", "mix.sfx", "soundscape.sfx", "video.fps", "screenshot_settle_ms",
    "DECKTALK_FFMPEG", "DECKTALK_FFPROBE", "DECKTALK_CACHE_DIR", "preflight", "screenshots",
)  # fmt: skip
"""Every name 0.5.0 retired, which no file a reader receives from the wheel may still carry."""


def carried(path: Path) -> list[str]:
    """Every retired name one packaged file still writes, which is empty on a file that is clean."""
    text = path.read_text(encoding="utf-8")
    return [name for name in RETIRED if name in text]


WRITTEN_SUFFIXES = frozenset({".toml", ".json", ".md", ".html", ".example"})
"""The packaged files a person reads. Everything else is a font or a bundle, and carries no name."""

PROJECT_FILES = sorted(p for root in PROJECTS.values() for p in root.rglob("*") if p.suffix in WRITTEN_SUFFIXES)


@pytest.mark.parametrize("path", PROJECT_FILES, ids=lambda p: p.name)
def test_no_retired_name_survives_in_a_packaged_project(path: Path) -> None:
    assert not carried(path), f"{path.name} still carries {carried(path)}"


# ---- the packaged skills ------------------------------------------------------------------------

COMMANDS = {stage.value for stage in Stage} | {
    "init", "install", "doctor", "status", "check", "words",
    "storyboard", "serve", "build", "clip", "config", "schema",
}  # fmt: skip
"""Every command of the final vocabulary, which is the six stage verbs and the twelve beside them.

The stage half is read from `PIPELINE`'s own enum, so a renamed stage fails here. The other half is
written out, because the CLI that declares it lands after this track and a skill may not name a
command that does not exist either way.
"""

SKILL_FILES = sorted(SKILLS.rglob("*.md"))

BANNED_WORDS = (
    "anthropic claude openai codex gpt gemini cursor copilot windsurf opencode goose kiro sonnet opus haiku"
).split()
"""Every vendor, model and harness name a skill may not carry, because it ships to all of them."""


def skill_of(path: Path) -> str:
    """The skill one packaged file belongs to, which is the first folder under the skills root."""
    return path.relative_to(SKILLS).parts[0]


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    """The YAML frontmatter as a flat mapping, and the body after it."""
    if not text.startswith("---\n"):
        return {}, text
    head, _, body = text[4:].partition("\n---\n")
    doc: dict[str, str] = {}
    key = ""
    for line in head.splitlines():
        if re.match(r"^[a-zA-Z][\w-]*:", line):
            key, _, value = line.partition(":")
            doc[key.strip()] = value.strip()
        elif key and line.strip():
            doc[key] = f"{doc[key]} {line.strip()}".strip()
    return doc, body


def test_every_packaged_skill_is_a_folder_with_one_skill_file() -> None:
    assert sorted(p.name for p in SKILLS.iterdir() if p.is_dir()) == sorted(template.SKILL_NAMES)
    for name in template.SKILL_NAMES:
        assert (SKILLS / name / "SKILL.md").is_file(), f"{name} has no SKILL.md"
    for path in SKILL_FILES:
        assert len(path.relative_to(SKILLS).parts) <= 3, f"{path} sits more than one folder below its skill"


@pytest.mark.parametrize("name", template.SKILL_NAMES)
def test_the_frontmatter_holds_only_the_keys_a_reader_of_it_accepts(name: str) -> None:
    doc, body = frontmatter((SKILLS / name / "SKILL.md").read_text(encoding="utf-8"))
    assert set(doc) <= {"name", "description", "license", "compatibility", "metadata"}, sorted(doc)
    assert doc["name"] == name and re.fullmatch(r"decktalk-[a-z0-9]+", doc["name"])
    assert 1 <= len(doc["description"]) <= 1024
    assert len(doc.get("compatibility", "")) <= 500
    assert "<" not in "".join(doc.values()), "frontmatter carries no XML-style tag"
    assert len(body.splitlines()) <= 200, f"{name} body is {len(body.splitlines())} lines"


@pytest.mark.parametrize("name", template.SKILL_NAMES)
def test_every_skill_hands_off_to_a_sibling_by_name(name: str) -> None:
    _doc, body = frontmatter((SKILLS / name / "SKILL.md").read_text(encoding="utf-8"))
    section = body.partition("\n## Hand off")[2]
    assert section.strip(), f"{name} has no 'Hand off' section"
    assert [other for other in template.SKILL_NAMES if other != name and other in section]


def test_a_shared_reference_file_is_byte_identical_in_every_skill_that_ships_it() -> None:
    """A rule two skills state is one file, so it cannot drift between them."""
    shared: dict[str, list[Path]] = {}
    for path in SKILL_FILES:
        if path.name != "SKILL.md":
            shared.setdefault(path.name, []).append(path)
    for name, copies in shared.items():
        where = sorted(skill_of(path) for path in copies)
        assert len({path.read_bytes() for path in copies}) == 1, f"{name} differs between {where}"


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: f"{skill_of(p)}/{p.name}")
def test_every_relative_link_resolves_inside_its_own_skill(path: Path) -> None:
    folder = SKILLS / skill_of(path)
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        resolved = (path.parent / target.split("#", 1)[0]).resolve()
        assert resolved.is_file() and resolved.is_relative_to(folder.resolve()), target


COMMAND = re.compile(r"`decktalk(?: ([^`\n]*))?`")


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.name)
def test_a_skill_names_only_commands_of_the_final_vocabulary(path: Path) -> None:
    """A skill points at the instruction set. It never becomes a second, staler copy of it."""
    bad: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for written in COMMAND.finditer(line):
            rest = (written.group(1) or "").split()
            if not rest:
                continue
            if rest[0] == "--help":
                continue
            if rest[0] not in COMMANDS:
                bad.append(f"{path.name}:{number}: {written.group(0)} is no command of the vocabulary")
            elif len(rest) > 1 and rest[0] not in {"config", "schema"}:
                bad.append(f"{path.name}:{number}: {written.group(0)} spells a command line")
            elif any(word.startswith("-") for word in rest[1:]):
                bad.append(f"{path.name}:{number}: {written.group(0)} spells a flag")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.name)
def test_no_skill_names_a_vendor_a_model_or_a_harness(path: Path) -> None:
    """A skill ships to whoever reads it, so it names nothing it does not really use."""
    banned = BANNED_WORDS
    hits = [
        f"{path.name}:{number}: {word}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for word in banned
        if re.search(rf"\b{word}\b", line, re.IGNORECASE)
    ]
    assert not hits, "\n".join(hits)


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.name)
def test_no_skill_carries_a_semicolon_or_a_dash_in_its_prose(path: Path) -> None:
    """A skill is a file an author receives, so the house rule for prose binds it too."""
    hits: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        bare = re.sub(r"`[^`]*`|&[A-Za-z]+;", "", line)
        if ";" in bare or "—" in bare or "–" in bare:
            hits.append(f"{path.name}:{number}: {line.strip()}")
    assert not hits, "\n".join(hits)


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.name)
def test_no_retired_name_survives_in_a_skill(path: Path) -> None:
    assert not carried(path), f"{path.name} still carries {carried(path)}"


def test_the_agents_file_init_writes_points_at_the_help_rather_than_repeating_it() -> None:
    """A concise file that names the surface beats a long one that copies it and goes stale."""
    path = assets.package_file(f"template/{template.AGENTS_FILE}")
    text = path.read_text(encoding="utf-8")
    assert not carried(path), f"AGENTS.md still carries {carried(path)}"
    assert "`decktalk --help`" in text and "`decktalk schema`" in text
    assert len(text.split()) < 200, "AGENTS.md is a few lines, not a manual"


def test_the_two_projects_are_the_only_ones_the_wheel_ships() -> None:
    """A reserved example name has no directory, so nothing can be written from it by accident."""
    shipped = {found.name for found in template.EXAMPLES if found.shipped}
    assert shipped == {LESSON}
    assert json.loads((PROJECTS[STARTER] / "cues.json").read_text(encoding="utf-8"))["sections"]
