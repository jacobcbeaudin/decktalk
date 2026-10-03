"""No word the glossary retired comes back in the help, a docs page or a packaged skill.

The glossary at `docs/reference/glossary.mdx` gives each concept one name and lists, in its **Not**
column, the words DeckTalk retired for it. That column is the list this test reads, so the page and
the test cannot drift: retiring a word is one edit to the glossary, and a word stays retired until
that cell stops naming it.

A retired word matches whole and in any case, with an optional plural, so `knobs` and `Knob` trip it
and a longer word that merely contains it does not. A phrase matches across a line break, because a
page wraps wherever its line runs out. The published changelogs are history and are not read, and
neither are the glossary's own **Not** cells, which are where each word is retired.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from decktalk.cli import catalog, main

ROOT = Path(__file__).resolve().parents[2]
"""The repository, which every path this test reads is under."""

GLOSSARY = ROOT / "docs" / "reference" / "glossary.mdx"
"""The page whose **Not** column is the list of retired words."""

NOT_COLUMN = "Not"
"""The header of the glossary column that lists the words retired for each concept."""

DOCS = ROOT / "docs"
"""Every page of the site, read as the `.mdx` a visitor is served."""

SKILLS = ROOT / "src" / "decktalk" / "skills"
"""The packaged skills a project keeps in `.agents/skills/`, read as the Markdown an agent loads."""

HISTORY = frozenset({DOCS / "changelog.mdx"})
"""The published changelog, which records the words a release used and is never rewritten."""

SEEDS = frozenset({"knob", "wire id", "soundscape", "paid take", "bought take", "certainty", "spending", "silent take"})
"""Words the glossary must keep retiring, so a column that stops parsing fails rather than passes empty."""


@dataclass(frozen=True)
class Retired:
    """One retired word, and the glossary word that replaced it."""

    word: str
    instead: str

    @property
    def pattern(self) -> re.Pattern[str]:
        """The word whole, in any case, with an optional plural, and with any run of space between its words."""
        spaced = r"\s+".join(re.escape(part) for part in self.word.split())
        return re.compile(rf"(?<![\w-]){spaced}(?:e?s)?(?![\w-])", re.IGNORECASE)


@dataclass(frozen=True)
class Text:
    """One body of visitor-facing text, named by where a reader meets it."""

    where: str
    body: str


def _cells(line: str) -> list[str]:
    """The cells of one Markdown table row, stripped, without the empty edges the outer pipes make."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _glossary_rows() -> Iterator[tuple[int, list[str], int]]:
    """Every data row of every glossary table that has a **Not** column, with the line and that column's index."""
    column: int | None = None
    for number, line in enumerate(GLOSSARY.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.startswith("|"):
            column = None
            continue
        cells = _cells(line)
        if column is None:
            column = cells.index(NOT_COLUMN) if NOT_COLUMN in cells else -1
            continue
        if column < 0 or set(line) <= set("|-: "):
            continue
        yield number, cells, column


def retired() -> list[Retired]:
    """Every word the glossary's **Not** column retires, with the word it retired it for."""
    out: list[Retired] = []
    for _, cells, column in _glossary_rows():
        out.extend(Retired(word.strip(), cells[0]) for word in cells[column].split(",") if word.strip())
    return out


def _glossary_without_its_not_cells() -> str:
    """The glossary as a visitor reads it, less the cells that name each retired word on purpose."""
    lines = GLOSSARY.read_text(encoding="utf-8").splitlines()
    for number, cells, column in _glossary_rows():
        lines[number - 1] = "| " + " | ".join(cells[:column] + cells[column + 1 :]) + " |"
    return "\n".join(lines)


def help_texts() -> Iterator[Text]:
    """The help of the root and of every command, as `--help` prints it at a width nothing wraps."""
    runner = CliRunner()
    for command in [None, *(row["command"] for row in catalog.walk())]:
        argv = [*(command.split() if command else []), "--help"]
        with runner.isolation(env={"COLUMNS": "200", "TTY_COMPATIBLE": "0"}) as (out, _err, _):
            main(argv)
            sys.stdout.flush()
            yield Text(f"decktalk {' '.join(argv)}", out.getvalue().decode())


def page_texts() -> Iterator[Text]:
    """Every docs page and every packaged skill file, with the glossary less its **Not** cells."""
    for path in sorted([*DOCS.rglob("*.mdx"), *SKILLS.rglob("*.md")]):
        if path in HISTORY:
            continue
        body = _glossary_without_its_not_cells() if path == GLOSSARY else path.read_text(encoding="utf-8")
        yield Text(path.relative_to(ROOT).as_posix(), body)


def offences(texts: list[Text], words: list[Retired]) -> list[str]:
    """Each retired word found, as `where:line: 'found', which the glossary calls 'instead'`."""
    found: list[str] = []
    for text in texts:
        for word in words:
            for match in word.pattern.finditer(text.body):
                line = text.body.count("\n", 0, match.start()) + 1
                said = " ".join(match.group().split())
                found.append(f"{text.where}:{line}: '{said}', which the glossary calls '{word.instead}'")
    return found


def test_the_glossary_retires_every_seed_word() -> None:
    """A **Not** column that stopped parsing would retire nothing, and every test below would pass empty."""
    words = {one.word.lower() for one in retired()}
    assert SEEDS <= words, f"the glossary no longer retires {sorted(SEEDS - words)}"


@pytest.mark.parametrize("word", ["knobs", "Knob", "wire\n  id", "Soundscapes"])
def test_a_retired_word_matches_whole_in_any_case_and_across_a_line(word: str) -> None:
    words = retired()
    assert offences([Text("probe", f"a {word} here")], words), f"{word!r} slipped through"


@pytest.mark.parametrize("word", ["knobbly", "soundscaped", "uncertainty", "prepaid takeaway"])
def test_a_longer_word_that_holds_a_retired_one_is_left_alone(word: str) -> None:
    assert offences([Text("probe", f"a {word} here")], retired()) == []


def test_no_help_text_uses_a_retired_word() -> None:
    found = offences(list(help_texts()), retired())
    assert not found, "Retired words in the help:\n" + "\n".join(found)


def test_no_docs_page_or_skill_uses_a_retired_word() -> None:
    found = offences(list(page_texts()), retired())
    assert not found, "Retired words in the docs and the skills:\n" + "\n".join(found)
