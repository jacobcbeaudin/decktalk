"""The projects and the skills packaged in the wheel, and writing one of them into a directory.

`decktalk init DIR` writes the starter: three sections, one equation, markup slides and a
placeholder build of about a minute. `--example NAME` writes another packaged project instead, and
`--example starter` names the default. Either way the skills land in `.agents/skills/` and an
`AGENTS.md` is written when the directory has none. No copy of the runtime or of KaTeX is written,
because the origin serves both from the engine.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from decktalk.errors import InputError
from decktalk.toolchain import assets

log = logging.getLogger(__name__)

STARTER = "starter"
"""The packaged project `init` writes when no example is named."""

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
    path: str
    """The directory under `template/`."""


EXAMPLES: tuple[Example, ...] = (
    Example(
        STARTER,
        "three sections, one equation and markup slides, with a placeholder build of about a minute",
        STARTER,
    ),
    Example(
        "lesson",
        "a lesson that teaches how a model learns, whose one long scene is written in markup, and "
        "which exercises every page attribute",
        "examples/lesson",
    ),
    Example(
        "interview",
        "an interview between a narrator and a guest, whose second section is a dialogue read by two voices",
        "examples/interview",
    ),
    Example(
        "motion",
        "things that travel between two words: a firm arrival, a spring, a loop and a bar that fills",
        "examples/motion",
    ),
)
"""Every packaged project `init` writes, the starter first because it is the default."""


def names() -> tuple[str, ...]:
    """Every `--example` value, in the order `listed_names` gives them."""
    return tuple(found.name for found in EXAMPLES)


def listed_names() -> str:
    """Every `--example` value in one line, the starter first."""
    return ", ".join(names())


def example(name: str) -> Example:
    """The example called `name`, or an `INPUT` refusal naming every example there is."""
    for found in EXAMPLES:
        if found.name == name:
            return found
    raise InputError(
        f"There is no example called {name!r}.",
        hint=f"The examples are {listed_names()}.",
    )


def title_from(name: str) -> str:
    """The project name as a title, which is what the starter's pages print."""
    words = re.split(r"[-_\s]+", name.strip())
    return " ".join(word[:1].upper() + word[1:] for word in words if word) or "Untitled"


def write_project(target: Path, *, name: str, example_name: str | None, skills: bool, force: bool) -> tuple[Path, ...]:
    """Write a packaged project into `target` and give back every file written, in the order written.

    `example_name` None is the starter, as `init` with no `--example` is.
    """
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
    source = assets.package_file(f"template/{example(example_name or STARTER).path}")
    written = [_copy(found, target / _destination(found.relative_to(source)), name) for found in _files(source)]
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
    except OSError as refused:
        # Windows without developer mode, and any filesystem that has no links at all.
        log.debug("%s could not be a link, so it is a copy.", LINK_DIR, exc_info=refused)
        shutil.copytree(root / SKILLS_DIR, link)
    return link


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
