"""The projects and the skills packaged in the wheel, and writing one of them into a directory.

`decktalk init DIR` writes the starter: three sections, one equation, markup slides and a
placeholder build of about a minute. `--example NAME` writes a finished project instead. Either way
the packaged runtime and KaTeX land beside the pages, the skills land in `.agents/skills/`, and an
`AGENTS.md` is written when the directory has none.

An example with no project behind it yet is reserved here rather than left out, so the flag value
never changes meaning and the refusal says what it is waiting for.

A project is written once. A project that wants a newer runtime is written again.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from decktalk.errors import InputError
from decktalk.toolchain import assets

STARTER = "starter"
"""The packaged project `init` writes when no example is named, which is not itself an example."""

DECK_DIR = "deck"
"""The directory of a project that holds its pages, the runtime and KaTeX."""

AGENTS_FILE = "AGENTS.md"
"""The file an agent reads first, which is written only when the directory has none."""

SKILLS_DIR = Path(".agents") / "skills"
"""Where a project keeps its skills, relative to the project root."""

LINK_DIR = Path(".claude") / "skills"
"""The Claude Code folder, which is a link to the skills, or a copy of them on Windows."""

SKILL_NAMES = (
    "decktalk-script",
    "decktalk-slide",
    "decktalk-cues",
    "decktalk-build",
    "decktalk-fix",
    "decktalk-revise",
)
"""Every skill in the wheel, in the order an author meets them."""

NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""What a project may be called, which is what a file name and a film name both accept."""

RENAMED = {"gitignore": ".gitignore", "env.example": ".env.example"}
"""A packaged file whose name a working copy changes, because a dotfile in a wheel is skipped."""

FILLED_SUFFIXES = frozenset({".toml", ".md", ".json", ".html", ".css", ".js", ".txt", ".example", ""})
"""Suffixes of files that carry the two placeholders. Everything else is copied byte for byte."""

NAME_MARK = "__NAME__"
TITLE_MARK = "__TITLE__"


@dataclass(frozen=True)
class Example:
    """One `--example` value: where its project sits in the wheel, and what it shows."""

    name: str
    summary: str
    path: str | None
    """The directory under `template/`, or None while the example is only a reserved name."""

    @property
    def shipped(self) -> bool:
        return self.path is not None


EXAMPLES: tuple[Example, ...] = (
    Example(
        "lesson",
        "a lesson that teaches how a model learns, whose one long scene is written in markup, and "
        "which exercises every page attribute",
        "examples/lesson",
    ),
    Example(
        "product",
        "the project behind the product film, which is that film's deck and script without its voice",
        None,
    ),
    Example(
        "tutorial",
        "the six-section technical tutorial whose measured numbers the product film shows",
        None,
    ),
)
"""Every packaged example, including the names that are reserved and not yet written."""


def listed_names() -> str:
    """Every `--example` value in one line, with each reserved name marked as one."""
    return ", ".join(example.name if example.shipped else f"{example.name} (reserved)" for example in EXAMPLES)


def example(name: str) -> Example:
    """The example called `name`, or an `INPUT` refusal naming every example there is."""
    for found in EXAMPLES:
        if found.name == name:
            return found
    raise InputError(
        f"there is no example called {name!r}.",
        hint=f"The examples are {listed_names()}.",
    )


def title_from(name: str) -> str:
    """The project name as a title, which is what the starter's pages print."""
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(word[:1].upper() + word[1:] for word in words if word) or "Untitled"


def write_project(target: Path, *, name: str, example_name: str | None, skills: bool, force: bool) -> tuple[Path, ...]:
    """Write a packaged project into `target` and give back every file written, in the order written."""
    if not NAME_PATTERN.fullmatch(name):
        raise InputError(
            f"a project name must be letters, digits, dots, dashes or underscores, and {name!r} is not.",
            hint="Pass --name with a plainer name, or rename the directory.",
        )
    if target.exists() and any(target.iterdir()) and not force:
        raise InputError(
            f"{target.name} is not empty, so nothing was written.",
            hint="Pass --force to write into it anyway, or name an empty directory.",
        )
    source = _source(example_name)
    written = [_copy(found, target / _destination(found.relative_to(source)), name) for found in _files(source)]
    deck = target / DECK_DIR
    deck.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(assets.runtime_path(), deck / assets.RUNTIME_FILE)
    written.append(deck / assets.RUNTIME_FILE)
    written.append(assets.vendor_katex(deck))
    agents = target / AGENTS_FILE
    # An AGENTS.md the author already wrote is theirs, so it is never replaced.
    if not agents.exists():
        written.append(_copy(assets.package_file(f"template/{AGENTS_FILE}"), agents, name))
    if skills:
        written.extend(write_skills(target))
    return tuple(written)


def write_skills(root: Path) -> tuple[Path, ...]:
    """Write every packaged skill into the project's own skills directory, with the link beside it.

    A project keeps one folder of skills and the second harness's folder is a link to it, so a
    second harness needs no second copy. A skill folder that is already there is replaced, because
    the packaged skills are the only source there is.
    """
    target = root / SKILLS_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in (assets.package_file("skills") / name for name in SKILL_NAMES):
        destination = target / source.name
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination)
        written.append(destination)
    written.append(_link(root))
    return tuple(written)


def _link(root: Path) -> Path:
    """The second harness's folder pointing at the first, as a link, or as a copy where links are refused."""
    link = root / LINK_DIR
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.is_dir():
        shutil.rmtree(link)
    relative = Path(os.path.relpath(root / SKILLS_DIR, link.parent))
    try:
        link.symlink_to(relative, target_is_directory=True)
    except OSError:
        # Windows without developer mode, and any filesystem that has no links at all.
        shutil.copytree(root / SKILLS_DIR, link)
    return link


def _source(example_name: str | None) -> Path:
    """The packaged project directory to copy, refusing an example that is only a reserved name."""
    if example_name is None:
        return assets.package_file(f"template/{STARTER}")
    found = example(example_name)
    if not found.shipped:
        raise InputError(
            f"--example {found.name} is a reserved name and no project stands behind it yet: {found.summary}.",
            hint="Run `decktalk init` for the starter, or `decktalk init --example lesson` for a finished project.",
        )
    return assets.package_file(f"template/{found.path}")


def _files(source: Path) -> list[Path]:
    """Every file of a packaged project, in a stable order."""
    return [found for found in sorted(source.rglob("*")) if found.is_file() and "__pycache__" not in found.parts]


def _destination(relative: Path) -> Path:
    """The path a packaged file takes in a working copy, which renames the two dotfiles."""
    return relative.parent / RENAMED.get(relative.name, relative.name)


def _copy(source: Path, destination: Path, name: str) -> Path:
    """Copy one packaged file, filling the project name into the kinds of file that carry it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix in FILLED_SUFFIXES:
        text = source.read_text(encoding="utf-8")
        destination.write_text(text.replace(NAME_MARK, name).replace(TITLE_MARK, title_from(name)), encoding="utf-8")
    else:
        shutil.copyfile(source, destination)
    return destination
