"""The house rules for prose, over every file git tracks, held where prose is and nowhere else.

Two of the founder's three rules are mechanical and this file holds them. No semicolon joins two
clauses, because a full stop is the same sentence with one less thing to read. No em dash appears
where a reader meets it as punctuation, because a colon or a full stop says the same thing and the
dash is already spoken for: it is DeckTalk's own beat marker in a script, so a dash in prose and a
dash in data would be one character with two meanings.

The third rule, that every sentence is complete and declarative, is not held here and the panel said
so out loud. Any regex over it passes everything or fails every heading, table and code fence, and a
proxy a contributor learns to satisfy is worse than no mechanism at all. `CONTRIBUTING.md` names it
as a review item so the gap is designed rather than assumed.

**Prose is found rather than assumed.** In Python it is the docstrings and the comments, read from
the tokens, so a string a stage sends to a voice is data and is left alone. In TypeScript, in
JavaScript and in shell it is the comments. In Markdown it is everything outside a fenced block and
outside a code span, because a fence and a span are both quoting something. In a data file it is
nothing at all. On top of that, a quoted run inside prose is data wherever it appears, since a
sentence that quotes `## 3. The demo - 0:40 to 1:40` is quoting the syntax and not writing a dash.
"""

from __future__ import annotations

import io
import re
import subprocess
import tokenize
from pathlib import Path

import pytest

from support.paths import REPO

EM_DASH = "—"
"""The character the rule refuses in prose, which is also the beat a script writes between phrases."""

SEMICOLON = ";"

PROSE_SUFFIXES = {".py", ".md", ".mdx", ".ts", ".js", ".sh", ".css", ".html", ".yml", ".yaml", ".toml"}
"""Every kind of tracked file that carries a sentence a person reads."""

DATA_SUFFIXES = {".json", ".jsonl", ".lock", ".svg", ".webp", ".woff2", ".png", ".ico", ".txt"}
"""Every kind of tracked file that carries no prose, so nothing in it is punctuation."""

DATA_FILES = {
    "src/decktalk/template/starter/script.md": "A script is spoken text, where the dash is the beat.",
    "src/decktalk/template/examples/lesson/script.md": "A script is spoken text, where the dash is the beat.",
    "CHANGELOG.md": "release-please writes it from the commit subjects, so it is a record and not a page.",
}
"""Every tracked file whose whole content is data, and the one sentence saying what makes it data."""

GENERATED_PARTS = ("runtime/decktalk-runtime.js", "runtime/decktalk-probe.js")
"""The compiled bundles, whose prose is the prose of the TypeScript they are compiled from."""

SEMICOLON_EXEMPT_SUFFIXES = {".ts", ".js", ".css", ".html", ".yml", ".yaml", ".sh"}
"""Where a semicolon is syntax rather than punctuation, so only the dash rule reaches those files."""

CODE_FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`[^`]*`")
QUOTED = re.compile(r"""("[^"\n]*"|'[^'\n]*'|<[^>\n]*>)""")
"""A run a sentence quotes, which is data wherever it appears, including an HTML tag it shows."""

LINE_COMMENT = re.compile(r"(?://|#)\s?(.*)$")
BLOCK_COMMENT = re.compile(r"/\*(.*?)\*/", re.DOTALL)


def tracked() -> list[Path]:
    """Every file git tracks, which is the set the rule holds over."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, check=True, capture_output=True, text=True).stdout
    return [REPO / name for name in out.split("\0") if name]


def readable(path: Path) -> bool:
    """Whether a tracked file carries prose at all, which decides whether the rule reaches it."""
    name = path.relative_to(REPO).as_posix()
    if name in DATA_FILES or any(part in name for part in GENERATED_PARTS):
        return False
    return path.suffix in PROSE_SUFFIXES and path.suffix not in DATA_SUFFIXES


def strip_data(text: str) -> str:
    """One line of prose with every quoted run taken out, because a quoted run is data."""
    return QUOTED.sub(" ", CODE_SPAN.sub(" ", text))


def python_prose(source: str) -> list[tuple[int, str]]:
    """Every docstring and every comment in one Python file, read from the tokens.

    A string that is not a docstring is data, such as the punctuation a voice is sent or the regex a
    heading is parsed with, so it never reaches the rule.
    """
    found: list[tuple[int, str]] = []
    previous = tokenize.INDENT
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            found.append((token.start[0], token.string))
        elif token.type == tokenize.STRING and previous in (tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE):
            # The quotes that open and close a docstring are not prose, and leaving them in would
            # make the first quoted run of the docstring read as the delimiter it is not.
            found.append((token.start[0], token.string.strip("rbuf").strip("\"'")))
        if token.type not in (tokenize.NL, tokenize.COMMENT):
            previous = token.type
    return found


def markdown_prose(source: str) -> list[tuple[int, str]]:
    """Every line of a Markdown page that is not inside a fenced block, which is what a reader reads."""
    found: list[tuple[int, str]] = []
    fenced = False
    for number, line in enumerate(source.splitlines(), start=1):
        if CODE_FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            found.append((number, line))
    return found


def comment_prose(source: str) -> list[tuple[int, str]]:
    """Every comment in a file whose code is not Python, which is the only prose such a file carries."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if match := LINE_COMMENT.search(line):
            found.append((number, match.group(1)))
    for match in BLOCK_COMMENT.finditer(source):
        number = source[: match.start()].count("\n") + 1
        found += [(number + offset, text) for offset, text in enumerate(match.group(1).splitlines())]
    return found


def prose(path: Path) -> list[tuple[int, str]]:
    """Every line of one tracked file that a reader meets as a sentence."""
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return python_prose(source)
    if path.suffix in (".md", ".mdx"):
        return markdown_prose(source)
    return comment_prose(source)


def offences(path: Path, character: str) -> list[str]:
    """Every line of one file where this character stands in prose rather than in data."""
    name = path.name if REPO not in path.parents else path.relative_to(REPO).as_posix()
    return [f"{name}:{number}: {line.strip()[:100]}" for number, line in prose(path) if character in strip_data(line)]


PROSE_FILES = [path for path in tracked() if readable(path)]
IDS = [path.relative_to(REPO).as_posix() for path in PROSE_FILES]


@pytest.mark.parametrize("path", PROSE_FILES, ids=IDS)
def test_no_tracked_file_writes_an_em_dash_in_prose(path: Path):
    """The dash is the beat a script writes, so in a sentence it is a colon or a full stop instead."""
    found = offences(path, EM_DASH)
    assert found == [], "\n".join(found)


@pytest.mark.parametrize(
    "path",
    [path for path in PROSE_FILES if path.suffix not in SEMICOLON_EXEMPT_SUFFIXES],
    ids=[name for name in IDS if Path(name).suffix not in SEMICOLON_EXEMPT_SUFFIXES],
)
def test_no_tracked_file_joins_two_clauses_with_a_semicolon(path: Path):
    """Two clauses a semicolon joins are one sentence with one less thing to read as two sentences."""
    found = offences(path, SEMICOLON)
    assert found == [], "\n".join(found)


@pytest.mark.parametrize("name", sorted(DATA_FILES))
def test_every_file_excused_as_data_is_still_tracked_and_still_data(name: str):
    """A file excused from the rule and no longer there is an exemption that has outlived its reason."""
    assert (REPO / name).exists(), f"{name} is excused from the prose rules and is not tracked any more."
    assert DATA_FILES[name].endswith("."), name


def test_the_rule_sees_a_dash_a_sentence_writes_and_not_one_a_string_carries(tmp_path: Path):
    """The guard is only worth its exemptions if it still sees the dash each of them is not."""
    path = tmp_path / "sample.py"
    path.write_text(
        f'"""A heading is written as "## 1. Open {EM_DASH} 0:00 to 0:20" and read back."""\n'
        f'BEAT = "{EM_DASH}"\n'
        f"# The dash {EM_DASH} which a reader meets as punctuation {EM_DASH} is what this refuses.\n",
        encoding="utf-8",
    )
    found = [line.split(":")[1] for line in offences(path, EM_DASH)]
    assert found == ["3"], found
