"""The packaged skills say only what the product has, which is what makes a rename impossible to forget.

Every `decktalk` line in a skill goes through the real parser, every configuration key is a field of
the settings or the project document, and every verdict, skip reason and envelope field is a name the
code produces. A skill names no vendor, no model and no harness, because the same folder ships to
every reader of it.

This module runs in the default fast suite: no browser, no network and no spend.
"""

from __future__ import annotations

import re
import shlex
import tomllib
import types
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, NamedTuple, Union, get_args, get_origin, get_type_hints

import pytest

from decktalk.artifacts import ProgressRow
from decktalk.cli.parser import UsageError, build_parser, command_parsers
from decktalk.cli.schema import ENVELOPE_KEYS, PAYLOADS, Envelope
from decktalk.errors import ErrorCode
from decktalk.model.cues import Cue, SectionCues
from decktalk.pipeline import ProgressEvent
from decktalk.scaffold.skills import SKILL_NAMES, packaged_skills, skills_dir
from decktalk.settings import Settings
from decktalk.verdicts import FINDING_KEYS, VERDICT_KEYS, Finding, SkipReason, Verdict

# ---- what the test collects ---------------------------------------------------------------


class SkillFile(NamedTuple):
    """One file of one skill: the skill it belongs to, its path, and its text."""

    skill: str
    path: Path
    text: str

    @property
    def where(self) -> str:
        return f"{self.skill}/{self.path.name}"


def _files() -> list[SkillFile]:
    """Every SKILL.md and every reference file of every packaged skill."""
    out: list[SkillFile] = []
    for folder in packaged_skills():
        for path in sorted(folder.rglob("*.md")):
            out.append(SkillFile(folder.name, path, path.read_text(encoding="utf-8")))
    return out


FILES = _files()
SKILL_MDS = [f for f in FILES if f.path.name == "SKILL.md"]


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
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


# ---- part 1, structure --------------------------------------------------------------------


def test_every_packaged_skill_is_a_folder_with_one_skill_file():
    assert sorted(p.name for p in packaged_skills()) == sorted(SKILL_NAMES)
    for folder in packaged_skills():
        assert (folder / "SKILL.md").is_file(), f"{folder.name} has no SKILL.md"
    for file in FILES:
        depth = len(file.path.relative_to(skills_dir() / file.skill).parts)
        assert depth <= 2, f"{file.where} sits more than one folder below its skill"


@pytest.mark.parametrize("file", SKILL_MDS, ids=lambda f: f.skill)
def test_the_frontmatter_holds_only_the_keys_a_reader_of_it_accepts(file: SkillFile):
    doc, body = _frontmatter(file.text)
    assert doc, f"{file.where} has no frontmatter"
    assert set(doc) <= {"name", "description", "license", "compatibility", "metadata"}, sorted(doc)
    assert doc["name"] == file.skill and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", doc["name"])
    assert doc["name"].startswith("decktalk-") and 1 <= len(doc["name"]) <= 64
    assert 1 <= len(doc["description"]) <= 1024
    assert len(doc.get("compatibility", "")) <= 500
    assert "<" not in "".join(doc.values()), "frontmatter carries no XML-style tag"
    assert len(body.splitlines()) <= 200, f"{file.where} body is {len(body.splitlines())} lines"


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.where)
def test_every_relative_link_resolves_inside_its_own_skill(file: SkillFile):
    folder = skills_dir() / file.skill
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", file.text):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        resolved = (file.path.parent / target.split("#", 1)[0]).resolve()
        assert resolved.is_file(), f"{file.where} links to {target}, which is not a file"
        assert resolved.is_relative_to(folder.resolve()), f"{file.where} links outside its skill"


@pytest.mark.parametrize("file", SKILL_MDS, ids=lambda f: f.skill)
def test_every_skill_hands_off_to_a_sibling_by_name(file: SkillFile):
    _doc, body = _frontmatter(file.text)
    section = body.partition("\n## Hand off")[2]
    assert section.strip(), f"{file.where} has no 'Hand off' section"
    named = [name for name in SKILL_NAMES if name != file.skill and name in section]
    assert named, f"{file.where} names no sibling skill in its hand off"


def test_a_shared_reference_file_is_byte_identical_in_every_skill_that_ships_it():
    """A rule two skills state is one file, so it cannot drift between them."""
    shared: dict[str, list[SkillFile]] = {}
    for file in FILES:
        if file.path.name != "SKILL.md":
            shared.setdefault(file.path.name, []).append(file)
    for name, copies in shared.items():
        bytes_of = {c.path.read_bytes() for c in copies}
        assert len(bytes_of) == 1, f"{name} differs between {sorted(c.skill for c in copies)}"


# ---- part 2, the vendor and harness lint ---------------------------------------------------

# A skill ships to whoever reads it, so it names no vendor and no harness of its own.
BANNED_WORDS = (
    "anthropic claude openai codex gpt gemini cursor copilot windsurf opencode goose kiro sonnet opus haiku"
).split()
# The one short name matched with its case, so the HTML entity &amp; in a sample is not a hit.
BANNED_EXACT = ("Amp",)
BANNED_PHRASES = (
    "the Bash tool", "the Read tool", "the Write tool", "the Edit tool",
    "TodoWrite", "AskUserQuestion", "WebFetch", "activate_skill", "subagent", "MCP",
)  # fmt: skip
BANNED_SYNTAX = ("$ARGUMENTS", "${CLAUDE", ".claude/", "@file")
# The names of things the product really uses, listed here so adding one is a deliberate edit. No
# name may sit in both lists, which is a property of these two lines and is read rather than tested.
ALLOWED = ("elevenlabs", "katex", "ffmpeg", "chromium")


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.where)
def test_a_skill_names_no_vendor_no_model_and_no_harness(file: SkillFile):
    hits: list[str] = []
    for number, line in enumerate(file.text.splitlines(), 1):
        for word in BANNED_WORDS:
            if re.search(rf"\b{word}\b", line, re.IGNORECASE):
                hits.append(f"{file.where}:{number}: {word}")
        for word in BANNED_EXACT:
            if re.search(rf"\b{word}\b", line):
                hits.append(f"{file.where}:{number}: {word}")
        for phrase in (*BANNED_PHRASES, *BANNED_SYNTAX):
            if phrase in line:
                hits.append(f"{file.where}:{number}: {phrase}")
        if re.match(r"^\s*/[a-z][\w-]*\s*$", line):
            hits.append(f"{file.where}:{number}: a leading slash command")
        # `${` on its own is what a slide skill teaches an author to escape inside a template
        # literal, so only a closed expansion counts.
        if re.search(r"\$\([^)]*\)|\$\{[A-Za-z_][\w]*\}", line):
            hits.append(f"{file.where}:{number}: an inline shell expansion")
    assert not hits, "\n".join(hits)


# ---- part 3, the commands ------------------------------------------------------------------


class Line(NamedTuple):
    """One command line a skill writes, and where it wrote it."""

    where: str
    text: str
    context: str  # The whole line it sits in, so a rule that forbids a flag is not an example of it.


def _command_lines() -> list[Line]:
    """Every line beginning with `decktalk`, from the fenced blocks and the inline code."""
    out: list[Line] = []
    for file in FILES:
        for number, line in enumerate(file.text.splitlines(), 1):
            where = f"{file.where}:{number}"
            stripped = line.strip().lstrip("$ ").strip()
            if stripped.startswith("decktalk ") or stripped == "decktalk":
                out.append(Line(where, stripped, line))
            for code in re.findall(r"`([^`\n]+)`", line):
                if code.startswith("decktalk ") and " " in code:
                    out.append(Line(where, code.strip(), line))
    return out


COMMAND_LINES = _command_lines()


def test_a_skill_writes_command_lines_at_all():
    """The whole point of the collection is that it is not empty."""
    assert len(COMMAND_LINES) > 20


@pytest.mark.parametrize("line", COMMAND_LINES, ids=lambda line: f"{line.where} {line.text}")
def test_every_command_a_skill_writes_parses(line: Line):
    """A skill writes concrete values, never a placeholder, so the real parser can accept the line."""
    parser = build_parser()
    try:
        parser.parse_args(shlex.split(line.text)[1:])
    except (UsageError, SystemExit) as refused:
        pytest.fail(f"{line.where}: {line.text!r} does not parse: {refused}")


SPENDING_FLAGS = ("--force", "--exit-zero", "--allow-unresolved-cues", "--allow-unknown-cues", "--allow-placeholders")

# A command that can run without a voice spends on one unless it is told not to, and the parser is
# what says which commands those are, by offering `--no-voice` on them.
VOICED = {name for name, sub in command_parsers().items() if "--no-voice" in sub.format_usage()}


@pytest.mark.parametrize("line", COMMAND_LINES, ids=lambda line: f"{line.where} {line.text}")
def test_a_line_that_would_spend_sits_in_a_skill_that_waits_for_approval(line: Line):
    words = shlex.split(line.text)
    command = words[1] if len(words) > 1 else ""
    spends = (command in VOICED and "--no-voice" not in words) or (command == "soundscape" and "--dry-run" not in words)
    if not spends:
        return
    skill = line.where.split("/", 1)[0]
    text = "".join(f.text for f in FILES if f.skill == skill).lower()
    assert "wait for" in text and "approve" in text, f"{line.where} spends, and {skill} never stops for approval"


@pytest.mark.parametrize("line", COMMAND_LINES, ids=lambda line: f"{line.where} {line.text}")
def test_no_example_carries_a_flag_that_hides_a_finding(line: Line):
    """A rule such as "Never pass --force" passes, because the test reads the line it sits in."""
    forbidding = re.search(r"\b(never|do not|don't|refuse|without)\b", line.context, re.IGNORECASE)
    for flag in SPENDING_FLAGS:
        if flag in shlex.split(line.text):
            assert forbidding, f"{line.where} uses {flag} outside a sentence that forbids it"


# ---- part 4, the configuration and the file keys -------------------------------------------


def _table_keys() -> dict[str, set[str]]:
    """Every configuration table a skill may name, with the keys it really has.

    The tuning tables come from the settings dataclasses and the presentation tables from the
    project document, which is the same pair of readers the generated reference uses, so a key that
    is renamed in one place is renamed here too.
    """
    from decktalk.model import document

    tables: dict[str, set[str]] = {}
    for f in fields(Settings):
        holder = getattr(Settings(), f.name)
        tables[f.name] = {k.name for k in fields(holder)} if is_dataclass(holder) else set()
    for name, cls in (
        ("voice", document.Voice),
        ("transition", document.Transition),
        ("mix", document.Mix),
        ("soundscape", document.Soundscape),
    ):
        tables[name] = tables.get(name, set()) | {k.name for k in fields(cls)}
    # `[project]` is the document's own top level, and `[[section]]` is either section shape.
    tables["project"] = {f.name for f in fields(document.Document)} - {"sections"}
    tables["section"] = {f.name for f in fields(document.PageSection)} | {f.name for f in fields(document.ClipSection)}
    tables["mix"] |= {"loudness", "sfx"}
    return tables


TABLES = _table_keys()


# `script.md` writes its directions in brackets too, and they are not configuration tables.
SCRIPT_DIRECTIONS = {"beat", "pause"}


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.where)
def test_every_configuration_table_a_skill_names_exists(file: SkillFile):
    named = set(re.findall(r"`\[\[?([a-z_]+)(?:\.[a-z_]+)?\]?\]`", file.text))
    unknown = sorted(named - set(TABLES) - SCRIPT_DIRECTIONS)
    assert not unknown, f"{file.where} names the table(s) {unknown}, which no dataclass declares"


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.where)
def test_every_toml_block_a_skill_writes_parses_and_names_real_tables(file: SkillFile):
    for block in re.findall(r"```toml\n(.*?)```", file.text, re.DOTALL):
        doc = tomllib.loads(block)
        for table, body in doc.items():
            assert table in TABLES, f"{file.where} writes [{table}], which no dataclass declares"
            rows = body if isinstance(body, list) else [body]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                unknown = sorted(set(row) - TABLES[table])
                assert not unknown, f"{file.where} writes [{table}] {unknown}, which the table has no key for"


CUE_KEYS = {f.name for f in fields(Cue) if not f.name.endswith("_set")} | {
    f.name for f in fields(SectionCues) if f.name != "cues"
}


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.where)
def test_every_cues_json_key_a_skill_names_is_a_field_of_the_cue_model(file: SkillFile):
    for block in re.findall(r"```json\n(.*?)```", file.text, re.DOTALL):
        if '"cue"' not in block:
            continue
        for key in re.findall(r'"([a-z_]+)"\s*:', block):
            if key in ("sections", "cues") or key.isdigit():
                continue
            assert key in CUE_KEYS, f"{file.where} names the cue key {key!r}, which the cue model has no field for"


VERDICT_NAMES = {v.name for v in Verdict}
SKIP_NAMES = {s.name for s in SkipReason}


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.where)
def test_every_verdict_and_skip_reason_a_skill_names_is_a_member_of_its_enum(file: SkillFile):
    shouted = set(re.findall(r"`([A-Z][A-Z0-9_]{3,})`", file.text))
    unknown = sorted(shouted - VERDICT_NAMES - SKIP_NAMES - _ALSO_SHOUTED)
    assert not unknown, f"{file.where} names {unknown}, which is no verdict and no skip reason"


# The other shouted names a skill may write: the error codes, the placeholder the scaffold's script
# ships with, and two file formats.
_ALSO_SHOUTED = {code.value for code in ErrorCode} | {"CLIENT_NAME", "JSON", "PNG"}


# ---- part 5, the JSON fields -----------------------------------------------------------------
#
# The typed reader in `cli/schema.py` is the whole of what a caller may read from DeckTalk's JSON,
# and `tests/contract/test_results.py` holds every result's payload to it. So a field a skill names is checked
# against those types: a dotted path such as `narrate.sections[].request.text` is walked field by
# field, and a sentence that says what a path carries has each field it lists checked on that path.


def _members(kind: Any) -> list[Any]:
    """The types a value of `kind` can be once null, a list and a union are opened."""
    if get_origin(kind) in (Union, types.UnionType):
        return [m for arg in get_args(kind) if arg is not type(None) for m in _members(arg)]
    if get_origin(kind) in (list, tuple):
        return _members(get_args(kind)[0])
    return [kind]


def json_fields(kind: Any) -> dict[str, Any] | None:
    """Each JSON key a value of `kind` carries and the type under it, or None when any key may appear."""
    found: dict[str, Any] = {}
    for member in _members(kind):
        if member is Any or get_origin(member) is dict:
            return None
        if member is Verdict:
            found |= dict.fromkeys(VERDICT_KEYS, str)
        elif member is Finding:
            found |= dict.fromkeys(FINDING_KEYS, str)
        elif is_dataclass(member):
            found |= get_type_hints(member)
    return found


HEADS: dict[str, Any] = {
    **{name: kind for name, kind in get_type_hints(Envelope).items() if name in ENVELOPE_KEYS},
    **PAYLOADS,
    "verdict": Verdict,  # The one object every verdict in any payload is written as.
}
"""What a path may start with: an envelope field, a command's payload, or a verdict object."""


def resolve(path: str) -> Any:
    """The type a dotted path names, or an AssertionError saying which step names nothing."""
    head, *steps = path.replace("[]", "").split(".")
    kind = HEADS[head]
    for step in steps:
        known = json_fields(kind)
        if known is None:
            return Any
        assert step in known, f"`{path}` names `{step}`, which is no field of {sorted(known)}"
        kind = known[step]
    return kind


PATH = re.compile(r"^([a-z_]+)(\[\])?((\.[a-z0-9_]+(\[\])?)*)$")


def named_paths(text: str) -> list[str]:
    """Every backticked path whose first step is a head, such as `status.run` or `findings.items[]`."""
    out = []
    for token in re.findall(r"`([^`\n]+)`", text):
        match = PATH.match(token)
        if match and match.group(1) in HEADS and (match.group(3) or match.group(2)):
            out.append(token)
    return out


@pytest.mark.parametrize("file", FILES, ids=lambda f: f.where)
def test_every_json_path_a_skill_names_is_a_path_the_typed_reader_reads(file: SkillFile):
    for path in named_paths(file.text):
        resolve(path)


JSON_FIELDS = next(f for f in FILES if f.path.name == "json-fields.md")
LISTING = re.compile(r"\b(carries|each with|repeats|fills|leaving)\b")


def _paragraphs(text: str) -> list[list[str]]:
    """Each paragraph as its sentences, with the lines of a paragraph joined."""
    out = []
    for paragraph in re.split(r"\n\s*\n", text):
        flat = re.sub(r"\s+", " ", paragraph).strip()
        if flat:
            out.append(re.split(r"(?<=\.) (?=[A-Z`])", flat))
    return out


class Subject(NamedTuple):
    """The path a sentence is about, and the type it names."""

    kind: Any
    named: str


def listed_fields(
    sentence: str, carried: Subject | None, *, listing: bool
) -> tuple[list[tuple[str, Subject]], Subject | None]:
    """Each field a sentence lists with the subject it is checked on, and the subject the sentence opened with.

    A backticked path, or a head that is no field of the subject, starts a new subject, and a sentence
    that names none is about the subject the sentence before it in its paragraph opened with. A field
    written as `x[]` and followed by "of" makes the fields after it fields of `x`, and a field after
    "whose" is a field of the one before. Anything shouted is a code, so it is never a field. In a
    sentence that lists fields, a field with no subject is a subject the reference forgot to name.
    """
    out: list[tuple[str, Subject]] = []
    subject, opened = carried, None
    previous: Subject | None = None
    parts = re.split(r"`([^`]+)`", sentence)
    for index in range(1, len(parts), 2):
        token, before, after = parts[index], parts[index - 1], parts[index + 1]
        known = (json_fields(subject.kind) or {}) if subject is not None else {}
        dotted = "." in token and token.split(".", 1)[0].replace("[]", "") in HEADS
        if dotted or token in HEADS and token.removesuffix("[]") not in known:
            subject, previous = Subject(resolve(token), token), None
            opened = opened or subject
            continue
        if not re.fullmatch(r"[a-z_0-9]+(\[\])?", token):
            continue
        field = token.removesuffix("[]")
        if before.rstrip().endswith("whose") and previous is not None:
            out.append((field, previous))
            continue
        if subject is None:
            assert not listing, f"`{token}` is listed with no path it belongs to: {sentence}"
            continue
        out.append((field, subject))
        previous = Subject(known.get(field, Any), f"{subject.named}.{token}")
        if token.endswith("[]") and after.lstrip().startswith("of "):
            subject = previous
    return out, opened


def test_every_field_the_json_reference_lists_is_a_field_of_the_path_it_names():
    checked = 0
    payloads = JSON_FIELDS.text.split("## The payloads a build reads", 1)[1].split("## build/progress.jsonl")[0]
    for paragraph in _paragraphs(payloads):
        carried: Subject | None = None
        for sentence in paragraph:
            listing = LISTING.search(sentence) is not None
            fields_listed, opened = listed_fields(sentence, carried if listing else None, listing=listing)
            carried = opened or carried
            if not listing:
                continue
            for field, subject in fields_listed:
                known = json_fields(subject.kind)
                assert known is None or field in known, f"`{subject.named}` carries no `{field}`: {sentence}"
                checked += 1
    assert checked > 140, f"only {checked} fields were read, so the sentences are no longer being parsed"


def test_every_field_and_event_of_the_progress_log_the_reference_names_is_one_a_build_writes():
    section = JSON_FIELDS.text.split("## build/progress.jsonl", 1)[1]
    carried = re.search(r"Each line carries ([^.]*)\.", re.sub(r"\s+", " ", section))
    assert carried is not None
    assert re.findall(r"`([a-z_]+)`", carried.group(1)) == [f.name for f in fields(ProgressRow)]
    events = re.search(r"The `event` is ([^.]*)\.", re.sub(r"\s+", " ", section))
    assert events is not None
    assert [ProgressEvent(word) for word in re.findall(r"`([a-z]+)`", events.group(1))] == list(ProgressEvent)


def test_every_table_of_the_json_reference_names_the_fields_of_its_heading():
    """The envelope's table, the row's table and the error's table are the three shapes a reader dispatches on."""
    tables = {
        "The envelope": [*ENVELOPE_KEYS, "<command>"],
        "findings.items[]": list(json_fields(resolve("findings.items[]")) or {}),
        "error": list(json_fields(resolve("error")) or {}),
    }
    for heading, expected in tables.items():
        body = JSON_FIELDS.text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
        named = re.findall(r"^\| `([^`]+)` \|", body, re.MULTILINE)
        assert named == expected, f"the {heading} table names {named}"


def test_no_skill_carries_a_semicolon_in_its_prose():
    """A skill is a file an author receives, and the house rule forbids a semicolon in prose."""
    hits: list[str] = []
    for file in FILES:
        for number, line in enumerate(file.text.splitlines(), 1):
            # A semicolon inside code, an HTML entity or a JavaScript sample is not prose.
            bare = re.sub(r"`[^`]*`|&[A-Za-z]+;", "", line)
            if ";" in bare:
                hits.append(f"{file.where}:{number}: {line.strip()}")
    assert not hits, "\n".join(hits)
